"""
LLM 调用包装：重试 + 退避 + 并发限速 + 断点续跑的追加式缓存。

为什么需要这一层：simulation/infra/llm.py 的客户端
  - 没有任何重试、退避、超时或限速；
  - 每次 run() 新建一个 OpenAI 客户端（所以并发调用是安全的），
    但 token 计数器没有加锁（所以并发下计数会竞争）。
本包装自己维护加锁的计数，并使用追加式 JSONL 缓存，
使得最贵的打分步骤幂等、可中断续跑。

另注：不要在这一层里给 LLM 传 Logger —— logger 的文件写入没有线程安全保证。
"""

import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from simulation.infra.llm import create_llm


class AllCallsFailed(RuntimeError):
    """某条样本重试耗尽仍失败。"""


class ScoringLLM:
    """带缓存与重试的并发打分客户端。"""

    def __init__(self, config, cache_path, parse_and_validate,
                 workers=8, max_retries=5, rpm=300, model_name=None):
        """
        Args:
            config: 完整 CONFIG dict（需要含 'LLM_config'）
            cache_path: 追加式 JSONL 缓存路径
            parse_and_validate: callable(raw_text) -> dict，解析失败要抛异常
            workers: 并发线程数
            max_retries: 单条最大重试次数
            rpm: 全局每分钟请求上限
            model_name: 覆盖 config 里的模型名
        """
        import copy
        config = copy.deepcopy(config)
        if model_name:
            config['LLM_config']['model_name'] = model_name

        self.model_name = config['LLM_config']['model_name']
        self.llm = create_llm(config, logger=None)
        self.cache_path = Path(cache_path)
        self.parse_and_validate = parse_and_validate
        self.workers = workers
        self.max_retries = max_retries

        self._lock = threading.Lock()
        self._tokens_in = 0
        self._tokens_out = 0
        self._n_calls = 0

        self._min_interval = 60.0 / rpm if rpm else 0.0
        self._last_call = 0.0

        self._cache = self._load_cache()
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 缓存
    # ------------------------------------------------------------------

    def _load_cache(self) -> dict:
        cache = {}
        models_seen = {}
        if self.cache_path.exists():
            with open(self.cache_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue          # 容忍上次崩溃留下的半行
                    cache[obj['comment_id']] = obj
                    m = obj.get('model', '(未记录)')
                    models_seen[m] = models_seen.get(m, 0) + 1

        # 防止把不同模型打的分混进同一份语料 —— 那会让验证曲线的口径不一致。
        others = {m: n for m, n in models_seen.items() if m != self.model_name}
        if others:
            detail = ', '.join(f'{m}×{n}' for m, n in others.items())
            raise ValueError(
                f'缓存 {self.cache_path} 里已有其他模型打的分（{detail}），'
                f'当前模型是 {self.model_name}。\n'
                f'混用不同模型的分数会让语料口径不一致。请二选一：\n'
                f'  1) 用回原模型；或\n'
                f'  2) 加 --reset-cache 清空缓存重新打（已打的分数会丢失）')
        return cache

    def _append_cache(self, rec: dict) -> None:
        with open(self.cache_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            f.flush()

    # ------------------------------------------------------------------
    # 限速
    # ------------------------------------------------------------------

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        while True:
            with self._lock:
                now = time.time()
                wait = self._min_interval - (now - self._last_call)
                if wait <= 0:
                    self._last_call = now
                    return
            time.sleep(min(wait, 0.25))

    # ------------------------------------------------------------------
    # 单条打分
    # ------------------------------------------------------------------

    def score_one(self, comment_id, system, user):
        """打分一条；命中缓存直接返回。"""
        hit = self._cache.get(comment_id)
        if hit is not None:
            return hit

        last_err = None
        for attempt in range(self.max_retries):
            try:
                self._throttle()
                resp = self.llm.run(
                    [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user}],
                    temperature=0.0,          # 分类任务必须是确定性温度
                )
                parsed = self.parse_and_validate(resp['result'])
                rec = {'comment_id': comment_id, 'model': self.model_name, **parsed}

                with self._lock:
                    self._tokens_in += resp.get('input_token', 0) or 0
                    self._tokens_out += resp.get('output_token', 0) or 0
                    self._n_calls += 1
                    self._cache[comment_id] = rec
                    self._append_cache(rec)
                return rec

            except Exception as e:                      # noqa: BLE001 —— 网络/解析都可能抛
                last_err = e
                if attempt < self.max_retries - 1:
                    delay = min(2 ** attempt + random.random(), 60.0)
                    time.sleep(delay)

        raise AllCallsFailed(f'{comment_id} 重试 {self.max_retries} 次仍失败: {last_err}')

    # ------------------------------------------------------------------
    # 批量
    # ------------------------------------------------------------------

    def score_many(self, items, system, user_fn, progress_every=100):
        """
        items: [(comment_id, payload), ...]
        user_fn: payload -> user message

        Returns: {comment_id: rec}，失败的不在结果里（由调用方决定怎么处理）。
        """
        todo = [(cid, p) for cid, p in items if cid not in self._cache]
        done = {cid: self._cache[cid] for cid, _ in items if cid in self._cache}
        if done:
            print(f'  缓存命中 {len(done)} 条，需新打分 {len(todo)} 条')

        failed = {}
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = {
                ex.submit(self.score_one, cid, system, user_fn(p)): cid
                for cid, p in todo
            }
            for i, fut in enumerate(as_completed(futures), 1):
                cid = futures[fut]
                try:
                    done[cid] = fut.result()
                except Exception as e:                  # noqa: BLE001
                    failed[cid] = str(e)
                if i % progress_every == 0 or i == len(futures):
                    print(f'  进度 {i}/{len(futures)}  '
                          f'(累计 tokens: in={self._tokens_in} out={self._tokens_out})')

        return done, failed

    # ------------------------------------------------------------------

    def usage(self) -> dict:
        return {
            'model': self.model_name,
            'new_calls': self._n_calls,
            'input_tokens': self._tokens_in,
            'output_tokens': self._tokens_out,
            'cached': len(self._cache),
        }

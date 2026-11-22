import csv
import pickle
import random
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np


# ============================================================
# 数据加载
# ============================================================

def _read_csv(path):
    """自动检测编码读取 CSV"""
    for enc in ['utf-8', 'gbk', 'gb2312', 'gb18030', 'latin-1']:
        try:
            return pd.read_csv(path, encoding=enc).to_dict('records')
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise UnicodeDecodeError(f"无法用任何编码读取: {path}")


def load_posts(config):
    """加载多帖事件时间线"""
    return _read_csv(config['data_path']['posts'])


def load_user(config):
    """加载用户画像，并合并 LLM 生成的三个初始心理状态（如果存在）。

    `events/<id>/processed/initial_states.csv` 由 personas/generate_initial_states.py 生成；
    合并后每行会多出 init_panic / init_trust / init_rumor 三个字段。
    如果文件不存在，Agent 会回退到旧的大五加性模型并给出警告。
    """
    users = _read_csv(config['data_path']['user'])

    init_path = config.get('data_path', {}).get('initial_states')
    if not init_path:
        return users

    init_path = Path(init_path)
    if not init_path.exists():
        return users

    initial_rows = _read_csv(init_path)
    by_uid = {}
    for r in initial_rows:
        try:
            by_uid[int(r['uid'])] = r
        except (KeyError, TypeError, ValueError):
            continue

    merged = 0
    for u in users:
        try:
            r = by_uid.get(int(u['uid']))
        except (KeyError, TypeError, ValueError):
            continue
        if not r:
            continue
        # uid 相同但姓名不同，说明 user.csv 换过人，不能把旧初值套上去。
        if r.get('name') and u.get('name') and str(r['name']) != str(u['name']):
            continue
        try:
            u['init_panic'] = float(r['init_panic'])
            u['init_trust'] = float(r['init_trust'])
            u['init_rumor'] = float(r['init_rumor'])
            merged += 1
        except (KeyError, TypeError, ValueError):
            continue

    if merged:
        # 不打印日志，避免在每次构造 Agent 时刷屏；调用方可以自己检查字段。
        pass
    return users


# ============================================================
# LLM 输出解析
# ============================================================

def parse_llm_reply(text):
    """解析 LLM 的回复输出: Comment_ID + Reply"""
    pattern = r"Comment_ID:\s*(\d+)\s*Reply:\s*([\s\S]*)"
    match = re.search(pattern, text)
    if not match:
        raise ValueError("LLM output format error")
    comment_id = int(match.group(1))
    reply = match.group(2).strip()
    return {"comment_id": comment_id, "reply": reply}


def extract_score(text, key):
    """从 LLM 输出中提取数值分数，处理各种畸形输出。"""
    if not text:
        return None

    # 预处理：修复常见的 LLM 输出错误
    cleaned = text
    # 去掉 key 后面的换行和空格
    cleaned = re.sub(rf'{key}:\s*[\n\r]+', f'{key}: ', cleaned)
    # 替换字母 o/O 为 0
    cleaned = re.sub(rf'{key}:\s*[oO]', f'{key}: 0', cleaned)
    # 修复双点号 .. → .
    cleaned = re.sub(rf'{key}:\s*(\d+)\.\.(\d*)', rf'{key}: \1.\2', cleaned)
    # 修复空格分隔的小数 e.g. "0 . 5" → "0.5"
    cleaned = re.sub(rf'{key}:\s*(\d+)\s*\.\s*(\d+)', rf'{key}: \1.\2', cleaned)

    # 匹配数字（可能包含小数点）
    pattern = rf'{key}:\s*([\d.]+)'
    match = re.search(pattern, cleaned)
    if not match:
        return None

    raw = match.group(1).strip()

    # 尝试解析
    try:
        val = float(raw)
    except ValueError:
        # 尝试修复多个小数点：取第一个有效的
        parts = [p for p in raw.split('.') if p]
        if parts:
            try:
                val = float('.'.join(parts[:2]))
            except ValueError:
                return None
        else:
            return None

    # 缩放：如果值明显是百分比(>=2)，可能是 0-100 或 0-10 刻度
    if val > 1.0:
        if val <= 10:
            val = val / 10.0   # 0-10 刻度 → 0-1
        elif val <= 100:
            val = val / 100.0  # 0-100 刻度 → 0-1
        else:
            val = 0.5          # 完全异常，给个中间值

    return max(0.0, min(1.0, val))


_RESPONSE_KEYS = ('LATEST_POSTS', 'OUTDATED_POSTS', 'PANIC_SCORE',
                  'TRUST_SCORE', 'RUMOR_SCORE', 'REASONING')


def extract_pid_list(text, key, valid_pids=None):
    """从 LLM 反思输出中提取 pid 列表。

    与 extract_score 不同：这里要的是列表，不能复用标量解析器。
    缺失、空值、脏数据一律返回 []，绝不抛异常 —— 反思解析失败只应
    让字段缺失，不应让整个 take_action 被 environment 捕获成空记录。

    Args:
        text: LLM 原始输出
        key: 'LATEST_POSTS' / 'OUTDATED_POSTS'
        valid_pids: 可选；只保留这些 pid，过滤模型编造的编号
    """
    try:
        if not isinstance(text, str) or not text.strip():
            return []

        pattern = re.compile(
            r'(?im)^[ \t>*#\-|]*' + re.escape(key)
            + r'[ \t*]*[:：][ \t]*(.*)$')
        match = pattern.search(text)
        if not match:
            return []

        seg = match.group(1).strip()

        # 同一行里可能紧跟下一个字段，先截断。
        key_re = re.compile(
            r'\s*(?:' + '|'.join(_RESPONSE_KEYS) + r')\s*[:：]',
            re.IGNORECASE)
        seg = key_re.split(seg, maxsplit=1)[0].strip()

        # 续行：直到下一个字段名为止。
        next_key = re.compile(
            r'^\s*[>*#\-|]*\s*(?:' + '|'.join(_RESPONSE_KEYS)
            + r')\s*[:：]', re.IGNORECASE)
        for line in text[match.end():].splitlines():
            if next_key.match(line):
                break
            if line.strip():
                seg += ' ' + line.strip()

        # 去掉行尾括号解释： "3, 4 (已被更新)" → "3, 4"。
        # 但如果整段就是 "(pid=3)"，保留原段，避免误删。
        stripped = re.sub(r'\s*[\(（][^\)）]*[\)）]\s*$', '', seg).strip()
        if stripped:
            seg = stripped

        if not seg:
            return []
        if re.match(r'^\s*(NONE|NULL|NIL|无|没有|\[\s*\]|\(\s*\))\s*$',
                    seg, re.IGNORECASE):
            return []

        out, seen = [], set()
        for tok in re.findall(r'\d+', seg):
            val = int(tok)
            if val not in seen:
                seen.add(val)
                out.append(val)

        if valid_pids is not None:
            valid = set()
            for v in valid_pids:
                try:
                    valid.add(int(v))
                except (TypeError, ValueError):
                    pass
            out = [v for v in out if v in valid]
        return out
    except Exception:
        return []


# ============================================================
# 内容审查（规则驱动，不用 LLM）
# ============================================================

def contains_panic_keywords(text, keywords):
    """检查文本是否包含恐慌关键词"""
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower:
            return True
    return False


def contains_negative_emotion(text, emotion_words):
    """检查文本是否包含负面情绪词"""
    text_lower = text.lower()
    count = 0
    for w in emotion_words:
        if w.lower() in text_lower:
            count += 1
    return count


def score_comment_negativity(comment_text, panic_keywords, negative_emotion_words):
    """
    对评论的负面程度打分（规则驱动）。
    分数越高越负面。
    """
    score = 0
    # 恐慌关键词命中
    if contains_panic_keywords(comment_text, panic_keywords):
        score += 3
    # 负面情绪词命中数
    score += contains_negative_emotion(comment_text, negative_emotion_words)
    return score


# ============================================================
# 活跃度判断
# ============================================================

def is_agent_active_today(activity_level, activity_prob):
    """根据活跃度等级，概率判断 agent 今天是否上线"""
    prob = activity_prob.get(activity_level, 0.7)
    return random.random() < prob


# ============================================================
# Checkpoint
# ============================================================

class Checkpoint:
    def __init__(self, config, start_time):
        self.config = config
        self.start_time = start_time
        self.checkpoint_time = None
        self.simulator = None

    def save_checkpoint(self, simulator):
        raise NotImplementedError("Checkpoint 保存功能尚未实现")

    def load_checkpoint(self):
        raise NotImplementedError("Checkpoint 加载功能尚未实现")


# ============================================================
# 命令行参数更新
# ============================================================

def update_config(cfg, extra_args):
    """
    通过命令行参数覆盖配置。
    Args:
        cfg: config dict
        extra_args: ['-port', '8000', '-llm_type', 'remote_openai', ...]
    """
    if len(extra_args) % 2 != 0:
        raise ValueError("Command argparse number is not correct!")
    for i in range(0, len(extra_args), 2):
        name, par = extra_args[i], extra_args[i + 1]

        if name == '-port':
            cfg['LLM_config']['port'] = int(par)
        if name == '-llm_type':
            cfg['LLM_config']['llm_type'] = par
        if name == '-api_key':
            cfg['LLM_config']['api_key'] = par
        if name == '-base_url':
            cfg['LLM_config']['base_url'] = par
        if name == '-model_name':
            cfg['LLM_config']['model_name'] = par
        if name == '-seed':
            cfg['simulation_config']['seed'] = int(par)
        if name == '-days':
            cfg['simulation_config']['total_days'] = int(par)
        if name == '-stimulus_mode':
            cfg.setdefault('stimulus_config', {})['mode'] = par
        if name == '-record_path':
            cfg['data_path']['record'] = par
        if name == '-load_checkpoint':
            cfg['data_path']['load_checkpoint'] = par
        if name == '-save_checkpoint':
            cfg['data_path']['save_checkpoint'] = par

    return cfg

"""
简易日志系统——实时输出 + 写文件。
"""

import sys
import os
from datetime import datetime

from simulation.infra.config import OUTPUT_DIR


def _safe(msg):
    """过滤无法被终端编码的字符"""
    try:
        msg.encode(sys.stdout.encoding or 'utf-8')
        return msg
    except (UnicodeEncodeError, UnicodeDecodeError):
        return msg.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(
            sys.stdout.encoding or 'utf-8', errors='replace')


class Logger:
    def __init__(self, log_path=None):
        if log_path is None:
            log_path = str(OUTPUT_DIR / 'simulation.log')
        self.log_path = log_path
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self.file = open(log_path, 'w', encoding='utf-8')
        self.agent_log = open(log_path.replace('.log', '_agents.log'), 'w', encoding='utf-8')
        self.llm_log = open(log_path.replace('.log', '_llm.log'), 'w', encoding='utf-8')
        self._start = datetime.now()
        self._llm_counter = 0

    def log(self, msg, level='INFO'):
        ts = datetime.now().strftime('%H:%M:%S')
        elapsed = (datetime.now() - self._start).total_seconds()
        line = f"[{ts} +{elapsed:6.1f}s] [{level:5s}] {msg}"
        print(_safe(line), flush=True)
        self.file.write(line + '\n')
        self.file.flush()

    def agent(self, day, aid, action_type, panic, trust, rumor):
        line = f"Day{day:2d} | Agent{aid:3d} | {action_type:10s} | panic={panic:.2f} trust={trust:.2f} rumor={rumor:.2f}"
        self.agent_log.write(line + '\n')
        self.agent_log.flush()

    def llm_query(self, prompt):
        self._llm_counter += 1
        n = self._llm_counter
        ts = datetime.now().strftime('%H:%M:%S')
        self.llm_log.write(f"\n{'='*60}\n")
        self.llm_log.write(f"[#{n}] [{ts}] QUERY ({len(prompt)} chars):\n")
        self.llm_log.write(f"{prompt}\n")
        self.llm_log.write(f"{'='*60}\n")
        self.llm_log.flush()
        short = _safe(prompt[:200].replace('\n', '\\n'))
        self.log(f'LLM#{n} query ({len(prompt)} chars): {short}...', 'LLM')

    def llm_response(self, result):
        n = self._llm_counter
        ts = datetime.now().strftime('%H:%M:%S')
        self.llm_log.write(f"[#{n}] [{ts}] RESPONSE ({len(result or '')} chars):\n")
        self.llm_log.write(f"{result}\n\n")
        self.llm_log.flush()
        short = _safe((result or '')[:200].replace('\n', '\\n'))
        self.log(f'LLM#{n} response: {short}...', 'LLM')

    def phase(self, msg):
        self.log('=' * 50)
        self.log(msg)
        self.log('=' * 50)

    def close(self):
        self.file.close()
        self.agent_log.close()
        self.llm_log.close()

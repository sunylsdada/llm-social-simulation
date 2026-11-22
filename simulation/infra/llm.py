import json
import time
import requests
from openai import OpenAI

# ---------------------------------------------------------------------------
# API 调用健壮性参数
# ---------------------------------------------------------------------------
# 原来客户端既没有 timeout 也没有重试，导致两种故障：
#   (1) 单个挂住的请求会无限期阻塞整个仿真（实测卡死 3 分钟以上）；
#   (2) 偶发的网络抖动会直接让那次调用失败。
# 单次调用的正常耗时是 2–10 秒，60 秒足够宽松。
API_TIMEOUT = 60.0
API_MAX_RETRIES = 2


class BaseLLM:
    """LLM 基类，定义统一接口"""
    def __init__(self, config, logger=None):
        self.global_config = config
        self.config = config["LLM_config"]
        self.logger = logger
        self.call_time = 0
        self.total_consumption = 0
        self.input_consumption = 0
        self.output_consumption = 0

    def print_total_consumption(self):
        print('The input consumption is %d tokens, output consumption is %s, '
              'cost is ~%.4f CNY.' % (self.input_consumption, self.output_consumption,
                                      self._estimate_cost()))

    def _estimate_cost(self):
        return 0.0

    def add_consumption(self, response):
        self.input_consumption += response['input_token']
        self.output_consumption += response['output_token']
        self.total_consumption += response['total_token']

    def run(self, message_list, temperature=1.0, penalty_score=0.0):
        raise NotImplementedError

    def fast_run(self, query, temperature=0.95, penalty_score=1.0):
        if self.logger:
            self.logger.llm_query(query)
        response = self.run([{"role": "user", "content": query}], temperature, penalty_score)
        result = response['result']
        if self.logger:
            self.logger.llm_response(result)
        return result


class Local_LLM(BaseLLM):
    """本地部署的 LLM（通过 vLLM / Ollama 等 OpenAI 兼容接口）"""
    def __init__(self, config, logger=None):
        super().__init__(config, logger=logger)
        self.port = self.config['port']

    def parse_response(self, response):
        return {
            'time_stamp': response.created,
            'result': response.choices[0].message.content,
            'input_token': 0,
            'output_token': 0,
            'total_token': 0
        }

    def run(self, message_list, temperature=1.0, penalty_score=0.0):
        client = OpenAI(
            api_key="none",
            base_url="http://localhost:%d/v1" % self.port,
            timeout=API_TIMEOUT,
            max_retries=API_MAX_RETRIES,
        )

        response = client.chat.completions.create(
            model=self.config['model_name'],
            messages=message_list,
        )

        response = self.parse_response(response)
        self.call_time += 1
        self.add_consumption(response)

        return response


class Remote_OpenAI_LLM(BaseLLM):
    """远程 OpenAI 兼容 API（OpenAI / DeepSeek / 通义千问 / 硅基流动等）"""
    def __init__(self, config, logger=None):
        super().__init__(config, logger=logger)
        self.api_key = self.config.get('api_key', '')
        self.base_url = self.config.get('base_url', 'https://api.openai.com/v1')
        self.model_name = self.config.get('model_name', 'gpt-4o')

    def parse_response(self, response):
        usage = response.usage
        return {
            'time_stamp': response.created,
            'result': response.choices[0].message.content,
            'input_token': usage.prompt_tokens if usage else 0,
            'output_token': usage.completion_tokens if usage else 0,
            'total_token': usage.total_tokens if usage else 0
        }

    def _estimate_cost(self):
        """粗略估算费用（以 OpenAI 官方价格为例，实际按具体服务商调整）"""
        # GPT-4o: input ~$2.5/1M tokens, output ~$10/1M tokens
        # 这里给个大概参考，按 7 CNY = 1 USD 折算
        input_cost = self.input_consumption / 1_000_000 * 2.5 * 7.2
        output_cost = self.output_consumption / 1_000_000 * 10 * 7.2
        return input_cost + output_cost

    def run(self, message_list, temperature=1.0, penalty_score=0.0):
        if not self.api_key:
            raise RuntimeError('No API key configured. Set LLM_API_KEY or OPENAI_API_KEY.')
        # 必须设 timeout：没有它时一个挂住的 HTTP 请求会**无限期阻塞整个仿真**
        # （实测出现过连续 3 分钟无响应、进度停在第 3 天不动的情况）。
        # max_retries 交给 SDK 处理连接错误与 429/5xx 的自动重试。
        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=API_TIMEOUT,
            max_retries=API_MAX_RETRIES,
        )

        response = client.chat.completions.create(
            model=self.model_name,
            messages=message_list,
            temperature=temperature,
        )

        response = self.parse_response(response)
        self.call_time += 1
        self.add_consumption(response)

        return response


class Remote_Claude_LLM(BaseLLM):
    """Anthropic Claude API"""
    def __init__(self, config, logger=None):
        super().__init__(config, logger=logger)
        self.api_key = self.config.get('api_key', '')
        self.base_url = self.config.get('base_url', 'https://api.anthropic.com')
        self.model_name = self.config.get('model_name', 'claude-sonnet-4-6')
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from anthropic import Anthropic
            self._client = Anthropic(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=API_TIMEOUT,
                max_retries=API_MAX_RETRIES,
            )
        return self._client

    def parse_response(self, response):
        return {
            'time_stamp': time.time(),
            'result': response.content[0].text,
            'input_token': response.usage.input_tokens,
            'output_token': response.usage.output_tokens,
            'total_token': response.usage.input_tokens + response.usage.output_tokens
        }

    def _estimate_cost(self):
        # Claude Sonnet 4: input $3/1M, output $15/1M
        input_cost = self.input_consumption / 1_000_000 * 3 * 7.2
        output_cost = self.output_consumption / 1_000_000 * 15 * 7.2
        return input_cost + output_cost

    def run(self, message_list, temperature=1.0, penalty_score=0.0):
        if not self.api_key:
            raise RuntimeError('No Anthropic API key configured. Set LLM_API_KEY.')
        # 将 OpenAI 格式的 messages 转为 Anthropic 格式
        system_prompt = None
        messages = []
        for msg in message_list:
            if msg['role'] == 'system':
                system_prompt = msg['content']
            else:
                messages.append({"role": msg['role'], "content": msg['content']})

        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 4096,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        response = self.client.messages.create(**kwargs)

        response = self.parse_response(response)
        self.call_time += 1
        self.add_consumption(response)

        return response


def create_llm(config, logger=None):
    """LLM 工厂函数：根据配置选择 LLM 类型"""
    llm_type = config.get('LLM_config', {}).get('llm_type', 'local')

    if llm_type == 'local':
        return Local_LLM(config, logger=logger)
    elif llm_type == 'remote_openai':
        return Remote_OpenAI_LLM(config, logger=logger)
    elif llm_type == 'remote_claude':
        return Remote_Claude_LLM(config, logger=logger)
    elif llm_type == 'fake':
        from simulation.infra.fake_llm import FakeLLM
        return FakeLLM(config, logger=logger)
    else:
        raise ValueError(f"Unknown LLM type: {llm_type}. "
                         f"Supported: 'local', 'remote_openai', 'remote_claude', 'fake'")

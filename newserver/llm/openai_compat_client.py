from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen



DEFAULT_BASE_URL = os.environ.get("LLM_BASE_URL", "https://www.packyapi.com/")
DEFAULT_API_PREFIX = os.environ.get("LLM_API_PREFIX", "/v1")
DEFAULT_API_KEY = os.environ.get("LLM_API_KEY", "sk-QItd82OOCDas3pjeUjVVGuoIIiNF87Gk2lPFJeCyS9CxdxN4")


class LLMRequestError(RuntimeError):
	"""
	LLM 请求失败（网络/HTTP/响应格式）统一异常。
	"""


def _join_url(base_url: str, path: str) -> str:
	b = str(base_url or "").rstrip("/")
	p = str(path or "").lstrip("/")
	return f"{b}/{p}"


@dataclass
class OpenAICompatClient:
	"""
	OpenAI-compatible API 客户端（不依赖 openai 官方库）。

	支持接口：
	- POST /chat/completions
	"""

	base_url: str = DEFAULT_BASE_URL
	api_prefix: str = DEFAULT_API_PREFIX
	api_key: str = DEFAULT_API_KEY
	timeout_seconds: int = 60
	max_retries: int = 2
	retry_backoff_seconds: float = 1.0
	user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
	extra_headers: dict[str, str] | None = None

	def chat_completions(
		self,
		messages: list[dict[str, Any]],
		model: str,
		temperature: float = 0.2,
		max_tokens: int | None = None,
		response_format: dict[str, Any] | None = None,
		extra: dict[str, Any] | None = None,
	) -> dict[str, Any]:
		"""
		返回完整 JSON 响应（方便你后续做日志/调试/解析）。

		messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
		response_format: OpenAI compatible 的 response_format（例如 {"type":"json_object"}），第三方是否支持取决于平台。
		"""

		if not isinstance(messages, list) or not messages:
			raise ValueError("messages must be a non-empty list")
		if not str(model or "").strip():
			raise ValueError("model is required")

		# 多数第三方 OpenAI-compatible API 的路径是：{base_url}{api_prefix}/chat/completions
		prefix = str(self.api_prefix or "").strip() or "/v1"
		if not prefix.startswith("/"):
			prefix = f"/{prefix}"
		url = _join_url(self.base_url, f"{prefix}/chat/completions")

		payload: dict[str, Any] = {
			"model": str(model),
			"messages": list(messages),
			"temperature": float(temperature),
		}
		if max_tokens is not None:
			payload["max_tokens"] = int(max_tokens)
		if isinstance(response_format, dict) and response_format:
			payload["response_format"] = dict(response_format)
		if isinstance(extra, dict) and extra:
			# 允许注入第三方字段（例如 top_p、presence_penalty、seed 等）
			payload.update(dict(extra))

		body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

		headers = {
			"Content-Type": "application/json",
			"Accept": "application/json",
			"User-Agent": str(self.user_agent or ""),
		}
		# 允许调用方补充/覆盖 header（某些第三方需要额外 header 才放行）
		if isinstance(self.extra_headers, dict) and self.extra_headers:
			for k, v in self.extra_headers.items():
				headers[str(k)] = str(v)
		if str(self.api_key or "").strip() and self.api_key != "REPLACE_ME":
			headers["Authorization"] = f"Bearer {self.api_key}"

		last_err: Exception | None = None
		for attempt in range(int(self.max_retries) + 1):
			try:
				req = Request(url=url, data=body, headers=headers, method="POST")
				with urlopen(req, timeout=int(self.timeout_seconds)) as resp:
					raw = resp.read().decode("utf-8", errors="replace")
					data = json.loads(raw)
					if not isinstance(data, dict):
						raise LLMRequestError("invalid response json: not an object")
					return data
			except HTTPError as e:
				last_err = e
				try:
					err_body = e.read().decode("utf-8", errors="replace")
				except Exception:
					err_body = ""
				msg = f"LLM HTTPError {getattr(e, 'code', '')}: {getattr(e, 'reason', '')} body={err_body}"
				# 4xx 通常不可重试；5xx/429 可以重试
				code = int(getattr(e, "code", 0) or 0)
				if code and 400 <= code < 500 and code not in [429]:
					raise LLMRequestError(msg) from e
				if attempt >= int(self.max_retries):
					raise LLMRequestError(msg) from e
			except (URLError, TimeoutError, json.JSONDecodeError) as e:
				last_err = e
				if attempt >= int(self.max_retries):
					raise LLMRequestError(f"LLM request failed: {e}") from e

			# retry backoff
			time.sleep(float(self.retry_backoff_seconds) * float(2**attempt))

		raise LLMRequestError(f"LLM request failed: {last_err}")

	def chat_text(
		self,
		messages: list[dict[str, Any]],
		model: str,
		temperature: float = 0.2,
		max_tokens: int | None = None,
		response_format: dict[str, Any] | None = None,
		extra: dict[str, Any] | None = None,
	) -> str:
		"""
		便捷方法：直接返回 choices[0].message.content 的文本。
		"""

		data = self.chat_completions(
			messages=messages,
			model=model,
			temperature=temperature,
			max_tokens=max_tokens,
			response_format=response_format,
			extra=extra,
		)

		choices = data.get("choices", [])
		if not isinstance(choices, list) or not choices:
			raise LLMRequestError("invalid response: missing choices")
		msg = (choices[0] or {}).get("message", {}) or {}
		content = (msg or {}).get("content", "")
		if content is None:
			content = ""
		return str(content)


def demo_call() -> None:
	"""
	最小调用示例（仅用于你本地调试/验证第三方 API 是否兼容）。

	用法：
	- 在 DEFAULT_BASE_URL/DEFAULT_API_KEY 填入你的第三方平台信息（或实例化时传入）
	- 运行一个简单对话请求，打印返回文本
	"""

	client = OpenAICompatClient()
	text = client.chat_text(
		messages=[
			{"role": "system", "content": "你是一个简短回答的助手。"},
			{"role": "user", "content": "用一句话解释什么是 tick-based simulation。"},
		],
		model="REPLACE_ME_MODEL",
		temperature=0.2,
	)
	print(text)


@dataclass
class DualModelLLM:
	"""
	两层 LLM 的简单封装：支持 planner/grounder 使用不同的模型名。

	说明：
	- model 名字完全由你自定义（取决于第三方平台支持的模型标识）。
	"""

	client: OpenAICompatClient
	planner_model: str
	grounder_model: str

	def planner_text(self, messages: list[dict[str, Any]], temperature: float = 0.4, max_tokens: int | None = None) -> str:
		return self.client.chat_text(
			messages=messages,
			model=str(self.planner_model),
			temperature=float(temperature),
			max_tokens=max_tokens,
		)

	def grounder_text(
		self,
		messages: list[dict[str, Any]],
		temperature: float = 0.2,
		max_tokens: int | None = None,
		response_format: dict[str, Any] | None = None,
	) -> str:
		return self.client.chat_text(
			messages=messages,
			model=str(self.grounder_model),
			temperature=float(temperature),
			max_tokens=max_tokens,
			response_format=response_format,
		)


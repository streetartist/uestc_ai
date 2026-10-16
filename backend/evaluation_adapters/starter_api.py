"""Contestant helper for the metered evaluation endpoint and development API."""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request


MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ModelResponseError(RuntimeError):
    def __init__(self, code, message, metadata):
        self.code = code
        self.metadata = metadata
        super().__init__(message)


def _usage_summary(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        count = value.get(name)
        if type(count) is int and count >= 0:
            result[name] = count
    for name in ("prompt_tokens_details", "completion_tokens_details"):
        details = value.get(name)
        if isinstance(details, dict):
            counts = {key: details[key] for key in
                      ("cached_tokens", "audio_tokens", "reasoning_tokens",
                       "accepted_prediction_tokens", "rejected_prediction_tokens")
                      if type(details.get(key)) is int and details[key] >= 0}
            if counts:
                result[name] = counts
    return result


def _request_id(headers):
    return (headers.get("X-Request-ID") or headers.get("Request-ID")) if headers is not None else None


class ModelClient:
    def __init__(self, model, max_output_tokens=1024, timeout=45):
        if type(max_output_tokens) is not int or max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("timeout must be a positive number of seconds")
        self.model, self.max_output_tokens = model, max_output_tokens
        self.timeout = timeout
        self.last_response = None

    def _fail(self, code, message):
        raise ModelResponseError(code, message, self.last_response)

    def chat(self, text, images=()):
        self.last_response = {"http_status": None, "request_id": None,
                              "finish_reason": None, "usage": {}}
        endpoint = os.environ.get("EVALUATION_API_URL")
        token = os.environ.get("EVALUATION_API_TOKEN")
        if endpoint and token:
            headers = {"X-Evaluation-API-Token": token}
        else:
            endpoint = os.environ.get("UESTC_MODEL_API_URL", "https://uestcai.top/api/ai/v1/chat/completions")
            token = os.environ.get("UESTC_MODEL_API_KEY")
            if not token:
                self._fail("missing_credentials", "请设置开发 API Key；测评期间凭据由平台自动注入。")
            headers = {"Authorization": "Bearer " + token}
        content = [{"type": "text", "text": text}]
        content += [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image}} for image in images]
        payload = {"model": self.model, "messages": [{"role": "user", "content": content}],
                   "max_tokens": self.max_output_tokens, "stream": False}
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(),
            headers={**headers, "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self.last_response.update(http_status=response.status,
                                          request_id=_request_id(response.headers))
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            self.last_response.update(http_status=error.code,
                                      request_id=_request_id(error.headers))
            error.close()
            self._fail("http_error", f"模型调用失败 HTTP {error.code}；检查额度、允许模型和参数。"
                       f"本次 max_output_tokens={self.max_output_tokens}，须在 API 平台本队本题额度的单次输出上限内。")
        except (urllib.error.URLError, OSError, TimeoutError):
            self._fail("transport_error", "模型请求连接失败或超时；核对网络与平台请求记录后再决定是否重试。")
        # A request can be billed even if its response is empty or interrupted.
        # Keep retry decisions in the agent harness.
        if len(raw) > MAX_RESPONSE_BYTES:
            self._fail("response_too_large", "模型响应超过 2 MiB，未解析正文。")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            self._fail("invalid_json", "模型响应不是有效 JSON；记录请求编号并联系组织方检查接口。")
        if not isinstance(result, dict):
            self._fail("invalid_response", "模型响应缺少 choices/message/content 结构。")
        self.last_response["usage"] = _usage_summary(result.get("usage"))
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            self._fail("invalid_response", "模型响应缺少 choices/message/content 结构。")
        choice = choices[0]
        reason = choice.get("finish_reason")
        self.last_response["finish_reason"] = reason if isinstance(reason, str) else None
        if reason == "length":
            self._fail("truncated_content", "模型输出达到长度上限，正文可能为空或不完整。"
                       f"本次 max_output_tokens={self.max_output_tokens}；缩短提示词、减少输出，"
                       "或在题目允许上限内调整预算。")
        message = choice.get("message")
        if not isinstance(message, dict):
            self._fail("invalid_response", "模型响应缺少 choices/message/content 结构。")
        content = message.get("content")
        if not isinstance(content, str):
            self._fail("empty_content" if content is None else "invalid_content",
                       "模型未返回文本正文；请检查 last_response 的结束原因和用量。")
        if not content.strip():
            self._fail("empty_content", "模型返回空正文；请检查 last_response 的结束原因和用量。"
                       f"本次 max_output_tokens={self.max_output_tokens}。")
        if reason not in (None, "stop"):
            self._fail("incomplete_content", "模型响应未正常完成文本输出；请检查 last_response 的结束原因。")
        return content

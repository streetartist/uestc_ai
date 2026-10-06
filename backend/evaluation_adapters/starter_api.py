"""Contestant helper for the metered evaluation endpoint and development API."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class ModelClient:
    def __init__(self, model, max_output_tokens=1024):
        self.model, self.max_output_tokens = model, max_output_tokens

    def chat(self, text, images=()):
        endpoint = os.environ.get("EVALUATION_API_URL")
        token = os.environ.get("EVALUATION_API_TOKEN")
        if endpoint and token:
            headers = {"X-Evaluation-API-Token": token}
        else:
            endpoint = os.environ.get("UESTC_MODEL_API_URL", "https://uestcai.top/api/ai/v1/chat/completions")
            token = os.environ.get("UESTC_MODEL_API_KEY")
            if not token:
                raise RuntimeError("请设置开发 API Key；测评期间凭据由平台自动注入。")
            headers = {"Authorization": "Bearer " + token}
        content = [{"type": "text", "text": text}]
        content += [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image}} for image in images]
        payload = {"model": self.model, "messages": [{"role": "user", "content": content}],
                   "max_tokens": self.max_output_tokens, "stream": False}
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(),
            headers={**headers, "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.loads(response.read(2 * 1024 * 1024))
        except urllib.error.HTTPError as error:
            # Never retry automatically: duplicate attempts consume the shared allowance.
            raise RuntimeError(f"模型调用失败 HTTP {error.code}；检查额度和平台状态。") from None
        return result["choices"][0]["message"]["content"]

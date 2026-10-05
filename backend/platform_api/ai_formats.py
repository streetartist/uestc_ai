"""OpenAI chat to Anthropic Messages conversion (text, images and tools)."""
from __future__ import annotations

import json
import time

from .ai_gateway import GatewayError, read_usage


def anthropic_request(body: dict) -> dict:
    allowed = {"model", "messages", "max_tokens", "max_completion_tokens", "stream", "stream_options",
               "temperature", "top_p", "stop", "tools", "tool_choice", "n"}
    if set(body) - allowed:
        raise GatewayError("此 Anthropic 渠道不支持当前请求中的额外参数。")
    result = {"model": body["model"], "max_tokens": body.get("max_completion_tokens", body.get("max_tokens", 1024)),
              "stream": body.get("stream", False), "messages": []}
    system = []
    for message in body["messages"]:
        if not isinstance(message, dict) or message.get("role") not in {"system", "developer", "user", "assistant", "tool"}:
            raise GatewayError("消息格式无效。")
        role, content = message["role"], message.get("content") or ""
        if role in {"system", "developer"}:
            if not isinstance(content, str):
                raise GatewayError("系统消息必须为文本。")
            system.append(content)
            continue
        if role == "tool":
            blocks = [{"type": "tool_result", "tool_use_id": message.get("tool_call_id"), "content": content}]
            role = "user"
        else:
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) and content else []
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "text":
                        blocks.append({"type": "text", "text": block["text"]})
                    elif block.get("type") == "image_url":
                        url = block["image_url"]["url"]
                        header, data = url.split(",", 1)
                        if not header.endswith(";base64"):
                            raise GatewayError("图片必须为 base64 data URL。")
                        blocks.append({"type": "image", "source": {"type": "base64", "media_type": header[5:-7], "data": data}})
                    else:
                        raise GatewayError("此渠道不支持该消息内容类型。")
            for tool in message.get("tool_calls", []):
                try:
                    arguments = json.loads(tool["function"]["arguments"])
                    if not isinstance(arguments, dict):
                        raise ValueError()
                    blocks.append({"type": "tool_use", "id": tool["id"], "name": tool["function"]["name"], "input": arguments})
                except (KeyError, TypeError, ValueError):
                    raise GatewayError("工具调用参数必须为 JSON 对象。") from None
        if not blocks:
            raise GatewayError("消息内容不能为空。")
        if result["messages"] and result["messages"][-1]["role"] == role:
            result["messages"][-1]["content"].extend(blocks)
        else:
            result["messages"].append({"role": role, "content": blocks})
    if system:
        result["system"] = "\n\n".join(system)
    for field in ("temperature", "top_p", "tools"):
        if field in body:
            result[field] = body[field]
    if "stop" in body:
        result["stop_sequences"] = [body["stop"]] if isinstance(body["stop"], str) else body["stop"]
    if "tools" in body:
        try:
            result["tools"] = [{"name": tool["function"]["name"],
                "description": tool["function"].get("description", ""),
                "input_schema": tool["function"].get("parameters", {"type": "object", "properties": {}})} for tool in body["tools"]]
        except (KeyError, TypeError):
            raise GatewayError("工具定义格式无效。") from None
    choice = body.get("tool_choice")
    if choice in ("auto", "required", "none"):
        result["tool_choice"] = {"type": {"auto": "auto", "required": "any", "none": "none"}[choice]}
    elif isinstance(choice, dict):
        result["tool_choice"] = {"type": "tool", "name": choice.get("function", {}).get("name")}
    return result


def finish_reason(reason):
    return {"max_tokens": "length", "tool_use": "tool_calls"}.get(reason, "stop")


def anthropic_response(body: dict, model: str) -> dict:
    content, tools = [], []
    for block in body["content"]:
        if block["type"] == "text":
            content.append(block["text"])
        elif block["type"] == "tool_use":
            tools.append({"id": block["id"], "type": "function", "function": {
                "name": block["name"], "arguments": json.dumps(block["input"], ensure_ascii=False)}})
    message = {"role": "assistant", "content": "".join(content) or None}
    if tools:
        message["tool_calls"] = tools
    result = {"id": body["id"], "object": "chat.completion", "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason(body.get("stop_reason"))}]}
    measured = read_usage(body)
    if measured:
        inp, out, cached = measured
        result["usage"] = {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out,
                           "prompt_tokens_details": {"cached_tokens": cached}}
    return result


class AnthropicStream:
    def __init__(self, model: str):
        self.model, self.id, self.usage = model, "", None
        self.tool_indexes = {}

    def chunk(self, delta: dict, reason=None):
        return {"id": self.id, "object": "chat.completion.chunk", "created": int(time.time()), "model": self.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}

    def feed(self, event: dict):
        kind = event.get("type")
        if kind == "message_start":
            self.id = event["message"]["id"]
            self.usage = read_usage(event["message"])
            yield self.chunk({"role": "assistant", "content": ""})
        elif kind == "content_block_start" and event["content_block"]["type"] == "tool_use":
            block = event["content_block"]
            index = len(self.tool_indexes)
            self.tool_indexes[event["index"]] = index
            yield self.chunk({"tool_calls": [{"index": index, "id": block["id"], "type": "function",
                "function": {"name": block["name"], "arguments": json.dumps(block["input"]) if block.get("input") else ""}}]})
        elif kind == "content_block_start" and event["content_block"]["type"] == "text" and event["content_block"].get("text"):
            yield self.chunk({"content": event["content_block"]["text"]})
        elif kind == "content_block_delta":
            delta = event["delta"]
            if delta["type"] == "text_delta":
                yield self.chunk({"content": delta["text"]})
            elif delta["type"] == "input_json_delta":
                yield self.chunk({"tool_calls": [{"index": self.tool_indexes[event["index"]],
                    "function": {"arguments": delta["partial_json"]}}]})
        elif kind == "message_delta":
            output = (event.get("usage") or {}).get("output_tokens")
            if self.usage and type(output) is int and output >= 0:
                self.usage = (self.usage[0], output, self.usage[2])
            yield self.chunk({}, finish_reason(event["delta"].get("stop_reason")))
        elif kind == "message_stop" and self.usage:
            inp, out, cached = self.usage
            yield {"id": self.id, "object": "chat.completion.chunk", "created": int(time.time()), "model": self.model,
                "choices": [], "usage": {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out,
                                          "prompt_tokens_details": {"cached_tokens": cached}}}

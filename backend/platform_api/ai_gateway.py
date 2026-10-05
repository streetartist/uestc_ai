"""Authenticated, metered proxy shared by participants and evaluation workers.

Quota is reserved atomically before dispatch. Unknown upstream usage retains the
reservation; disconnects and process crashes cannot give participants free calls.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app, jsonify, request, stream_with_context
from sqlalchemy import exists, func, or_, select, update

from .ai_models import AIChannel, AIGrant, AIKey, AIUsage
from .extensions import db
from .models import EvaluationRun, TeamMember, utcnow
from .security import hash_token


class GatewayError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "invalid_request"):
        super().__init__(message)
        self.status, self.code = status, code


def error_response(error: GatewayError):
    return jsonify({"error": {"message": str(error), "type": error.code, "code": error.code}}), error.status


def cipher() -> Fernet:
    secret = current_app.config.get("AI_GATEWAY_ENCRYPTION_KEY", "")
    if not secret:
        raise GatewayError("请先配置 AI_GATEWAY_ENCRYPTION_KEY，再保存上游密钥。", 503, "gateway_not_configured")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt_keys(keys: list[str]) -> str:
    return cipher().encrypt(json.dumps(keys).encode()).decode()


def decrypt_keys(value: str) -> list[str]:
    try:
        return json.loads(cipher().decrypt(value.encode()))
    except (InvalidToken, ValueError):
        raise GatewayError("上游密钥无法解密，请检查网关加密配置。", 503, "gateway_not_configured") from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def validate_url(url: str):
    parsed = urllib.parse.urlsplit(url)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (parsed.scheme != "https" and not (local and parsed.scheme == "http" and
            current_app.config.get("AI_GATEWAY_ALLOW_LOCAL_HTTP"))):
        raise GatewayError("上游地址必须使用 HTTPS；本地模型服务需显式开启本地 HTTP。")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise GatewayError("上游地址不能包含凭据、查询参数或片段。")


def upstream_request(channel: AIChannel, key: str, endpoint: str, payload=None, timeout=None):
    validate_url(channel.base_url)
    url = channel.base_url.rstrip("/") + "/" + endpoint
    if channel.protocol == "custom":
        url = channel.base_url
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if channel.protocol == "anthropic":
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, headers=headers,
        data=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None)
    return urllib.request.build_opener(NoRedirect).open(req, timeout=timeout or current_app.config["AI_GATEWAY_TIMEOUT_SECONDS"])


def key_auth() -> AIKey:
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else request.headers.get("x-api-key", "")
    key = AIKey.query.filter_by(key_hash=hash_token(token), enabled=True).first() if token else None
    if not key or not key.grant.enabled or not db.session.get(TeamMember, (key.grant.team_id, key.created_by)):
        raise GatewayError("API 密钥无效或已停用。", 401, "invalid_api_key")
    return key


def available_channels(grant: AIGrant, model: str | None = None, endpoint: str | None = None):
    channels = AIChannel.query.filter_by(enabled=True).all()
    if grant.allowed_channels:
        channels = [c for c in channels if c.id in grant.allowed_channels]
    if endpoint == "messages":
        channels = [c for c in channels if c.protocol == "anthropic"]
    elif endpoint == "responses":
        channels = [c for c in channels if c.protocol != "anthropic"]
    if model is not None:
        if model not in grant.allowed_models:
            raise GatewayError("此队伍没有该模型的使用权限。", 403, "model_not_allowed")
        channels = [c for c in channels if model in c.models and model not in c.disabled_models]
        if not channels:
            raise GatewayError("该模型暂无可用渠道。", 503, "model_unavailable")
    # Weighted permutation within each priority tier, without repeating channels.
    return sorted(channels, key=lambda c: (-c.priority, -math.log(max(random.random(), 1e-10)) / c.weight))


def model_catalog(grants: list[AIGrant]):
    items: dict[str, dict] = {}
    for grant in grants:
        for channel in available_channels(grant):
            for alias in channel.models:
                if alias not in grant.allowed_models or alias in channel.disabled_models:
                    continue
                item = items.setdefault(alias, {"id": alias, "object": "model", "owned_by": "competition",
                    "protocols": [], "input_price": channel.input_price, "output_price": channel.output_price})
                protocols = ["chat/completions", "messages"] if channel.protocol == "anthropic" else ["chat/completions", "responses"]
                item["protocols"] = sorted(set(item["protocols"] + protocols))
    return sorted(items.values(), key=lambda item: item["id"])


def price(input_tokens: int, output_tokens: int, channel: AIChannel) -> int:
    # Prices are admin-supplied points per million tokens, stored as micropoints.
    return int((Decimal(input_tokens) * Decimal(str(channel.input_price)) +
                Decimal(output_tokens) * Decimal(str(channel.output_price))).to_integral_value(rounding=ROUND_CEILING))


def read_usage(payload: dict) -> tuple[int, int, int] | None:
    raw = payload.get("usage")
    if not isinstance(raw, dict):
        return None
    inp = raw.get("prompt_tokens", raw.get("input_tokens"))
    out = raw.get("completion_tokens", raw.get("output_tokens"))
    if type(inp) is not int or type(out) is not int or min(inp, out) < 0:
        return None
    details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}
    if not isinstance(details, dict):
        return None
    cached = details.get("cached_tokens", 0)
    cache_creation = raw.get("cache_creation_input_tokens", 0)
    cache_read = raw.get("cache_read_input_tokens", 0)
    if any(type(n) is not int or n < 0 for n in (cached, cache_creation, cache_read)):
        return None
    inp += cache_creation + cache_read
    cached += cache_read
    return inp, out, min(inp, cached)


def validate_body(body: object, grant: AIGrant, endpoint: str):
    if not isinstance(body, dict) or not isinstance(body.get("model"), str) or len(body["model"]) > 160:
        raise GatewayError("请求必须包含 model。")
    if "stream" in body and type(body["stream"]) is not bool:
        raise GatewayError("stream 必须为布尔值。")
    if type(body.get("n", 1)) is not int or body.get("n", 1) != 1 or body.get("best_of", 1) != 1 or body.get("background") or body.get("previous_response_id") or body.get("conversation"):
        raise GatewayError("配额模式仅支持单次、前台、完整上下文请求。")
    output_fields = {"max_tokens", "max_completion_tokens", "max_output_tokens"} & set(body)
    allowed_fields = {"max_output_tokens"} if endpoint == "responses" else {"max_tokens"} if endpoint == "messages" else {"max_tokens", "max_completion_tokens"}
    if output_fields - allowed_fields or len(output_fields) > 1:
        raise GatewayError("请仅使用当前接口对应的一种输出 token 上限参数。")
    if endpoint == "responses":
        if "input" not in body:
            raise GatewayError("Responses 请求必须包含 input。")
        field = "max_output_tokens"
    else:
        if not isinstance(body.get("messages"), list) or not body["messages"] or len(body["messages"]) > 1000:
            raise GatewayError("messages 必须是非空数组，且不超过 1000 条。")
        if any(not isinstance(m, dict) or m.get("role") not in {"system", "developer", "user", "assistant", "tool"} for m in body["messages"]):
            raise GatewayError("消息格式无效。")
        field = "max_completion_tokens" if "max_completion_tokens" in body else "max_tokens"
    output = body.get(field, min(1024, grant.max_output_tokens))
    if type(output) is not int or not 1 <= output <= grant.max_output_tokens:
        raise GatewayError(f"单次输出 token 必须在 1 到 {grant.max_output_tokens} 之间。")
    body = dict(body)
    body[field] = output
    if endpoint == "chat/completions" and body.get("stream"):
        body["stream_options"] = {"include_usage": True}
    # References to remote state/files or hosted tools defeat input reservation.
    image_count = 0
    def check(value):
        nonlocal image_count
        if isinstance(value, dict):
            if value.get("type") in {"file", "input_file", "file_search", "web_search", "web_search_preview", "computer_use_preview", "code_interpreter"}:
                raise GatewayError("请提交完整上下文；配额模式不支持托管文件或托管工具。")
            if value.get("type") in {"image_url", "input_image"}:
                image_count += 1
                image = value.get("image_url", "")
                url = image.get("url", "") if isinstance(image, dict) else image
                if not isinstance(url, str) or not url.startswith("data:image/"):
                    raise GatewayError("图片请使用 data:image 内联格式，以便预留 token 配额。")
            if value.get("type") == "image" and (not isinstance(value.get("source"), dict) or value["source"].get("type") != "base64"):
                raise GatewayError("图片请使用 base64 内联格式。")
            if value.get("type") == "image":
                image_count += 1
            for nested in value.values():
                check(nested)
        elif isinstance(value, list):
            for nested in value:
                check(nested)
    check(body)
    encoded = json.dumps(body, ensure_ascii=False).encode()
    if len(encoded) > current_app.config["AI_GATEWAY_MAX_REQUEST_BYTES"]:
        raise GatewayError("请求体过大。", 413, "request_too_large")
    # Byte-bound reserve for text, inline media and tool schema plus chat framing.
    input_reserve = len(encoded) + 2048 + 32 * len(body.get("messages", [])) + 32768 * image_count
    return body, input_reserve, output


def reserve(grant: AIGrant, key: AIKey | None, run: EvaluationRun | None,
            model: str, endpoint: str, tokens: int, cost: int) -> AIUsage:
    # PostgreSQL READ COMMITTED: acquire the grant lock in a separate statement
    # so the following counts see reservations committed by previous contenders.
    # SQLite serializes the conditional UPDATE itself.
    db.session.execute(select(AIGrant.id).where(AIGrant.id == grant.id).with_for_update())
    now = utcnow()
    per_minute = select(func.count(AIUsage.id)).where(AIUsage.grant_id == grant.id, AIUsage.created_at >= now - timedelta(minutes=1)).scalar_subquery()
    active = select(func.count(AIUsage.id)).where(AIUsage.grant_id == grant.id, AIUsage.status == "pending", AIUsage.deadline > now).scalar_subquery()
    conditions = [AIGrant.id == grant.id, AIGrant.enabled.is_(True),
        AIGrant.calls_used < AIGrant.max_calls, AIGrant.tokens_used + tokens <= AIGrant.max_tokens,
        or_(AIGrant.max_cost_micros.is_(None), AIGrant.cost_used_micros + cost <= AIGrant.max_cost_micros),
        per_minute < AIGrant.requests_per_minute, active < AIGrant.max_concurrent]
    if key:
        conditions += [exists().where(AIKey.id == key.id, AIKey.enabled.is_(True)),
                       exists().where(TeamMember.team_id == grant.team_id, TeamMember.user_id == key.created_by)]
    if run:
        policy = run.config_snapshot["api"]
        updated = db.session.execute(update(EvaluationRun).where(EvaluationRun.id == run.id,
            EvaluationRun.status == "running", EvaluationRun.api_token_hash == run.api_token_hash,
            EvaluationRun.lease_expires_at > now, EvaluationRun.api_calls_used < policy["max_calls"]
        ).values(api_calls_used=EvaluationRun.api_calls_used + 1).execution_options(synchronize_session=False))
        if not updated.rowcount:
            db.session.rollback()
            raise GatewayError("评测 API 已停用或调用次数已用完。", 429, "evaluation_limit")
    updated = db.session.execute(update(AIGrant).where(*conditions).values(
        calls_used=AIGrant.calls_used + 1, tokens_used=AIGrant.tokens_used + tokens,
        cost_used_micros=AIGrant.cost_used_micros + cost))
    if not updated.rowcount:
        db.session.rollback()
        raise GatewayError("配额不足、密钥已停用或达到频率／并发上限。请缩小输出 token 或联系组织方。", 429, "quota_exceeded")
    usage = AIUsage(grant_id=grant.id, key_id=key.id if key else None,
        evaluation_run_id=run.id if run else None, model=model, endpoint=endpoint,
        charged_tokens=tokens, charged_cost_micros=cost,
        deadline=now + timedelta(seconds=current_app.config["AI_GATEWAY_TIMEOUT_SECONDS"] + 10))
    db.session.add(usage)
    db.session.commit()
    return usage


def settle(usage_id: str, channel_id: str | None, status: str, measured, elapsed: int,
           first_token: int | None, attempts: list, code: str | None = None, rates: tuple[float, float] | None = None):
    record = db.session.get(AIUsage, usage_id)
    if record.status != "pending":
        return
    tokens, cost = record.charged_tokens, record.charged_cost_micros
    if measured is not None:
        inp, out, cached = measured
        tokens = inp + out
        if rates:
            cost = int((Decimal(inp) * Decimal(str(rates[0])) + Decimal(out) * Decimal(str(rates[1]))).to_integral_value(rounding=ROUND_CEILING))
        else:
            channel = db.session.get(AIChannel, channel_id) if channel_id else None
            cost = price(inp, out, channel) if channel else 0
        record.input_tokens, record.output_tokens, record.cached_tokens = inp, out, cached
    # Single transaction publishes both the refund and immutable usage outcome.
    db.session.execute(update(AIGrant).where(AIGrant.id == record.grant_id).values(
        tokens_used=AIGrant.tokens_used + tokens - record.charged_tokens,
        cost_used_micros=AIGrant.cost_used_micros + cost - record.charged_cost_micros))
    record.charged_tokens, record.charged_cost_micros = tokens, cost
    record.channel_id, record.status = channel_id, status
    record.latency_ms, record.first_token_ms = elapsed, first_token
    record.attempts, record.error_code = attempts, code
    grant = db.session.get(AIGrant, record.grant_id)
    db.session.refresh(grant)
    if grant.tokens_used > grant.max_tokens or (grant.max_cost_micros is not None and grant.cost_used_micros > grant.max_cost_micros):
        grant.enabled = False
        record.error_code = "upstream_usage_exceeded_reservation"
    db.session.commit()


def proxy(grant: AIGrant, body, endpoint: str = "chat/completions", key: AIKey | None = None, run: EvaluationRun | None = None):
    from .ai_formats import anthropic_request, anthropic_response, AnthropicStream
    body, inp, out = validate_body(body, grant, endpoint)
    model = body["model"]
    channels = available_channels(grant, model, endpoint)
    # Build all wire formats before reserving, so unsupported conversions are free.
    candidates = []
    conversion_error = None
    for channel in channels:
        payload = {**body, "model": channel.models[model]}
        upstream_endpoint = endpoint
        convert = channel.protocol == "anthropic" and endpoint == "chat/completions"
        if convert:
            try:
                payload, upstream_endpoint = anthropic_request(payload), "messages"
            except GatewayError as error:
                conversion_error = error
                continue
        keys = decrypt_keys(channel.secrets)
        random.shuffle(keys)
        for secret in keys:
            candidates.append((channel, secret, payload, upstream_endpoint, convert, (channel.input_price, channel.output_price)))
    if not candidates:
        raise conversion_error or GatewayError("该模型暂无兼容当前请求的渠道。", 503, "model_unavailable")
    reservation_cost = max(price(inp, out, candidate[0]) for candidate in candidates)
    usage = reserve(grant, key, run, model, endpoint, inp + out, reservation_cost)
    usage_id, started, attempts = usage.id, time.monotonic(), []
    response = channel = None
    convert = False
    deadline = started + current_app.config["AI_GATEWAY_TIMEOUT_SECONDS"]
    try:
        for candidate, secret, payload, upstream_endpoint, convert, rates in candidates[:8]:
            channel = candidate
            try:
                response = upstream_request(channel, secret, upstream_endpoint, payload, max(.1, deadline - time.monotonic()))
                attempts.append({"channel_id": channel.id, "status": response.status})
                break
            except urllib.error.HTTPError as error:
                attempts.append({"channel_id": channel.id, "status": error.code})
                error.close()
                # Only explicit rejection is safe to retry without double spending.
                if error.code not in {401, 403, 404, 429}:
                    raise GatewayError("上游服务返回错误，请查看调用记录。", 502, "upstream_error") from None
                if time.monotonic() >= deadline:
                    break
        if response is None:
            settle(usage_id, channel.id if channel else None, "failed", (0, 0, 0),
                   int((time.monotonic() - started) * 1000), None, attempts, "upstream_rejected")
            raise GatewayError("所有渠道均拒绝请求，请稍后重试。", 503, "upstream_rejected")
    except (urllib.error.URLError, TimeoutError, OSError, GatewayError) as error:
        settle(usage_id, channel.id if channel else None, "uncertain", None,
               int((time.monotonic() - started) * 1000), None, attempts, "upstream_unavailable")
        if isinstance(error, GatewayError):
            raise
        raise GatewayError("上游服务暂不可用，请稍后重试。", 502, "upstream_unavailable") from None

    def read_chunk():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        # urllib's per-read timeout alone lets a slow stream run indefinitely.
        try:
            response.fp.raw._sock.settimeout(remaining)
        except AttributeError:
            pass
        return response.read1(65536)

    if not body.get("stream"):
        try:
            chunks, size = [], 0
            while chunk := read_chunk():
                size += len(chunk)
                if size > current_app.config["AI_GATEWAY_MAX_RESPONSE_BYTES"]:
                    raise ValueError("response too large")
                chunks.append(chunk)
            result = json.loads(b"".join(chunks))
            if not isinstance(result, dict) or "error" in result:
                raise ValueError("invalid response")
            measured = read_usage(result)
            if convert:
                result = anthropic_response(result, model)
            result["model"] = model
            settle(usage_id, channel.id, "completed" if measured else "uncertain", measured,
                int((time.monotonic() - started) * 1000), None, attempts,
                None if measured else "usage_missing", rates)
            result_response = jsonify(result)
            result_response.headers["X-Request-ID"] = usage_id
            return result_response
        except (OSError, ValueError, TypeError, KeyError):
            settle(usage_id, channel.id, "uncertain", None,
                int((time.monotonic() - started) * 1000), None, attempts, "invalid_upstream_response")
            raise GatewayError("上游响应不完整或超时，已保留预占额度。", 502, "invalid_upstream_response") from None
        finally:
            response.close()

    @stream_with_context
    def generate():
        buffer, size, measured, first, finished, code = b"", 0, None, None, False, "stream_interrupted"
        translator = AnthropicStream(model) if convert else None
        try:
            while chunk := read_chunk():
                size += len(chunk)
                if size > current_app.config["AI_GATEWAY_MAX_RESPONSE_BYTES"]:
                    raise ValueError("stream too large")
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    stripped = line.strip()
                    if stripped.startswith(b"data:"):
                        data = stripped[5:].strip()
                        if data == b"[DONE]":
                            finished = True
                        elif data:
                            event = json.loads(data)
                            if not isinstance(event, dict):
                                raise ValueError("invalid event")
                            if first is None:
                                first = int((time.monotonic() - started) * 1000)
                            if event.get("type") in {"error", "response.failed", "response.incomplete"} or event.get("error"):
                                raise ValueError("upstream stream failed")
                            if translator:
                                for translated in translator.feed(event):
                                    yield ("data: " + json.dumps(translated, ensure_ascii=False) + "\n\n").encode()
                                measured = translator.usage
                            elif channel.protocol == "anthropic":
                                # Native Anthropic output counts arrive across two events.
                                if event.get("type") == "message_start":
                                    measured = read_usage(event.get("message", {}))
                                elif event.get("type") == "message_delta" and measured:
                                    output = (event.get("usage") or {}).get("output_tokens")
                                    if type(output) is int and output >= 0:
                                        measured = (measured[0], output, measured[2])
                            else:
                                measured = read_usage(event.get("response", event)) or measured
                            if event.get("type") in {"message_stop", "response.completed"}:
                                finished = True
                    if not translator:
                        yield line + b"\n"
            if buffer.strip():
                raise ValueError("unterminated stream")
            if not finished:
                raise ValueError("missing stream terminator")
            if translator:
                yield b"data: [DONE]\n\n"
            code = None if measured else "usage_missing"
        except (OSError, ValueError, TypeError, KeyError, GatewayError):
            code = "stream_interrupted"
            yield b'data: {"error":{"message":"Upstream stream interrupted","code":"stream_interrupted"}}\n\n'
        finally:
            response.close()
            # Do not refund partial usage on disconnect: generation may continue.
            trusted = measured if finished else None
            settle(usage_id, channel.id, "completed" if trusted and code is None else "uncertain",
                trusted, int((time.monotonic() - started) * 1000), first, attempts, code, rates)
    stream_response = current_app.response_class(generate(), content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Request-ID": usage_id})
    app = current_app._get_current_object()
    selected_channel_id = channel.id
    def close_response():
        response.close()
        with app.app_context():
            settle(usage_id, selected_channel_id, "uncertain", None,
                int((time.monotonic() - started) * 1000), None, attempts, "client_disconnected", rates)
    # Also covers a response closed before the generator ever yields a chunk.
    stream_response.call_on_close(close_response)
    return stream_response

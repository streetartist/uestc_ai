"""Official Pro API only. No shell execution or participant-supplied URLs."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app

from .ai_gateway import GatewayError, NoRedirect, decrypt_keys


def validate_base(url):
    parsed = urllib.parse.urlsplit(url)
    if url.rstrip("/") == "https://api.autodl.com":
        return
    if (current_app.config.get("COMPUTE_ALLOW_LOCAL_HTTP") and parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.path in {"", "/"}
            and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment):
        return
    raise GatewayError("AutoDL 地址必须为官方 https://api.autodl.com。")


class AutoDL:
    def __init__(self, provider):
        validate_base(provider.base_url)
        self.base = provider.base_url.rstrip("/")
        self.token = decrypt_keys(provider.secret)[0]

    def request(self, action, body=None, method="POST"):
        url = self.base + "/api/v1/dev/instance/pro/" + action
        # Real Pro status/snapshot endpoints bind GET query parameters, despite
        # the documentation presenting the instance ID as a request Body.
        if method == "GET":
            url += "?" + urllib.parse.urlencode(body or {})
        req = urllib.request.Request(url,
            data=None if method == "GET" else json.dumps(body or {}).encode(), method=method,
            headers={"Authorization": self.token, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=15) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError()
                result = json.loads(raw)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise GatewayError("AutoDL 暂时无法连接，操作结果待确认。", 502, "provider_unavailable") from None
        if isinstance(result, dict) and result.get("code") == "RecordNotFoundError" and action == "status":
            raise GatewayError("AutoDL 中已找不到已登记实例，请组织方重新配置。", 502, "provider_instance_missing")
        if not isinstance(result, dict) or result.get("code") != "Success":
            raise GatewayError("AutoDL 未确认操作成功，请检查渠道凭据、余额和库存。", 502, "provider_rejected")
        return result.get("data")

    def create(self, provider, name, seconds):
        body = {"req_gpu_amount": provider.gpu_count, "expand_system_disk_by_gb": 0,
                "gpu_spec_uuid": provider.gpu_spec_uuid, "image_uuid": provider.image_uuid,
                "cuda_v_from": provider.cuda_v_from, "instance_name": name,
                "start_command": self.shutdown_command(seconds)}
        if provider.data_centers:
            body["data_center_list"] = provider.data_centers
        remote_id = self.request("create", body)
        if not isinstance(remote_id, str) or not re.fullmatch(r"pro-[A-Za-z0-9_-]{1,150}", remote_id):
            raise GatewayError("AutoDL 返回的实例编号无效，创建结果待确认。", 502, "provider_invalid_result")
        return remote_id

    @staticmethod
    def shutdown_command(seconds):
        # Provider startup command is a secondary safeguard; worker shutdown is
        # authoritative. Root SSH users can alter commands inside their instance.
        return f"(sleep {int(seconds)}; /usr/bin/shutdown) >/tmp/contest-auto-stop.log 2>&1 & sleep 1"

    def power_on(self, remote_id, seconds):
        return self.request("power_on", {"instance_uuid": remote_id, "payload": "gpu",
                                        "start_command": self.shutdown_command(seconds)})

    def power_off(self, remote_id):
        return self.request("power_off", {"instance_uuid": remote_id})

    def status(self, remote_id):
        result = self.request("status", {"instance_uuid": remote_id}, "GET")
        if not isinstance(result, str) or len(result) > 32:
            raise GatewayError("AutoDL 返回的状态无效。", 502, "provider_invalid_result")
        return result

    def snapshot(self, remote_id):
        result = self.request("snapshot", {"instance_uuid": remote_id}, "GET")
        if not isinstance(result, dict):
            raise GatewayError("AutoDL 返回的详情无效。", 502, "provider_invalid_result")
        return result

    def list_page(self, page=1, page_size=100, action="list"):
        result = self.request(action, {"page_index": page, "page_size": page_size})
        if isinstance(result, dict) and result.get("list") is None and result.get("result_total") == 0:
            # Verified against the real Pro API: an empty private image list
            # is serialized as null rather than the documented array.
            result = {**result, "list": []}
        if not isinstance(result, dict) or not isinstance(result.get("list"), list):
            raise GatewayError("AutoDL 返回的列表无效。", 502, "provider_invalid_result")
        return result

    def find_created(self, name):
        # Do not replay an uncertain create: reconcile using its stable name.
        for page in range(1, 6):
            result = self.list_page(page=page)
            matches = [row for row in result["list"] if isinstance(row, dict) and row.get("name") == name]
            if len(matches) > 1:
                raise GatewayError("AutoDL 中出现重复的实例名称，需管理员核对。", 409, "ambiguous_instance")
            if matches:
                remote_id = matches[0].get("uuid")
                if isinstance(remote_id, str) and re.fullmatch(r"pro-[A-Za-z0-9_-]{1,150}", remote_id):
                    return remote_id
                raise GatewayError("AutoDL 实例编号无效。", 502, "provider_invalid_result")
            maximum = result.get("max_page", 1)
            if type(maximum) is not int:
                raise GatewayError("AutoDL 分页数据无效。", 502, "provider_invalid_result")
            if page >= maximum:
                return None
        raise GatewayError("AutoDL 实例列表过大，需要管理员核对创建结果。", 409, "reconciliation_required")

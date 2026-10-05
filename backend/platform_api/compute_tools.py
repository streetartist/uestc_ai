"""Sanitized AutoDL Pro tools; access tokens are never persisted or listed."""
from datetime import datetime, timezone
from math import isfinite
import re
from urllib.parse import urlencode, urlsplit


VENDOR_DOMAINS = ("autodl.com", "seetacloud.com", "gpuhub.com")


def authority(value):
    if not isinstance(value, str) or not value or len(value) > 260:
        return None
    try:
        parsed = urlsplit("https://" + value)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        return None
    if (not host or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
            or not any(host.endswith("." + suffix) for suffix in VENDOR_DOMAINS)
            or not re.fullmatch(r"[A-Za-z0-9.-]+", host)
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split("."))
            or (port is not None and not 1 <= port <= 65535)):
        return None
    return host + (f":{port}" if port is not None else "")


def address(snapshot, domain_key, port_key):
    port = snapshot.get(port_key)
    if port is not None and type(port) is not int:
        return None, None
    if port is None or port == 0:
        domain = authority(snapshot.get(domain_key))
        return ("https", domain) if domain else (None, None)
    host = authority(snapshot.get("proxy_host"))
    if type(port) is int and 1 <= port <= 65535 and host and ":" not in host:
        return "http", f"{host}:{port}"
    return None, None


def number(value, maximum=None, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or value < 0:
        return None
    if (maximum is not None and value > maximum) or not isfinite(value):
        return None
    return value


def snapshot_tools(snapshot):
    scheme, host = address(snapshot, "jupyter_domain", "jupyter_port")
    token = snapshot.get("jupyter_token")
    if not isinstance(token, str) or not token or len(token) > 4096 or any(ord(c) < 32 for c in token):
        token = None
    base = f"{scheme}://{host}" if scheme and host and token else None
    query = urlencode({"token": token}) if token else ""
    services = []
    for port in (6006, 6008):
        protocol = snapshot.get(f"service_{port}_port_protocol")
        scheme, host = address(snapshot, f"service_{port}_domain", f"service_{port}_port")
        if host and protocol in {"http", "https", "tcp"}:
            services.append({"port": port, "protocol": protocol, "address": host,
                "url": f"{scheme}://{host}" if protocol != "tcp" else None})
    usage = snapshot.get("usage_info")
    usage = usage if isinstance(usage, dict) else {}
    valid_at = usage.get("valid_at")
    sample_time = None
    try:
        sample_time = datetime.fromisoformat(valid_at) if isinstance(valid_at, str) and len(valid_at) <= 80 else None
        valid_at = sample_time.isoformat() if sample_time and sample_time.tzinfo else None
    except ValueError:
        valid_at = None
    metrics = {"cpu_usage_percent": number(usage.get("cpu_usage_percent"), maximum=100000),
        "mem_usage_percent": number(usage.get("mem_usage_percent"), maximum=100),
        **{key: number(usage.get(key), maximum=2**53 - 1, integer=True) for key in (
            "mem_usage", "mem_limit", "root_fs_used_size", "root_fs_total_size", "data_disk_used_size", "data_disk_total_size")}}
    # Real snapshots provide usable samples while usage_info.valid is false;
    # do not interpret that undocumented flag as sample readiness. Expose age.
    fresh = bool(valid_at and -30 <= (datetime.now(timezone.utc) - sample_time).total_seconds() <= 120)
    monitor = {"valid": any(value is not None for value in metrics.values()), "stale": not fresh, "valid_at": valid_at, **metrics}
    return {"jupyter_url": f"{base}/jupyter?{query}" if base else None,
        "autopanel_url": f"{base}/?{query}" if base else None, "services": services, "monitor": monitor}

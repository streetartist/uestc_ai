"""Organizer-only HTTPS route. Preserve Host/SNI/certificate verification.

An optional origin IP bypasses a CDN's unreliable cross-region route; it does
not affect participant subprocesses or external model downloads.
"""
import ipaddress
import os
import socket
from urllib.parse import urlsplit


def install_origin_route():
    address = os.environ.get("EVALUATION_API_ORIGIN_IP", "")
    if not address:
        return
    origin = urlsplit(os.environ["EVALUATION_API_BASE"])
    ip = ipaddress.ip_address(address)
    if origin.scheme != "https" or not origin.hostname or origin.username or not ip.is_global:
        raise ValueError("trusted evaluation origin requires HTTPS and a public IP")
    hostname = origin.hostname
    resolve = socket.getaddrinfo

    def direct(host, port, *args, **kwargs):
        if host == hostname and str(port) == str(origin.port or 443):
            return resolve(address, port, *args, **kwargs)
        return resolve(host, port, *args, **kwargs)

    socket.getaddrinfo = direct

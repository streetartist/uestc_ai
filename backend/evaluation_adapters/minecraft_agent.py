"""Untrusted contestant entry point; runs only in the separate agent container."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import stat
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

from .minecraft_protocol import JsonChannel


def load_agent(package: Path, directory: Path):
    directory.mkdir(mode=0o700)
    with zipfile.ZipFile(package) as archive:
        files = archive.infolist()
        if len(files) > 256 or sum(item.file_size for item in files) > 64 * 1024 * 1024:
            raise ValueError("agent package exceeds the extracted size limit")
        for item in files:
            name = PurePosixPath(item.filename)
            if (name.is_absolute() or not name.parts or ".." in name.parts or
                    "\\" in item.filename or stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK):
                raise ValueError("agent package contains an unsafe path")
            target = directory.joinpath(*name.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open("wb") as destination:
                    while chunk := source.read(65536):
                        destination.write(chunk)
    entry = directory / "agent.py"
    if not entry.is_file():
        raise ValueError("agent package must contain agent.py at its root")
    sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location("contestant_agent", entry)
    if not spec or not spec.loader:
        raise ValueError("agent.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    agent = module.Agent()
    if not callable(getattr(agent, "act", None)):
        raise ValueError("Agent must implement act(observation)")
    config_path = directory / "config.json"
    if config_path.is_file():
        if config_path.stat().st_size > 64 * 1024:
            raise ValueError("agent config.json exceeds 64 KB")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("agent config.json must be an object")
        configure = getattr(agent, "configure", None)
        if callable(configure):
            configure(config)
    return agent


def connect(path: str, timeout_seconds: float = 300) -> socket.socket:
    deadline = time.monotonic() + 120
    while True:
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.connect(path)
            sock.settimeout(timeout_seconds)
            return sock
        except (FileNotFoundError, ConnectionRefusedError):
            sock.close()
            if time.monotonic() >= deadline:
                raise TimeoutError("evaluation controller did not start")
            time.sleep(0.2)


def run(agent, channel: JsonChannel) -> None:
    while True:
        message = channel.receive()
        if message.get("type") == "done":
            return
        if message.get("type") == "reset":
            reset = getattr(agent, "reset", None)
            if callable(reset):
                reset(message["goal"])
            channel.send({"type": "ready"})
        elif message.get("type") == "step":
            action = agent.act(message["observation"])
            channel.send({"type": "action", "value": action})
        else:
            raise ValueError("unknown evaluation message")


def main() -> None:
    agent = load_agent(Path("/input/package.zip"), Path("/tmp/agent"))
    with connect(os.environ.get("EVALUATION_SOCKET", "/ipc/agent.sock"),
                 float(os.environ.get("EVALUATION_SOCKET_TIMEOUT", "300"))) as sock:
        run(agent, JsonChannel(sock))


if __name__ == "__main__":
    main()

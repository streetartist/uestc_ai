"""Run trusted evaluation adapters in isolated containers on a dedicated worker host.

Each adapter image reads /input/package.zip and /input/config.json, then writes
{"episodes": [{"metric_key": number, ...}]} to /output/result.json.
The image, datasets, seeds and episode logic are installed by the operator.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4
from pathlib import Path


def request_json(base: str, path: str, method="GET", body=None, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(base + path, data=data, headers={
        **({"Content-Type": "application/json"} if data is not None else {}),
        **(headers or {}),
    }, method=method)
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            return json.load(response) if response.status != 204 else None
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"evaluation API returned HTTP {error.code}") from error


def download(base: str, path: str, lease: str, destination: Path):
    url = urllib.parse.urljoin(base.rstrip("/") + "/", path)
    origin = urllib.parse.urlsplit(base)
    target = urllib.parse.urlsplit(url)
    if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
        raise RuntimeError("evaluation asset must use the API origin")
    request = urllib.request.Request(url, headers={"X-Evaluation-Lease": lease})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as target:
        while chunk := response.read(1024 * 1024):
            target.write(chunk)


def heartbeat(base: str, run_id: str, lease: str, stopped: threading.Event, container_names):
    names = [container_names] if isinstance(container_names, str) else container_names
    while not stopped.wait(60):
        try:
            request_json(base, f"/evaluation-worker/runs/{run_id}/heartbeat", "POST", {}, {"X-Evaluation-Lease": lease})
        except (OSError, RuntimeError):
            stopped.set()
            for name in names:
                remove_container(name)
            return


def remove_container(name: str):
    try:
        subprocess.run(["docker", "rm", "-f", name],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def pinned_image(image: str) -> bool:
    # Local builds have an immutable image ID; deployed images use a repo digest.
    return bool(re.fullmatch(r"(?:[^\s]+@)?sha256:[a-f0-9]{64}", image))


def execute_minecraft(base: str, job: dict, trusted_image: str, agent_image: str,
                      scenarios_path: str, proxy_url: str, network: str, log_directory: str = ""):
    config = job["config"]
    resources = config["resources"]
    if not pinned_image(agent_image):
        raise RuntimeError("Minecraft agent image requires a pinned digest")
    scenarios = Path(scenarios_path).resolve()
    if not scenarios.is_file():
        raise RuntimeError("trusted Minecraft scenarios file is missing")
    if config["api"]["enabled"] and (not proxy_url or not network):
        raise RuntimeError("API proxy URL and restricted Docker network are required")

    with tempfile.TemporaryDirectory(prefix="minecraft-evaluation-") as directory:
        root = Path(directory)
        inputs, output, ipc = root / "input", root / "output", root / "ipc"
        inputs.mkdir(mode=0o755)
        output.mkdir(mode=0o700)
        ipc.mkdir(mode=0o755)
        os.chmod(ipc, 0o755)
        download(base, job["asset_url"], job["lease_token"], inputs / "package.zip")
        os.chmod(inputs / "package.zip", 0o644)
        (inputs / "config.json").write_text(json.dumps(config), encoding="utf-8")
        trusted_name, agent_name = "minecraft-env-" + uuid4().hex, "minecraft-agent-" + uuid4().hex
        # Docker Desktop cannot share a Linux Unix socket through a Windows bind
        # mount. Keep IPC on the Docker host while inputs/results remain files.
        ipc_volume = "minecraft-ipc-" + uuid4().hex if os.name == "nt" else None
        ipc_mount = (f"type=volume,src={ipc_volume},dst=/ipc" if ipc_volume
                     else f"type=bind,src={ipc.resolve()},dst=/ipc")
        common = ["--rm", "--init", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                  "--label", "uestc.evaluation_run=" + job["id"],
                  f"--cpus={resources['cpus']}", f"--memory={resources['memory_mb']}m"]
        controller = [
            "docker", "run", *common, "--pids-limit=512", "--name", trusted_name, "--user=0:0", "--network=none",
            "--tmpfs=/tmp:rw,exec,nosuid,size=2048m", "--env", "MINEDOJO_HEADLESS=1",
            "--env", "MINEDOJO_DEBUG_LOG=1", "--env", "MALMO_MINECRAFT_OUTPUT_LOGDIR=/output",
            "--mount", f"type=bind,src={(inputs / 'config.json').resolve()},dst=/input/config.json,readonly",
            "--mount", f"type=bind,src={scenarios},dst=/scenarios/scenarios.json,readonly",
            "--mount", f"type=bind,src={output.resolve()},dst=/output",
            "--mount", ipc_mount,
            trusted_image,
        ]
        contestant = [
            "docker", "run", *common, "--pids-limit=256", "--read-only", "--name", agent_name, "--user=65534:65534",
            "--tmpfs=/tmp:rw,exec,nosuid,size=128m",
            "--env", f"EVALUATION_SOCKET_TIMEOUT={resources['time_seconds']}",
            "--network=" + (network if config["api"]["enabled"] else "none"),
            "--mount", f"type=bind,src={(inputs / 'package.zip').resolve()},dst=/input/package.zip,readonly",
            "--mount", ipc_mount,
        ]
        if config["api"]["enabled"]:
            contestant.extend([
                "--env", "EVALUATION_API_URL=" + proxy_url.rstrip("/") + f"/evaluation-worker/runs/{job['id']}/api",
                "--env", "EVALUATION_API_TOKEN=" + job["api_token"],
            ])
        contestant.append(agent_image)
        stopped = threading.Event()
        thread = threading.Thread(target=heartbeat, args=(base, job["id"], job["lease_token"], stopped,
                                                          [trusted_name, agent_name]), daemon=True)
        deadline = time.monotonic() + resources["time_seconds"]
        controller_process = None
        thread.start()
        logs = Path(log_directory) if log_directory else root
        logs.mkdir(parents=True, exist_ok=True)
        try:
            with (logs / "controller.log").open("wb") as controller_log, (logs / "agent.log").open("wb") as agent_log:
                controller_process = subprocess.Popen(controller, stdout=controller_log, stderr=subprocess.STDOUT)
                ready_deadline = min(deadline, time.monotonic() + 120)
                while True:
                    ready_remaining = ready_deadline - time.monotonic()
                    if ready_remaining <= 0:
                        raise subprocess.TimeoutExpired(controller, resources["time_seconds"])
                    ready = (subprocess.run(["docker", "exec", trusted_name, "test", "-S", "/ipc/agent.sock"],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            timeout=min(10, ready_remaining), check=False).returncode == 0
                             if ipc_volume else (ipc / "agent.sock").exists())
                    if ready:
                        break
                    if stopped.is_set() or controller_process.poll() is not None:
                        raise RuntimeError("Minecraft controller exited before starting its socket")
                    if time.monotonic() >= ready_deadline:
                        raise subprocess.TimeoutExpired(controller, resources["time_seconds"])
                    stopped.wait(min(0.5, max(0, ready_deadline - time.monotonic())))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(controller, resources["time_seconds"])
                agent_result = subprocess.run(contestant, timeout=remaining,
                                              stdout=agent_log, stderr=subprocess.STDOUT, check=False)
                if agent_result.returncode or stopped.is_set():
                    raise RuntimeError("Minecraft agent exited before evaluation completed")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(controller, resources["time_seconds"])
                controller_process.wait(timeout=remaining)
                if controller_process.returncode or stopped.is_set():
                    raise RuntimeError("Minecraft controller did not complete")
            result_path = output / "result.json"
            if not result_path.is_file() or result_path.stat().st_size > 256 * 1024:
                raise RuntimeError("Minecraft controller did not write a valid result")
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(result, dict) or set(result) != {"episodes"}:
                raise RuntimeError("Minecraft controller result has invalid fields")
            return {"status": "completed", "episodes": result["episodes"]}
        finally:
            remove_container(agent_name)
            remove_container(trusted_name)
            if controller_process is not None:
                try:
                    controller_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    controller_process.kill()
                    controller_process.wait(timeout=10)
            if ipc_volume:
                subprocess.run(["docker", "volume", "rm", ipc_volume], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=10, check=False)
            stopped.set()
            thread.join(timeout=2)


def execute(base: str, job: dict, images: dict[str, str], proxy_url: str, network: str, gpu_device: str,
            agent_image: str = "", scenarios_path: str = "", log_directory: str = ""):
    config = job["config"]
    image = images.get(config["adapter"], "")
    if not pinned_image(image):
        raise RuntimeError("adapter image is not configured with a pinned digest")
    resources = config["resources"]
    if config["adapter"] == "minecraft-agent-v1" and config["task"] == "open-world":
        return execute_minecraft(base, job, image, agent_image, scenarios_path, proxy_url, network, log_directory)
    if config["api"]["enabled"] and (not proxy_url or not network):
        raise RuntimeError("API proxy URL and restricted Docker network are required")
    if resources["gpu"] and (not gpu_device or not re.fullmatch(r"(?:GPU-)?[a-zA-Z0-9-]{8,64}", gpu_device)):
        raise RuntimeError("a dedicated GPU device must be configured")
    with tempfile.TemporaryDirectory(prefix="evaluation-") as directory:
        root = Path(directory)
        container_name = "evaluation-" + uuid4().hex
        inputs, output = root / "input", root / "output"
        inputs.mkdir(mode=0o755)
        output.mkdir(mode=0o777)
        download(base, job["asset_url"], job["lease_token"], inputs / "package.zip")
        (inputs / "config.json").write_text(json.dumps(config), encoding="utf-8")
        command = [
            "docker", "run", "--rm", "--name", container_name, "--init", "--read-only", "--cap-drop=ALL",
            "--label", "uestc.evaluation_run=" + job["id"],
            "--security-opt=no-new-privileges", "--pids-limit=256", "--user=65534:65534",
            f"--cpus={resources['cpus']}", f"--memory={resources['memory_mb']}m",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=256m", "--network=" + (network if config["api"]["enabled"] else "none"),
            "--mount", f"type=bind,src={inputs.resolve()},dst=/input,readonly",
            "--mount", f"type=bind,src={output.resolve()},dst=/output",
        ]
        if resources["gpu"]:
            command.extend(["--gpus", "device=" + gpu_device])
        if config["api"]["enabled"]:
            command.extend([
                "--env", "EVALUATION_API_URL=" + proxy_url.rstrip("/") + f"/evaluation-worker/runs/{job['id']}/api",
                "--env", "EVALUATION_API_TOKEN=" + job["api_token"],
            ])
        command.append(image)
        stopped = threading.Event()
        thread = threading.Thread(target=heartbeat, args=(base, job["id"], job["lease_token"], stopped, container_name), daemon=True)
        thread.start()
        try:
            result = subprocess.run(command, timeout=resources["time_seconds"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if stopped.is_set():
                raise RuntimeError("worker lease could not be renewed")
            if result.returncode:
                raise RuntimeError(f"adapter exited with status {result.returncode}")
            path = output / "result.json"
            if not path.is_file() or path.stat().st_size > 256 * 1024:
                raise RuntimeError("adapter did not produce a valid result file")
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or set(data) != {"episodes"}:
                raise RuntimeError("adapter result must contain episodes")
            return {"status": "completed", "episodes": data["episodes"]}
        finally:
            # Killing the Docker client on timeout does not stop its container.
            remove_container(container_name)
            stopped.set()
            thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description="Poll and execute configured evaluation adapters")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    base = os.environ["EVALUATION_API_BASE"].rstrip("/")
    if not base.startswith("https://") and not re.fullmatch(r"http://(?:127\.0\.0\.1|localhost):\d+/api", base):
        raise SystemExit("EVALUATION_API_BASE must use HTTPS or local loopback")
    token = os.environ["EVALUATION_WORKER_TOKEN"]
    images = json.loads(os.environ.get("EVALUATION_IMAGES_JSON", "{}"))
    if not isinstance(images, dict):
        raise SystemExit("EVALUATION_IMAGES_JSON must be an object")
    proxy_url = os.environ.get("EVALUATION_API_PROXY_URL", "")
    network = os.environ.get("EVALUATION_NETWORK", "")
    gpu_device = os.environ.get("EVALUATION_GPU_DEVICE", "")
    agent_image = os.environ.get("EVALUATION_AGENT_IMAGE", "")
    scenarios_path = os.environ.get("EVALUATION_MINECRAFT_SCENARIOS", "")
    supported = list(images)
    if ("minecraft-agent-v1" in supported and
            (not pinned_image(agent_image)
             or not Path(scenarios_path).is_file())):
        supported.remove("minecraft-agent-v1")
    if not supported:
        raise SystemExit("No evaluation adapters have a configured runtime")
    while True:
        job = request_json(base, "/evaluation-worker/claim", "POST", {
            "adapters": supported, "gpu": bool(gpu_device),
        }, {"Authorization": "Bearer " + token})
        if job is None:
            if args.once:
                break
            threading.Event().wait(5)
            continue
        try:
            result = execute(base, job, images, proxy_url, network, gpu_device, agent_image, scenarios_path)
        except subprocess.TimeoutExpired:
            result = {"status": "failed", "error": "evaluation time limit exceeded"}
        except (OSError, ValueError, RuntimeError) as error:
            result = {"status": "failed", "error": str(error)[:400]}
        request_json(base, f"/evaluation-worker/runs/{job['id']}/complete", "POST", result, {
            "X-Evaluation-Lease": job["lease_token"],
        })
        if args.once:
            break


if __name__ == "__main__":
    main()

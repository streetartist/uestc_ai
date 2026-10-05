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
import shutil
import signal
import subprocess
import sys
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


def cleanup_worker_containers(worker_id: str):
    """Recover containers left by this worker after a crash or host restart."""
    names = subprocess.check_output([
        'docker', 'ps', '-aq', '--filter', 'label=uestc.worker_id=' + worker_id,
    ], text=True, timeout=10).splitlines()
    for name in names:
        remove_container(name)


def stop_worker(_signal, _frame):
    # SystemExit unwinds execute()'s finally blocks and removes live containers.
    raise SystemExit(0)


def pinned_image(image: str) -> bool:
    # Local builds have an immutable image ID; deployed images use a repo digest.
    return bool(re.fullmatch(r"(?:[^\s]+@)?sha256:[a-f0-9]{64}", image))


def execute_isolated_agent(base: str, job: dict, trusted_image: str, agent_image: str,
                      scenarios_path: str, proxy_url: str, network: str, log_directory: str = ""):
    config = job["config"]
    resources = config["resources"]
    robot = config["adapter"] == "robot-arm-agent-v1"
    label = "Robot arm" if robot else "Minecraft"
    if not pinned_image(agent_image):
        raise RuntimeError(label + " agent image requires a pinned digest")
    scenarios = Path(scenarios_path).resolve()
    if not scenarios.is_file():
        raise RuntimeError("trusted " + label + " scenarios file is missing")
    if robot and resources["gpu"]:
        raise RuntimeError("Robot arm v1 uses CPU rendering; GPU allocation is not supported")
    if config["api"]["enabled"] and (not proxy_url or not network):
        raise RuntimeError("API proxy URL and restricted Docker network are required")

    with tempfile.TemporaryDirectory(prefix="minecraft-evaluation-") as directory:
        root = Path(directory)
        inputs, output, ipc = root / "input", root / "output", root / "ipc"
        inputs.mkdir(mode=0o755)
        # cap-drop=ALL removes root's DAC override on Linux bind mounts. Give
        # the trusted controller our group, without exposing /output to agents.
        host_group = os.getgid() if hasattr(os, 'getgid') else 0
        # Robot evidence has nested files; keep them owned by the worker so
        # temporary directories can be removed without host root privileges.
        controller_user = os.getuid() if robot and hasattr(os, 'getuid') else 0
        output.mkdir(mode=0o770)
        os.chmod(output, 0o770)
        ipc.mkdir(mode=0o775)
        os.chmod(ipc, 0o775)
        download(base, job["asset_url"], job["lease_token"], inputs / "package.zip")
        os.chmod(inputs / "package.zip", 0o644)
        (inputs / "config.json").write_text(json.dumps(config), encoding="utf-8")
        prefix = "robot-arm" if robot else "minecraft"
        trusted_name, agent_name = prefix + "-env-" + uuid4().hex, prefix + "-agent-" + uuid4().hex
        # Docker Desktop cannot share a Linux Unix socket through a Windows bind
        # mount. Keep IPC on the Docker host while inputs/results remain files.
        ipc_volume = "minecraft-ipc-" + uuid4().hex if os.name == "nt" else None
        ipc_mount = (f"type=volume,src={ipc_volume},dst=/ipc" if ipc_volume
                     else f"type=bind,src={ipc.resolve()},dst=/ipc")
        common = ["--rm", "--init", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                  "--label", "uestc.evaluation_run=" + job["id"],
                  "--label", "uestc.worker_id=" + os.environ.get('EVALUATION_WORKER_ID', 'legacy'),
                  f"--cpus={resources['cpus']}", f"--memory={resources['memory_mb']}m"]
        controller = [
            "docker", "run", *common, "--pids-limit=512", "--name", trusted_name, f"--user={controller_user}:{host_group}", "--network=none",
            # A network=none container has no IP entry for its generated name.
            # Java / Gradle still need getLocalHost() to resolve without DNS.
            "--hostname=localhost",
            "--tmpfs=/tmp:rw,exec,nosuid,size=2048m", "--env", "MINEDOJO_HEADLESS=1",
            # Java's nested debug files are root-owned. Keep them on the
            # controller tmpfs; stdout is retained in the worker's private log.
            "--env", "MINEDOJO_DEBUG_LOG=1", "--env", "MALMO_MINECRAFT_OUTPUT_LOGDIR=/tmp/minecraft-logs",
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
                "--dns=127.0.0.1",
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
                        raise RuntimeError(label + " controller exited before starting its socket")
                    if time.monotonic() >= ready_deadline:
                        raise subprocess.TimeoutExpired(controller, resources["time_seconds"])
                    stopped.wait(min(0.5, max(0, ready_deadline - time.monotonic())))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(controller, resources["time_seconds"])
                agent_result = subprocess.run(contestant, timeout=remaining,
                                              stdout=agent_log, stderr=subprocess.STDOUT, check=False)
                if agent_result.returncode or stopped.is_set():
                    raise RuntimeError(label + " agent exited before evaluation completed")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(controller, resources["time_seconds"])
                controller_process.wait(timeout=remaining)
                if controller_process.returncode or stopped.is_set():
                    raise RuntimeError(label + " controller did not complete")
            result_path = output / "result.json"
            if not result_path.is_file() or result_path.stat().st_size > 256 * 1024:
                raise RuntimeError(label + " controller did not write a valid result")
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(result, dict) or set(result) != {"episodes"}:
                raise RuntimeError(label + " controller result has invalid fields")
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
            if robot and log_directory and (output / "evidence").is_dir():
                shutil.copytree(output / "evidence", logs / "evidence", dirs_exist_ok=True)


def execute_minecraft(base, job, trusted_image, agent_image, scenarios_path, proxy_url, network, log_directory=""):
    return execute_isolated_agent(base, job, trusted_image, agent_image, scenarios_path, proxy_url, network, log_directory)


def execute(base: str, job: dict, images: dict[str, str], proxy_url: str, network: str, gpu_device: str,
            agent_image: str = "", scenarios_path: str = "", log_directory: str = ""):
    config = job["config"]
    runtime = job.get("runtime")
    image = runtime["image"] if runtime else images.get(config["adapter"], "")
    if not pinned_image(image):
        raise RuntimeError("adapter image is not configured with a pinned digest")
    resources = config["resources"]
    if config["adapter"] == "robot-arm-agent-v1":
        if not runtime:
            raise RuntimeError("Robot arm evaluation requires a problem runtime with private scenarios")
        with tempfile.TemporaryDirectory(prefix="robot-scenes-") as directory:
            cases = Path(directory) / "scenarios.json"
            cases.write_text(json.dumps(runtime["scenarios"]), encoding="utf-8")
            return execute_isolated_agent(base, job, image, runtime.get("agent_image", ""), str(cases), proxy_url, network, log_directory)
    if config["adapter"] == "minecraft-agent-v1" and config["task"] == "open-world":
        if runtime:
            with tempfile.TemporaryDirectory(prefix="private-scenes-") as directory:
                cases = Path(directory) / "scenarios.json"
                cases.write_text(json.dumps(runtime["scenarios"]), encoding="utf-8")
                return execute_minecraft(base, job, image, runtime.get("agent_image", ""), str(cases), proxy_url, network, log_directory)
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
        output.mkdir(mode=0o770)
        os.chmod(output, 0o770)
        host_group = os.getgid() if hasattr(os, 'getgid') else 0
        # Match a non-root host worker's UID so nested results remain removable.
        runner_user = (f'{os.getuid()}:{host_group}'
                       if hasattr(os, 'getuid') and os.getuid() != 0 else '65534:65534')
        download(base, job["asset_url"], job["lease_token"], inputs / "package.zip")
        (inputs / "config.json").write_text(json.dumps(config), encoding="utf-8")
        if runtime:
            (inputs / "scenarios.json").write_text(json.dumps(runtime.get("scenarios", [])), encoding="utf-8")
        command = [
            "docker", "run", "--rm", "--name", container_name, "--init", "--read-only", "--cap-drop=ALL",
            "--label", "uestc.evaluation_run=" + job["id"],
            "--label", "uestc.worker_id=" + os.environ.get('EVALUATION_WORKER_ID', 'legacy'),
            "--security-opt=no-new-privileges", "--pids-limit=256", f"--user={runner_user}", "--hostname=localhost",
            f"--group-add={host_group}",
            f"--cpus={resources['cpus']}", f"--memory={resources['memory_mb']}m",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=256m", "--network=" + (network if config["api"]["enabled"] else "none"),
            "--mount", f"type=bind,src={inputs.resolve()},dst=/input,readonly",
            "--mount", f"type=bind,src={output.resolve()},dst=/output",
        ]
        if resources["gpu"]:
            command.extend(["--gpus", "device=" + gpu_device])
        if config["api"]["enabled"]:
            command.extend([
                "--dns=127.0.0.1",
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
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
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
    supported = [adapter for adapter, image in images.items()
                 if isinstance(adapter, str) and isinstance(image, str) and pinned_image(image)]
    if ("minecraft-agent-v1" in supported and
            (not pinned_image(agent_image)
             or not Path(scenarios_path).is_file())):
        supported.remove("minecraft-agent-v1")
    worker_id = os.environ.get("EVALUATION_WORKER_ID") or "worker-" + uuid4().hex
    os.environ['EVALUATION_WORKER_ID'] = worker_id
    signal.signal(signal.SIGTERM, stop_worker)
    recovered = False
    failures = 0
    allowed = {item.strip() for item in os.environ.get("EVALUATION_WORKER_ADAPTERS", "").split(",") if item.strip()}
    if any(not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", item) for item in allowed):
        raise SystemExit("EVALUATION_WORKER_ADAPTERS must contain adapter IDs")
    installed = {item.strip() for item in os.environ.get('EVALUATION_INSTALLED_ADAPTERS', '').split(',') if item.strip()}
    if any(not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", item) for item in installed):
        raise SystemExit('EVALUATION_INSTALLED_ADAPTERS must contain adapter IDs')
    while True:
        try:
            subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            if not recovered:
                cleanup_worker_containers(worker_id)
                recovered = True
            managed = request_json(base, "/evaluation-worker/runtime-catalog", headers={"Authorization": "Bearer " + token})
            available = sorted(set(supported + managed["adapters"]) | installed)
            if allowed:
                available = [item for item in available if item in allowed]
            job = request_json(base, "/evaluation-worker/claim", "POST", {
                "adapters": available, "gpu": bool(gpu_device), "managed_runtime": True,
                "api_proxy": bool(proxy_url and network), "worker_id": worker_id,
                "legacy_adapters": supported,
            }, {"Authorization": "Bearer " + token}) if available else None
            failures = 0
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            recovered = False
            if args.once:
                raise SystemExit("Docker or evaluation API is unavailable") from None
            failures += 1
            delay = min(30, 2 ** min(failures, 5))
            print(f"Docker or evaluation API unavailable; retrying in {delay}s", file=sys.stderr, flush=True)
            threading.Event().wait(delay)
            continue
        if not available:
            if args.once:
                break
            threading.Event().wait(5)
            continue
        if job is None:
            if args.once:
                break
            threading.Event().wait(5)
            continue
        print(f"Evaluation {job['id']} started ({job['config']['adapter']})", flush=True)
        try:
            log_root = os.environ.get("EVALUATION_LOG_DIRECTORY", "")
            logs = str(Path(log_root) / job["id"]) if log_root else ""
            result = execute(base, job, images, proxy_url, network, gpu_device, agent_image, scenarios_path, logs)
        except subprocess.TimeoutExpired:
            result = {"status": "failed", "error": "evaluation time limit exceeded"}
        except (OSError, ValueError, RuntimeError) as error:
            result = {"status": "failed", "error": str(error)[:400]}
        try:
            request_json(base, f"/evaluation-worker/runs/{job['id']}/complete", "POST", result, {
                "X-Evaluation-Lease": job["lease_token"],
            })
            print(f"Evaluation {job['id']} {result['status']}", flush=True)
        except (OSError, RuntimeError):
            if args.once:
                raise SystemExit("Evaluation completion could not be confirmed") from None
            # Durable lease expiry handles recovery; do not charge another attempt.
            print("Evaluation completion unconfirmed; platform lease will handle recovery", file=sys.stderr, flush=True)
        if args.once:
            break


if __name__ == "__main__":
    main()

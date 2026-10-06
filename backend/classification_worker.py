"""AutoDL-native GPU worker, using the platform's existing trial/lease protocol."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import platform
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from evaluation_worker import download, request_json
from evaluation_adapters.classification_runner import digest_file, evaluate, validate_scenarios


def verify_runtime():
    root = Path(__file__).resolve().parent
    manifest = json.loads((root.parent / "runtime-manifest.json").read_text())
    if manifest["environment"]["python"] != platform.python_version():
        raise RuntimeError("classification Python version differs from the saved image")
    for name, version in manifest["environment"].items():
        if name != "python" and importlib.metadata.version(name) != version:
            raise RuntimeError("classification dependency differs from the saved image")
    for relative, digest in manifest["files"].items():
        path = root / relative
        if path.is_symlink() or root not in path.resolve().parents or digest_file(path) != digest:
            raise RuntimeError("classification code differs from the saved image")


def execute(base, job, *, image_uuid, dataset_root, gpu, log_root, cancelled=None):
    config = job["config"]
    runtime = job.get("runtime") or {}
    if (config["adapter"] != "classification-v1" or config["api"]["enabled"] or not config["resources"]["gpu"]
            or runtime.get("execution") != "autodl-native" or runtime.get("image") != image_uuid):
        raise ValueError("classification job does not match the installed AutoDL image")
    validate_scenarios(runtime.get("scenarios", []), config["resources"]["episodes"])
    if not re.fullmatch(r"GPU-[a-fA-F0-9-]{32,40}", gpu):
        raise ValueError("classification requires a dedicated GPU UUID")
    datasets = Path(dataset_root).resolve()
    resources = config["resources"]
    deadline = time.monotonic() + resources["time_seconds"]
    with tempfile.TemporaryDirectory(prefix="uestc-classification-job-") as directory:
        package = Path(directory) / "package.zip"
        download(base, job["asset_url"], job["lease_token"], package)
        if digest_file(package) != job["submission"].get("package_sha256"):
            raise ValueError("frozen inference package failed SHA256 verification")
        episodes = []
        for index, scene in enumerate(runtime["scenarios"]):
            path = datasets / scene["dataset"]
            if path.is_symlink() or not path.is_dir() or path.resolve().parent != datasets:
                raise ValueError("the requested private dataset is not installed")
            seconds = int(deadline - time.monotonic())
            if seconds < 1:
                raise TimeoutError("evaluation time limit exceeded")
            metrics = evaluate(package, path, scene["manifest_sha256"], Path(log_root) / job["id"] / str(index),
                gpu=gpu, seconds=seconds, cpus=resources["cpus"], memory_mb=resources["memory_mb"],
                model_hosts=os.environ.get("EVALUATION_MODEL_HOSTS", "huggingface.co,*.huggingface.co,*.hf.co").split(","),
                cancelled=cancelled)
            episodes.append({key: metrics[key] for key in config["metrics"]})
        from evaluation_evidence import publish_classification
        publish_classification(base, job, log_root)
        return {"status": "completed", "episodes": episodes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    verify_runtime()
    base = os.environ["EVALUATION_API_BASE"].rstrip("/")
    if not base.startswith("https://") and not re.fullmatch(r"http://127\.0\.0\.1:\d+/api", base):
        raise SystemExit("evaluation API must use HTTPS or a local acceptance tunnel")
    token = os.environ["EVALUATION_WORKER_TOKEN"]
    image = os.environ["EVALUATION_AUTODL_IMAGE_UUID"]
    if not re.fullmatch(r"image-[A-Za-z0-9_-]{1,150}", image):
        raise SystemExit("configure the saved AutoDL private image UUID")
    gpu = os.environ.get("EVALUATION_GPU_DEVICE") or subprocess.check_output([
        "nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"], text=True, timeout=5).strip()
    embedded = Path(__file__).resolve().parent.parent / "datasets"
    default_datasets = str(embedded) if embedded.is_dir() else "/root/autodl-tmp/uestc-evaluation/datasets"
    datasets = Path(os.environ.get("EVALUATION_CLASSIFICATION_DATASETS", default_datasets))
    logs = Path(os.environ.get("EVALUATION_LOG_DIRECTORY", "/root/autodl-tmp/uestc-evaluation/logs"))
    worker_id = os.environ.get("EVALUATION_WORKER_ID", "autodl-classification-" + gpu[-12:])
    stopping = threading.Event()
    active = [None]
    def stop(*_):
        stopping.set()
        if active[0] is not None:
            active[0].set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    capabilities = {"adapters": ["classification-v1"], "gpu": True, "managed_runtime": True,
                    "api_proxy": False, "worker_id": worker_id, "legacy_adapters": [],
                    "execution_backends": ["autodl-native"], "runtime_images": [image], "dataset_manifests": {}}
    while not stopping.is_set():
        job = None
        try:
            capabilities["dataset_manifests"] = {
                path.name: digest_file(path / "manifest.json") for path in datasets.iterdir()
                if path.is_dir() and not path.is_symlink() and (path / "manifest.json").is_file()
                and re.fullmatch(r"[a-z][a-z0-9_-]{2,80}", path.name)
            }
            job = request_json(base, "/evaluation-worker/claim", "POST", capabilities,
                               {"Authorization": "Bearer " + token})
        except (OSError, RuntimeError):
            if args.once:
                raise SystemExit("classification API unavailable") from None
            print("classification API unavailable; retrying", flush=True)
            stopping.wait(10)
            continue
        if not job:
            if args.once:
                return
            stopping.wait(5)
            continue
        lost = threading.Event()
        active[0] = lost
        finished = threading.Event()
        def heartbeat():
            while not finished.wait(30):
                if stopping.is_set():
                    lost.set()
                    return
                try:
                    request_json(base, f"/evaluation-worker/runs/{job['id']}/heartbeat", "POST", {},
                                 {"X-Evaluation-Lease": job["lease_token"]})
                except (OSError, RuntimeError):
                    lost.set()
                    return
        pulse = threading.Thread(target=heartbeat, daemon=True)
        pulse.start()
        try:
            result = execute(base, job, image_uuid=image, dataset_root=datasets, gpu=gpu, log_root=logs, cancelled=lost)
        except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError):
            # Submitted tracebacks may contain arbitrary text or private paths.
            result = {"status": "failed", "error": "分类推理未完成：请检查提交协议、权重、资源限制或联系组织方核查私有日志。"}
        finally:
            active[0] = None
            finished.set()
            pulse.join(timeout=2)
        try:
            request_json(base, f"/evaluation-worker/runs/{job['id']}/complete", "POST", result,
                         {"X-Evaluation-Lease": job["lease_token"]})
            print(f"Classification {job['id']} {result['status']}", flush=True)
        except (OSError, RuntimeError):
            # No second attempt is charged. Existing durable lease recovery applies.
            print("completion unconfirmed; lease recovery will reconcile", flush=True)
            if args.once:
                raise SystemExit(1)
        if args.once:
            return


if __name__ == "__main__":
    main()

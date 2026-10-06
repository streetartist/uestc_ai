"""Trusted classification controller for an organizer-only AutoDL GPU instance.

Private datasets and results stay root-only. Submitted Python executes under a
fresh UID, with no credentials, network sockets, labels or writable runner code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath

from .classification_assets import download_weights


def validate_scenarios(scenes, episodes):
    if len(scenes) != episodes or not scenes:
        raise ValueError("classification requires one installed dataset per episode")
    for scene in scenes:
        if (not isinstance(scene, dict) or set(scene) != {"dataset", "manifest_sha256"}
                or not isinstance(scene["dataset"], str)
                or not re.fullmatch(r"[a-z][a-z0-9_-]{2,80}", scene["dataset"])
                or not isinstance(scene["manifest_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", scene["manifest_sha256"])):
            raise ValueError("classification scenario needs an installed dataset ID and manifest SHA256")


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_dataset(directory: Path, expected_digest: str):
    import numpy as np
    directory = directory.resolve()
    if directory.stat().st_mode & 0o077 or directory.stat().st_uid != 0:
        raise ValueError("private dataset directory must have mode 0700")
    manifest = directory / "manifest.json"
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_uid != 0 or manifest.stat().st_size > 16 * 1024 * 1024 or digest_file(manifest) != expected_digest:
        raise ValueError("private dataset manifest failed SHA256 verification")
    data = json.loads(manifest.read_text())
    if (not isinstance(data, dict) or set(data) != {"version", "classes", "modality", "samples"}
            or data["version"] != 1 or data["modality"] != "depth"
            or not isinstance(data["classes"], list) or len(data["classes"]) != 40
            or any(not isinstance(name, str) or not 1 <= len(name) <= 100 for name in data["classes"])
            or len(set(data["classes"])) != 40
            or not isinstance(data["samples"], list) or not 1 <= len(data["samples"]) <= 100000):
        raise ValueError("dataset must declare 40 unique classes and non-RGB depth samples")
    seen = set()
    for sample in data["samples"]:
        if (not isinstance(sample, dict) or set(sample) != {"file", "label", "sha256"}
                or type(sample["label"]) is not int or not 0 <= sample["label"] < 40
                or not isinstance(sample["file"], str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}\.npy", sample["file"])
                or sample["file"] in seen
                or not isinstance(sample["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", sample["sha256"])):
            raise ValueError("dataset sample metadata is invalid")
        seen.add(sample["file"])
        path = directory / sample["file"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024 or digest_file(path) != sample["sha256"]:
            raise ValueError("dataset sample failed size/SHA256 verification")
        array = np.load(path, allow_pickle=False, mmap_mode="r")
        if (array.ndim != 4 or array.shape[1] != 1 or not all(0 < size <= 2048 for size in array.shape)
                or array.dtype.kind not in "uif" or not np.isfinite(array).all()):
            raise ValueError("depth clips must be finite non-RGB numeric arrays in [T,1,H,W] layout")
    return data


def extract_package(package: Path, target: Path):
    with zipfile.ZipFile(package) as archive:
        files = archive.infolist()
        if len(files) > 256 or sum(item.file_size for item in files) > 64 * 1024 * 1024:
            raise ValueError("inference ZIP exceeds the extracted size limit")
        names = set()
        for item in files:
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in item.filename
                    or not path.parts or item.filename in names
                    or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError("inference ZIP contains an unsafe path")
            names.add(item.filename)
            destination = target.joinpath(*path.parts)
            if item.is_dir():
                destination.mkdir(parents=True, exist_ok=True, mode=0o755)
                destination.chmod(0o755)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with archive.open(item) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output, 65536)
                destination.chmod(0o644)
        # mkdir(mode=...) still applies the organizer's umask. Nested packages
        # must remain readable by the inference UID under umask 077 as well.
        for directory in target.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o755)
    if not (target / "inference.py").is_file() or not (target / "config.json").is_file():
        raise ValueError("ZIP must contain inference.py and config.json at its root")
    path = target / "config.json"
    if path.stat().st_size > 64 * 1024:
        raise ValueError("inference config exceeds 64 KB")
    config = json.loads(path.read_text())
    if not isinstance(config, dict) or config.get("num_classes") != 40:
        raise ValueError("inference config must declare num_classes=40")
    return config


def classification_metrics(truth, predictions, classes=40):
    if not truth or len(truth) != len(predictions):
        raise ValueError("prediction count differs from the private dataset")
    matrix = [[0] * classes for _ in range(classes)]
    for label, predicted in zip(truth, predictions):
        if type(label) is not int or type(predicted) is not int or not 0 <= label < classes or not 0 <= predicted < classes:
            raise ValueError("predicted class is outside the class mapping")
        matrix[label][predicted] += 1
    f1 = 0.0
    for index in range(classes):
        true_positive = matrix[index][index]
        denominator = sum(matrix[index]) + sum(row[index] for row in matrix)
        f1 += 2 * true_positive / denominator if denominator else 0
    return {"accuracy": 100 * sum(matrix[i][i] for i in range(classes)) / len(truth),
            "macro_f1": 100 * f1 / classes}, matrix


def predicted_class(message, request_id):
    if not isinstance(message, dict) or set(message) != {"type", "id", "scores"} or message["type"] != "prediction" or message["id"] != request_id:
        raise ValueError("inference response does not match the current sample")
    scores = message["scores"]
    if not isinstance(scores, list) or len(scores) != 40 or any(type(value) not in (int, float) or not math.isfinite(value) for value in scores):
        raise ValueError("inference must return exactly 40 finite class scores")
    return max(range(40), key=scores.__getitem__)


class GPUUsage:
    def __init__(self, device):
        self.device = device
        self.peak = 0
        self.error = None
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.monitor, daemon=True)

    def query(self, field):
        return subprocess.check_output(["nvidia-smi", "-i", self.device, "--query-gpu=" + field,
                                       "--format=csv,noheader,nounits"], text=True, timeout=5).strip()

    def monitor(self):
        while not self.stopped.is_set():
            try:
                self.peak = max(self.peak, float(self.query("memory.used")))
            except (OSError, ValueError, subprocess.SubprocessError):
                self.error = "GPU telemetry unavailable; evaluation cannot produce trusted memory metrics"
                return
            self.stopped.wait(0.1)


def evaluate(package: Path, dataset: Path, manifest_digest: str, output: Path, *, gpu: str,
             seconds=1800, memory_mb=6144, cpus=4, uid=20000, model_hosts=(), cancelled=None):
    import psutil
    if os.geteuid() != 0:
        raise RuntimeError("run the trusted controller as root on an organizer-only instance")
    deadline = time.monotonic() + seconds
    data = load_dataset(dataset, manifest_digest)
    usage = GPUUsage(gpu)
    processes = subprocess.check_output(["nvidia-smi", "-i", gpu, "--query-compute-apps=pid",
                                        "--format=csv,noheader,nounits"], text=True, timeout=5).strip()
    if processes:
        raise RuntimeError("the evaluation GPU must be dedicated and idle")
    # The parent path is root-owned. Submitted code can read its own package and
    # one current clip, but cannot modify either or access any private label.
    private = tempfile.TemporaryDirectory(prefix="uestc-classification-private-")
    public = tempfile.TemporaryDirectory(prefix="uestc-classification-input-")
    root, exposed = Path(private.name), Path(public.name)
    exposed.chmod(0o755)
    program = exposed / "program"
    program.mkdir(mode=0o755)
    program.chmod(0o755)
    sandbox_tmp = exposed / "scratch"
    sandbox_tmp.mkdir(mode=0o700)
    os.chown(sandbox_tmp, uid, uid)
    process = None
    read_fd, write_fd = os.pipe()
    buffer = bytearray()
    evidence, predictions, latencies = [], [], []
    def remaining():
        if cancelled is not None and cancelled.is_set():
            raise RuntimeError("evaluation lease was lost")
        if time.monotonic() >= deadline:
            raise TimeoutError("evaluation time limit exceeded")
        if process:
            try:
                # Orphans retain this UID and process group; include them too.
                rss = 0
                for candidate in psutil.process_iter(["uids", "memory_info"]):
                    if candidate.info["uids"].real == uid:
                        rss += candidate.info["memory_info"].rss
                if rss > memory_mb * 1024 * 1024:
                    raise RuntimeError("inference exceeded its host memory limit")
            except psutil.NoSuchProcess:
                pass
        # Bound writable scratch space even without nested Docker/cgroups.
        written, count = 0, 0
        for directory, _, files in os.walk(sandbox_tmp):
            for name in files:
                count += 1
                path = Path(directory) / name
                try:
                    if not path.is_symlink():
                        written += path.stat().st_size
                except FileNotFoundError:
                    pass
                if count > 512 or written > 256 * 1024 * 1024:
                    raise RuntimeError("inference exceeded its scratch storage limit")
        return max(0.001, deadline - time.monotonic())

    def receive():
        while b"\n" not in buffer:
            wait = min(0.2, remaining())
            if not select.select([read_fd], [], [], wait)[0]:
                if process.poll() is not None:
                    raise RuntimeError("inference process exited before returning a prediction")
                continue
            chunk = os.read(read_fd, 8192)
            if not chunk:
                raise RuntimeError("inference closed its response pipe")
            buffer.extend(chunk)
            if len(buffer) > 64 * 1024:
                raise ValueError("inference response exceeds 64 KB")
        line, _, rest = buffer.partition(b"\n")
        buffer[:] = rest
        remaining()
        return json.loads(line)

    try:
        config = extract_package(package, program)
        config["package_dir"] = str(program)
        local_weights = config.get("weights_file")
        if local_weights is not None:
            if (not isinstance(local_weights, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", local_weights)
                    or not (program / local_weights).is_file()):
                raise ValueError("weights_file must name a bundled file at the ZIP root")
            config["weights_path"] = str(program / local_weights)
        weights = config.get("weights")
        if weights is not None:
            if (not isinstance(weights, dict) or set(weights) != {"url", "sha256", "size"}
                    or not isinstance(weights["url"], str) or not isinstance(weights["sha256"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", weights["sha256"])
                    or type(weights["size"]) is not int or not 1 <= weights["size"] <= 2 * 1024**3):
                raise ValueError("weights require URL, SHA256 and size (maximum 2 GB)")
            download_weights(weights["url"], weights["sha256"], weights["size"], program / "weights.bin", list(model_hosts), deadline)
            (program / "weights.bin").chmod(0o644)
            config["weights_path"] = str(program / "weights.bin")
        config["classes"] = data["classes"]
        (program / "config.json").write_text(json.dumps(config))
        script = Path(__file__).with_name("classification_inference.py").resolve()
        environment = {"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin", "HOME": str(sandbox_tmp),
                       "TMPDIR": str(sandbox_tmp), "CUDA_VISIBLE_DEVICES": gpu,
                       "OMP_NUM_THREADS": str(cpus), "MKL_NUM_THREADS": str(cpus), "OPENBLAS_NUM_THREADS": str(cpus),
                       "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1", "CUBLAS_WORKSPACE_CONFIG": ":4096:8"}
        with (root / "inference.log").open("wb") as log:
            process = subprocess.Popen([sys.executable, "-I", str(script), str(program), str(write_fd),
                                        str(sandbox_tmp), str(uid), str(cpus), str(seconds)],
                stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, env=environment, cwd=sandbox_tmp,
                pass_fds=(write_fd,), start_new_session=True)
            os.close(write_fd)
            write_fd = -1
            usage.thread.start()
            if receive() != {"type": "ready"}:
                raise ValueError("inference did not initialize correctly")
            for index, sample in enumerate(data["samples"]):
                remaining()
                sample_path = exposed / "clip.npy"
                shutil.copyfile(dataset / sample["file"], sample_path)
                sample_path.chmod(0o644)
                # IDs/filenames and hidden labels are never sent to inference.
                request_id = os.urandom(16).hex()
                message = {"type": "predict", "id": request_id, "sample_path": str(sample_path)}
                start = time.perf_counter()
                process.stdin.write((json.dumps(message) + "\n").encode())
                process.stdin.flush()
                predicted = predicted_class(receive(), request_id)
                elapsed = (time.perf_counter() - start) * 1000
                if index:  # First sample is warm-up; it still contributes accuracy.
                    latencies.append(elapsed)
                predictions.append(predicted)
                evidence.append({"file": sample["file"], "prediction": predicted, "latency_ms": elapsed})
            process.stdin.write(b'{"type":"done"}\n')
            process.stdin.flush()
            process.wait(timeout=remaining())
            if process.returncode:
                raise RuntimeError("inference exited unsuccessfully")
        if usage.error:
            raise RuntimeError(usage.error)
        metrics, matrix = classification_metrics([sample["label"] for sample in data["samples"]], predictions)
        metrics.update(latency_ms=sum(latencies) / len(latencies) if latencies else evidence[0]["latency_ms"],
                       peak_vram_mb=usage.peak)
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        output.chmod(0o700)
        (output / "result.json").write_text(json.dumps({"episodes": [metrics]}, allow_nan=False))
        (output / "evidence.json").write_text(json.dumps({"manifest_sha256": manifest_digest,
            "package_sha256": digest_file(package), "gpu": usage.query("name"),
            "vram_sampling_seconds": 0.1, "warmup_samples": 1, "sample_count": len(predictions),
            "confusion_matrix": matrix, "predictions": evidence}, allow_nan=False))
        return metrics
    finally:
        if process:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)
            if process.stdin:
                process.stdin.close()
        usage.stopped.set()
        if usage.thread.ident:
            usage.thread.join(timeout=6)
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
        # Keep error logs private to the organizer; never include them in API errors.
        if (root / "inference.log").exists():
            output.mkdir(parents=True, exist_ok=True, mode=0o700)
            output.chmod(0o700)
            shutil.copyfile(root / "inference.log", output / "inference.log")
        public.cleanup()
        private.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpu", required=True)
    parser.add_argument("--seconds", type=int, default=1800)
    parser.add_argument("--memory-mb", type=int, default=6144)
    parser.add_argument("--cpus", type=int, default=4)
    args = parser.parse_args()
    evaluate(args.package, args.dataset.resolve(), args.manifest_sha256, args.output, gpu=args.gpu,
             seconds=args.seconds, memory_mb=args.memory_mb, cpus=args.cpus,
             model_hosts=os.environ.get("EVALUATION_MODEL_HOSTS", "huggingface.co,*.huggingface.co,*.hf.co").split(","))


if __name__ == "__main__":
    main()

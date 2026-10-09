"""Public aggregate classification diagnostics; never publish sample IDs/labels."""
import json
import urllib.request
from pathlib import Path
from uuid import uuid4


MAX_EVIDENCE_BYTES = 25 * 1024 * 1024


def upload(base, job, name, path):
    if not path.is_file() or path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ValueError("trusted evidence exceeds 25 MB")
    boundary = "uestc" + uuid4().hex
    prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"name\"\r\n\r\n{name}\r\n"
              f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
              "Content-Type: application/octet-stream\r\n\r\n").encode()
    request = urllib.request.Request(base + f"/evaluation-worker/runs/{job['id']}/evidence",
        data=prefix + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode(), method="POST",
        headers={"User-Agent": "Mozilla/5.0 UESTC-EvaluationCheck", "X-Evaluation-Lease": job["lease_token"],
                 "Content-Type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def classification_report(matrix):
    if (not isinstance(matrix, list) or len(matrix) != 40
            or any(not isinstance(row, list) or len(row) != 40 or any(type(v) is not int or v < 0 for v in row) for row in matrix)):
        raise ValueError("expected a trusted 40-class confusion matrix")
    classes = []
    for label, row in enumerate(matrix):
        support = sum(row)
        predicted = sum(values[label] for values in matrix)
        precision = row[label] / predicted if predicted else 0
        recall = row[label] / support if support else 0
        classes.append({"class_id": label, "support": support, "precision": precision,
                        "recall": recall, "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0})
    return {"confusion_matrix": matrix, "per_class": classes}


def publish_classification(base, job, log_root):
    output = Path(log_root) / job["id"]
    for index in range(job["config"]["resources"]["episodes"]):
        evidence = json.loads((output / str(index) / "evidence.json").read_text())
        public = output / f"classification-{index + 1}.json"
        public.write_text(json.dumps(classification_report(evidence["confusion_matrix"])))
        upload(base, job, f"scene-{index + 1}-classification.json", public)

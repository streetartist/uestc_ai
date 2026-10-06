#!/usr/bin/env bash
# Run as root in AutoDL's CPU/no-card mode. GPU acceptance is a separate step.
# The original base-image-l2t43iu6uk uses PyTorch 2.0.0 / CUDA 11.8.
# Upload the backend tree to /opt/uestc-classification/backend first.
set -eu
test "$(id -u)" = 0
test -f /opt/uestc-classification/backend/classification_worker.py
chmod 755 /opt/uestc-classification
mkdir -p /root/autodl-tmp/uestc-evaluation/datasets /root/autodl-tmp/uestc-evaluation/logs
chmod 700 /root/autodl-tmp /root/autodl-tmp/uestc-evaluation
# Only traversal is enabled, so the unprivileged interpreter can use AutoDL's
# installed conda libraries. Private datasets and credentials remain mode 0700/0600.
chmod 711 /root
"${CLASSIFICATION_BASE_PYTHON:-/root/miniconda3/bin/python}" -m venv --system-site-packages /opt/uestc-classification/runtime
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  /opt/uestc-classification/runtime/bin/python -m pip install --no-cache-dir \
  --index-url "${CLASSIFICATION_PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}" \
  psutil==6.1.1 safetensors==0.5.3
chmod -R a+rX /opt/uestc-classification/backend /opt/uestc-classification/runtime
CLASSIFICATION_TORCH_VERSION="${CLASSIFICATION_TORCH_VERSION:-2.0.0+cu118}" \
  /opt/uestc-classification/runtime/bin/python - <<'PY'
import ctypes
import os
import torch

ctypes.CDLL("libseccomp.so.2")
assert torch.__version__ == os.environ["CLASSIFICATION_TORCH_VERSION"], "unexpected PyTorch build"
assert torch.version.cuda, "install a CUDA-enabled PyTorch build for later GPU evaluation"
print("CPU build verified; GPU acceptance pending:", torch.__version__, torch.version.cuda)
PY
/opt/uestc-classification/runtime/bin/python /opt/uestc-classification/backend/build_classification_image_manifest.py
/opt/uestc-classification/runtime/bin/python - <<'PY'
import sys
sys.path.insert(0, "/opt/uestc-classification/backend")
from classification_worker import verify_runtime
verify_runtime()
print("Evaluator runtime seal verified")
PY
# The saved image deliberately contains no platform token or formal dataset.

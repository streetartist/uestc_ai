#!/usr/bin/env bash
# Install the CUHK-X competition tools in CPU/no-card mode. No datasets or tokens
# are copied into the distributable image; those remain in private file storage.
set -eu
test "$(id -u)" = 0
CLASSIFICATION_BASE_PYTHON="${CLASSIFICATION_BASE_PYTHON:-/root/miniconda3/bin/python}"
CLASSIFICATION_PIP_INDEX_URL="${CLASSIFICATION_PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export CLASSIFICATION_BASE_PYTHON CLASSIFICATION_PIP_INDEX_URL
CLASSIFICATION_TORCH_VERSION="$($CLASSIFICATION_BASE_PYTHON -c 'import torch; print(torch.__version__)')"
export CLASSIFICATION_TORCH_VERSION
bash /opt/uestc-classification/autodl-classification-install.sh

# Preserve the base PyTorch/NumPy combination, with a fixed image decoder so
# participant preparation and organizer preparation produce the same clips.
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  /opt/uestc-classification/runtime/bin/python -m pip install --no-cache-dir \
  --index-url "$CLASSIFICATION_PIP_INDEX_URL" Pillow==10.4.0
mkdir -p /opt/uestc-classification/starter
cp /opt/uestc-classification/backend/evaluation_adapters/classification_example/* \
  /opt/uestc-classification/starter/
chmod -R a+rX /opt/uestc-classification/backend /opt/uestc-classification/starter
/opt/uestc-classification/runtime/bin/python - <<'PY'
import sys
sys.path.insert(0, "/opt/uestc-classification/backend")
from prepare_cuhkx_depth import load_classes, load_split
from evaluation_adapters.classification_example.network import TinyDepthNet
import numpy as np
import torch
from PIL import __version__ as pillow_version

root = "/opt/uestc-classification/"
assert len(load_classes(root + "depth-class-mapping.csv")) == 40
split = load_split(root + "depth-subject-split.json")
with torch.inference_mode():
    scores = TinyDepthNet()(torch.zeros(1, 1, 16, 64, 64))
assert scores.shape == (1, 40) and torch.isfinite(scores).all()
print("CPU baseline inference passed; GPU and real-data acceptance pending")
print("Preparation dependencies:", "Pillow", pillow_version, "NumPy", np.__version__)
print("Candidate subject counts:", {k: len(v) for k, v in split.items()})
PY
/opt/uestc-classification/runtime/bin/python \
  /opt/uestc-classification/backend/build_classification_image_manifest.py
echo "CPU competition tools installed. Back up raw data outside the system image before image/save."

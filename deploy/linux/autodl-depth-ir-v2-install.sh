#!/usr/bin/env bash
# Build a fresh v2 image; use the same installer for training and judging.
set -eu
test "$(id -u)" = 0
test -f /opt/uestc-classification/backend/classification_worker.py
test ! -e /opt/uestc-classification/runtime
test ! -e /opt/uestc-classification/starter
: "${CLASSIFICATION_BASELINE_ZIP:?Set the verified v2 starter ZIP path}"
: "${CLASSIFICATION_BASELINE_SHA256:?Set its published SHA256}"
export CLASSIFICATION_BASELINE_ZIP CLASSIFICATION_BASELINE_SHA256
CLASSIFICATION_BASE_PYTHON="${CLASSIFICATION_BASE_PYTHON:-/root/miniconda3/bin/python}"
"$CLASSIFICATION_BASE_PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 10), "v2 baseline requires Python 3.10"'
chmod 755 /opt/uestc-classification
chmod 711 /root
"$CLASSIFICATION_BASE_PYTHON" -m venv /opt/uestc-classification/runtime
PY=/opt/uestc-classification/runtime/bin/python
"$PY" -m pip install --index-url https://download.pytorch.org/whl/cu118 torch==2.5.1 torchvision==0.20.1
"$PY" -m pip install numpy==2.2.6 Pillow==12.2.0 opencv-python-headless==4.13.0.92 \
  psutil==6.1.1 safetensors==0.5.3 timm==1.0.27 ultralytics-thop==2.0.18 \
  'matplotlib>=3.3' 'PyYAML>=5.3.1' 'requests>=2.23' 'scipy>=1.4.1' 'polars>=0.20'
# Use the tested headless OpenCV build; avoid installing a second GUI cv2 wheel.
"$PY" -m pip install --no-deps ultralytics==8.3.228
"$PY" - <<'PY'
import ctypes, hashlib, os, stat, zipfile
from pathlib import Path, PurePosixPath
import torch, torchvision, numpy, PIL, cv2, ultralytics
assert torch.__version__ == '2.5.1+cu118'
assert torchvision.__version__ == '0.20.1+cu118'
assert numpy.__version__ == '2.2.6' and PIL.__version__ == '12.2.0'
assert cv2.__version__ == '4.13.0' and ultralytics.__version__ == '8.3.228'
ctypes.CDLL('libseccomp.so.2')
package = Path(os.environ['CLASSIFICATION_BASELINE_ZIP'])
digest = hashlib.sha256()
with package.open('rb') as source:
    for chunk in iter(lambda: source.read(1024 * 1024), b''): digest.update(chunk)
assert digest.hexdigest() == os.environ['CLASSIFICATION_BASELINE_SHA256']
destination = Path('/opt/uestc-classification/starter')
with zipfile.ZipFile(package) as archive:
    names = set()
    for entry in archive.infolist():
        path = PurePosixPath(entry.filename)
        if (path.is_absolute() or '..' in path.parts or '\\' in entry.filename
                or entry.filename in names or entry.is_dir()
                or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)):
            raise ValueError('Invalid baseline ZIP entry')
        names.add(entry.filename)
    archive.extractall(destination)
print('v2 libraries and verified baseline installed; GPU acceptance follows image restore')
PY
chmod -R a+rX /opt/uestc-classification/backend /opt/uestc-classification/runtime /opt/uestc-classification/starter
"$PY" -m pip freeze > /opt/uestc-classification/environment-lock.txt
"$PY" /opt/uestc-classification/backend/build_classification_image_manifest.py --profile depth-ir-v2
"$PY" - <<'PY'
import sys
sys.path.insert(0, '/opt/uestc-classification/backend')
from classification_worker import verify_runtime
verify_runtime()
print('v2 runtime seal verified')
PY

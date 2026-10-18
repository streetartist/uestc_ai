"""Environment files shared by the depth/IR development and baseline packages."""

RUNTIME_FILES = {
    "requirements.txt": "numpy==2.2.6\nPillow==12.2.0\ntimm==1.0.27\nsafetensors==0.8.0\n",
    "requirements-crop.txt": "-r requirements.txt\nultralytics==8.3.228\n",
    "setup.sh": '''#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
"${PYTHON:-python3.10}" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu118
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python check_environment.py
''',
    "check_environment.py": '''import sys
import torch, torchvision, numpy, PIL, timm, safetensors
from timm.utils import ModelEmaV3
from raw_model import RawModel
print("Python:", sys.version.split()[0])
print("PyTorch:", torch.__version__, "torchvision:", torchvision.__version__)
print("NumPy:", numpy.__version__, "Pillow:", PIL.__version__)
print("timm:", timm.__version__, "safetensors:", safetensors.__version__)
if not torch.cuda.is_available():
    raise SystemExit("CUDA unavailable. Check the NVIDIA driver and the selected GPU instance.")
x = torch.ones(4, device="cuda")
assert x.sum().item() == 4
print("GPU:", torch.cuda.get_device_name(0))
print("Environment ready.")
''',
}

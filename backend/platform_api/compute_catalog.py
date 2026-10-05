"""Pro-specific options from the official API appendix, checked 2026-10-05.

These are documented configurations, not a live inventory or price feed.
Do not import ordinary instance or enterprise deployment IDs into this list.
"""
SOURCE = "https://www.autodl.com/docs/instance_pro_api/"

GPU_SPECS = [
    {"id": "4090D", "name": "RTX 4090 D", "memory_gb": 24},
    {"id": "v-48g", "name": "RTX 4090", "memory_gb": 48},
    {"id": "5090-p", "name": "RTX 5090", "memory_gb": 32},
    {"id": "pro6000-p", "name": "RTX PRO 6000", "memory_gb": 96},
    {"id": "h800", "name": "H800", "memory_gb": 80},
    {"id": "v-32g-p", "name": "RTX 4080 (S)", "memory_gb": 32},
    {"id": "v-48g-350w", "name": "RTX 3090", "memory_gb": 48},
]

PUBLIC_IMAGES = [
    {"id": "base-image-l2t43iu6uk", "name": "PyTorch 2.0 · Python 3.8 · Ubuntu 20.04", "cuda_v_from": 118},
    {"id": "base-image-mbr2n4urrc", "name": "Miniconda · Python 3.8 · Ubuntu 20.04", "cuda_v_from": 116},
    {"id": "base-image-l374uiucui", "name": "PyTorch 1.11 · Python 3.8 · Ubuntu 20.04", "cuda_v_from": 113},
    {"id": "base-image-u9r24vthlk", "name": "PyTorch 1.10 · Python 3.8 · Ubuntu 20.04", "cuda_v_from": 113},
    {"id": "base-image-12be412037", "name": "PyTorch 1.9 · Python 3.8 · Ubuntu 18.04", "cuda_v_from": 111},
    {"id": "base-image-uxeklgirir", "name": "TensorFlow 2.9 · Python 3.8 · Ubuntu 20.04", "cuda_v_from": 112},
    {"id": "base-image-0gxqmciyth", "name": "TensorFlow 2.5 · Python 3.8 · Ubuntu 18.04", "cuda_v_from": 112},
    {"id": "base-image-4bpg0tt88l", "name": "TensorFlow 1.15 · Python 3.8 · Ubuntu 18.04", "cuda_v_from": 114},
    {"id": "base-image-h041hn36yt", "name": "Miniconda · Python 3.8 · Ubuntu 18.04", "cuda_v_from": 111},
    {"id": "base-image-qkkhitpik5", "name": "Miniconda · Python 3.8 · Ubuntu 18.04", "cuda_v_from": 102},
    {"id": "base-image-7bn8iqhkb5", "name": "Miniconda / 图形环境 · Python 3.8 · Ubuntu 18.04", "cuda_v_from": 113},
    {"id": "base-image-k0vep6kyq8", "name": "Miniconda · Python 3.6 · Ubuntu 16.04", "cuda_v_from": 90},
    {"id": "base-image-l2843iu23k", "name": "TensorRT 8.5 · Python 3.8 · Ubuntu 20.04", "cuda_v_from": 118},
]

DATA_CENTERS = [
    {"id": "westDC3", "name": "西北区"},
    {"id": "beijingDC2", "name": "北京区"},
]

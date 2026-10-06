import numpy as np
import torch
import torch.nn.functional as F
from safetensors.torch import load_file
from network import TinyDepthNet


class Model:
    def __init__(self, config, device):
        self.device = device
        self.scale = float(config.get("depth_scale", 1))
        if self.scale <= 0:
            raise ValueError("depth_scale must be positive")
        self.model = TinyDepthNet().to(device)
        self.model.load_state_dict(load_file(config["weights_path"], device=device), strict=True)
        self.model.eval()

    @torch.inference_mode()
    def predict(self, sample_path):
        clip = np.load(sample_path, allow_pickle=False)
        value = torch.from_numpy(clip.astype(np.float32)).permute(1, 0, 2, 3).unsqueeze(0).to(self.device)
        value = F.interpolate(value / self.scale, size=(16, 64, 64), mode="trilinear", align_corners=False)
        return self.model(value)[0].float().cpu().tolist()

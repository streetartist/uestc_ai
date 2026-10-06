import torch.nn as nn


class TinyDepthNet(nn.Module):
    """Small 3D-CNN starter. Train on the authorized public training split."""
    def __init__(self):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Conv3d(1, 16, 3, padding=1), nn.ReLU(), nn.MaxPool3d(2),
            nn.Conv3d(16, 32, 3, padding=1), nn.ReLU(), nn.MaxPool3d(2),
            nn.Conv3d(32, 64, 3, padding=1), nn.ReLU(), nn.AdaptiveAvgPool3d(1),
            nn.Flatten(), nn.Linear(64, 40),
        )

    def forward(self, value):
        return self.layers(value)

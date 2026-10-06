"""Starter training only; the organizer never runs this during evaluation."""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from safetensors.torch import save_file
from network import TinyDepthNet


class DepthDataset(torch.utils.data.Dataset):
    def __init__(self, directory, scale):
        self.directory, self.scale = directory, scale
        self.manifest = json.loads((directory / "manifest.json").read_text())
        if len(self.manifest["classes"]) != 40 or self.manifest["modality"] != "depth" or not self.manifest["samples"]:
            raise ValueError("use the public non-RGB split with 40-class mapping")

    def __len__(self):
        return len(self.manifest["samples"])

    def __getitem__(self, index):
        sample = self.manifest["samples"][index]
        clip = np.load(self.directory / sample["file"], allow_pickle=False)
        value = torch.from_numpy(clip.astype(np.float32)).permute(1, 0, 2, 3).unsqueeze(0)
        value = F.interpolate(value / self.scale, size=(16, 64, 64), mode="trilinear", align_corners=False)
        return value[0], sample["label"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--training-data", type=Path, required=True)
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--output", default="model.safetensors")
    p.add_argument("--depth-scale", type=float, default=1, help="Converted colorized-depth luminance is already in [0,1]")
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--validation-data", type=Path)
    args = p.parse_args()
    if args.epochs < 1 or args.batch_size < 1 or args.workers < 0 or args.depth_scale <= 0:
        p.error("epochs, batch size and depth scale must be positive")
    torch.manual_seed(42)
    torch.set_num_threads(4)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = TinyDepthNet().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    dataset = DepthDataset(args.training_data, args.depth_scale)
    loader = torch.utils.data.DataLoader(dataset, batch_size=args.batch_size, shuffle=True,
        num_workers=args.workers, pin_memory=device == "cuda", generator=torch.Generator().manual_seed(42))
    validation = DepthDataset(args.validation_data, args.depth_scale) if args.validation_data else None
    if validation and validation.manifest["classes"] != dataset.manifest["classes"]:
        p.error("training and validation class mappings differ")
    validation_loader = torch.utils.data.DataLoader(validation, batch_size=args.batch_size,
        num_workers=args.workers) if validation else None
    for epoch in range(args.epochs):
        started, total_loss, count = time.monotonic(), 0.0, 0
        model.train()
        for values, labels in loader:
            values, labels = values.to(device), labels.to(device)
            loss = F.cross_entropy(model(values), labels)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * len(labels)
            count += len(labels)
        elapsed = time.monotonic() - started
        report = {"epoch": epoch + 1, "training_loss": total_loss / count,
                  "training_seconds": elapsed, "samples_per_second": count / elapsed}
        if validation_loader:
            model.eval()
            correct, total = 0, 0
            with torch.inference_mode():
                for values, labels in validation_loader:
                    predictions = model(values.to(device)).argmax(1).cpu()
                    correct += int((predictions == labels).sum())
                    total += len(labels)
            report["validation_accuracy"] = 100 * correct / total
        print(json.dumps(report), flush=True)
    save_file({key:value.detach().cpu().contiguous() for key,value in model.state_dict().items()}, args.output)


if __name__ == "__main__": main()

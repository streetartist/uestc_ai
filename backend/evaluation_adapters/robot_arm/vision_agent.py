"""Minimal RGB baseline: color localization + calibrated ray / table-plane.

No object-state access, no model API. Assumes known cube sizes and table z=.8.
For a stronger entry use feedback localization, occlusion handling and retries.
"""
import base64
import io
import numpy as np
from PIL import Image


class Agent:
    def reset(self, goal):
        self.goal, self.turn, self.red, self.green = goal, 0, None, None

    def locate(self, observation, color, height):
        image = np.asarray(Image.open(io.BytesIO(base64.b64decode(observation["images_jpeg_base64"]["agentview"])))).astype(float)
        r, g, b = image[:, :, 0], image[:, :, 1], image[:, :, 2]
        mask = (r > g * 1.5) & (r > b * 1.5) & (r > 55) if color == "red" else (g > r * 1.3) & (g > b * 1.3) & (g > 40)
        rows, cols = np.where(mask)
        if len(rows) < 5:
            return None
        calibration = observation["camera_calibration"]["agentview"]
        intrinsic = np.asarray(calibration["intrinsic"])
        transform = np.asarray(calibration["camera_to_world"])
        ray = transform[:3, :3] @ np.linalg.solve(intrinsic, [float(np.median(cols)), float(np.median(rows)), 1])
        origin = transform[:3, 3]
        if abs(ray[2]) < 1e-6:
            return None
        xy = (origin + ray * ((height - origin[2]) / ray[2]))[:2]
        return np.clip(xy, -.15, .15).tolist()

    def act(self, observation):
        if self.red is None:
            self.red = self.locate(observation, "red", .82)
            self.green = self.locate(observation, "green", .825)
            if self.red is None or self.green is None:
                return {"target": [0, 0, 1.15], "gripper": -1, "repeat": 20}
        destination = self.goal.get("target_xy", self.green)
        phases = [([*self.red, 1.02], -1, 4), ([*self.red, .835], -1, 4),
                  ([*self.red, .835], 1, 3), ([*self.red, 1.04], 1, 4)]
        if self.goal["task"] != "lift":
            z = .88 if self.goal["task"] == "stack" else .84
            phases.extend([([*destination, 1.04], 1, 4), ([*destination, z], 1, 4),
                           ([*destination, z], -1, 3), ([*destination, 1.04], -1, 3)])
        self.turn += 1
        total = 0
        for target, gripper, duration in phases:
            total += duration
            if self.turn <= total:
                return {"target": target, "gripper": gripper, "repeat": 20}
        return {"target": [*destination, 1.04], "gripper": -1, "repeat": 20}

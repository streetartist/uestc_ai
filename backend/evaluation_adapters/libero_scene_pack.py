"""Load organizer-owned scene assets from the controller image, never the agent ZIP."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

ASSET_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")


def load_scene_asset(scene, root=None):
    import numpy as np

    asset_id = scene.get("scene_asset")
    if not isinstance(asset_id, str) or not ASSET_ID.fullmatch(asset_id):
        raise ValueError("Invalid LIBERO scene asset identifier")
    root = Path(root or os.environ.get("LIBERO_SCENE_PACK", "/opt/uestc-libero-scenes")).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != 1:
        raise ValueError("Unsupported LIBERO scene pack version")
    asset = manifest["scenes"][asset_id]
    for field in ("suite", "task_id", "task_name"):
        if asset.get(field) != scene[field]:
            raise ValueError("LIBERO scene asset identity mismatch")

    def checked_file(field):
        path = (root / asset[field]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("LIBERO scene asset path leaves its trusted pack")
        if hashlib.sha256(path.read_bytes()).hexdigest() != asset[field + "_sha256"]:
            raise ValueError("LIBERO scene asset checksum mismatch")
        return path

    bddl = checked_file("bddl")
    states_path = checked_file("states")
    match = re.search(r"\(:language\s+([^)]*)\)", bddl.read_text(encoding="utf-8"))
    if not match:
        raise ValueError("LIBERO scene has no language instruction")
    instruction = " ".join(match.group(1).split())
    if instruction != asset.get("instruction"):
        raise ValueError("LIBERO instruction differs from the scene manifest")
    if re.sub(r"[^a-z0-9]+", "_", instruction.lower()).strip("_") != asset["task_name"]:
        raise ValueError("LIBERO task name differs from its instruction")
    states = np.load(states_path, allow_pickle=False)
    if (not isinstance(states, np.ndarray) or states.ndim != 2 or not 1 <= len(states) <= 50
            or states.dtype.kind not in "fi" or not np.isfinite(states).all()):
        raise ValueError("Invalid LIBERO initial states")
    index = scene["init_state_id"]
    if type(index) is not int or not 0 <= index < len(states):
        raise ValueError("LIBERO initial state is unavailable")
    return bddl, states[index], instruction

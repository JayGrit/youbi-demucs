from __future__ import annotations

from pathlib import Path


MYSQL_CONFIG = {
    "host": "120.53.92.66",
    "port": 3306,
    "user": "hoshuuch",
    "password": "490229",
    "database": "youbi",
}

WORKFOLDER = Path("/Users/hoshuuch/Money/YouBi/workfolder").expanduser()
POLL_INTERVAL_SECONDS = 10

REPO_ROOT = Path("/Users/hoshuuch/Money/YouDub-webui").expanduser()

DEVICE = "auto"
DEMUCS_MODEL = "htdemucs_ft"
DEMUCS_SHIFTS = 1


def device() -> str:
    if DEVICE.lower() != "auto":
        return DEVICE
    return device_candidates()[0]


def device_candidates() -> list[str]:
    if DEVICE.lower() != "auto":
        selected_key = DEVICE.lower()
        if selected_key == "mps":
            return ["mps", "cpu"]
        if selected_key.startswith("cuda"):
            return [DEVICE, "cpu"]
        return [DEVICE]

    candidates: list[str] = []
    try:
        import torch

        if torch.cuda.is_available():
            candidates.append("cuda")
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            candidates.append("mps")
    except Exception:
        pass
    candidates.append("cpu")
    return candidates

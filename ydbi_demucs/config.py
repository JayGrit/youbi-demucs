from __future__ import annotations

import os
import tempfile
from pathlib import Path


MYSQL_CONFIG = {
    "host": "120.53.92.66",
    "port": 3306,
    "user": "hoshuuch",
    "password": "490229",
    "database": "youbi",
}

WORKFOLDER = Path("/Users/hoshuuch/Money/YouBi/workfolder").expanduser()
WORK_DIR = Path(os.environ.get("YDBI_DEMUCS_WORK_DIR", Path(tempfile.gettempdir()) / "ydbi" / "demucs")).expanduser()
POLL_INTERVAL_SECONDS = 10

STORAGE_BACKEND = "minio"
MINIO_ENDPOINT = "http://120.53.92.66:9000"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
MINIO_BUCKET = "ydbi"
MINIO_PUBLIC_BASE = "/minio"
MINIO_FULL_BASE_URL = "https://120.53.92.66/minio"
MINIO_SECURE = False

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(os.environ.get("YDBI_REPO_ROOT", SERVICE_ROOT)).expanduser()
DEMUCS_REPO = os.environ.get("YDBI_DEMUCS_REPO")

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


def task_work_dir(task_id: str) -> Path:
    path = WORK_DIR / task_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def demucs_source_candidates() -> list[Path]:
    candidates: list[Path] = []
    if DEMUCS_REPO:
        candidates.append(Path(DEMUCS_REPO).expanduser())
    candidates.append(REPO_ROOT / "submodule" / "demucs")
    legacy_repo_root = Path("/Users/hoshuuch/Money/YouDub-webui").expanduser()
    if legacy_repo_root != REPO_ROOT:
        candidates.append(legacy_repo_root / "submodule" / "demucs")
    return candidates

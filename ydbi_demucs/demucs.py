from __future__ import annotations

import sys
from pathlib import Path

from .config import DEMUCS_MODEL, DEMUCS_SHIFTS, REPO_ROOT, device_candidates


def _demucs_shifts() -> int:
    return max(0, DEMUCS_SHIFTS)


def _demucs_model() -> str:
    return DEMUCS_MODEL


def separate_audio(video_file: Path, session: Path) -> tuple[Path, Path]:
    demucs_path = REPO_ROOT / "submodule" / "demucs"
    if not demucs_path.exists():
        raise RuntimeError("Demucs submodule is missing. Run: git submodule update --init --recursive")
    sys.path.insert(0, str(demucs_path))

    from demucs.api import Separator, save_audio

    media_dir = session / "media"
    vocals_file = media_dir / "audio_vocals.wav"
    bgm_file = media_dir / "audio_bgm.wav"
    if vocals_file.exists() and bgm_file.exists():
        return vocals_file, bgm_file

    last_error: Exception | None = None
    for runtime_device in device_candidates():
        try:
            separator = Separator(
                model=_demucs_model(),
                device=runtime_device,
                progress=True,
                shifts=_demucs_shifts(),
            )
            _, separated = separator.separate_audio_file(str(video_file))
            break
        except Exception as exc:
            last_error = exc
            if runtime_device == "cpu":
                raise
    else:
        raise RuntimeError(f"Demucs failed to separate audio: {last_error}")

    vocals = separated["vocals"]
    bgm = None
    for stem, source in separated.items():
        if stem == "vocals":
            continue
        bgm = source if bgm is None else bgm + source

    save_audio(vocals, str(vocals_file), samplerate=separator.samplerate)
    save_audio(bgm, str(bgm_file), samplerate=separator.samplerate)
    return vocals_file, bgm_file

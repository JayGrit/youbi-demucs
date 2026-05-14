from __future__ import annotations

import sys
from pathlib import Path

from .config import DEMUCS_MODEL, DEMUCS_SHIFTS, demucs_source_candidates, device_candidates


def _demucs_shifts() -> int:
    return max(0, DEMUCS_SHIFTS)


def _demucs_model() -> str:
    return DEMUCS_MODEL


def _load_demucs_api():
    checked_paths = []
    for demucs_path in demucs_source_candidates():
        checked_paths.append(str(demucs_path))
        if demucs_path.exists():
            sys.path.insert(0, str(demucs_path))
            break

    try:
        from demucs.api import Separator, save_audio
    except ModuleNotFoundError as exc:
        if exc.name != "demucs":
            raise
        checked = "\n  - ".join(checked_paths)
        raise RuntimeError(
            "Demucs is not available. Run: git submodule update --init --recursive\n"
            "or set YDBI_DEMUCS_REPO to an existing demucs checkout.\n"
            f"Checked:\n  - {checked}"
        ) from exc

    return Separator, save_audio


def separate_audio(video_file: Path, session: Path) -> tuple[Path, Path]:
    Separator, save_audio = _load_demucs_api()

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

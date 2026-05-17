from __future__ import annotations

import gc
import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import (
    DEMUCS_JOBS,
    DEMUCS_LONG_AUDIO_JOBS,
    DEMUCS_LONG_AUDIO_MODEL,
    DEMUCS_LONG_AUDIO_SECONDS,
    DEMUCS_LONG_AUDIO_SEGMENT_SECONDS,
    DEMUCS_LONG_AUDIO_SHIFTS,
    DEMUCS_MODEL,
    DEMUCS_SEGMENT_SECONDS,
    DEMUCS_SHIFTS,
    demucs_source_candidates,
    device_candidates,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DemucsRuntime:
    model: str
    shifts: int
    segment: float | None
    jobs: int
    long_audio: bool
    duration_seconds: float | None


def _audio_duration_seconds(audio_file: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(audio_file),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        duration = json.loads(result.stdout).get("format", {}).get("duration")
        return float(duration) if duration is not None else None
    except Exception:
        pass

    try:
        import torchaudio

        info = torchaudio.info(str(audio_file))
        if info.sample_rate and info.num_frames:
            return info.num_frames / info.sample_rate
    except Exception:
        log.debug("failed to inspect audio duration: %s", audio_file, exc_info=True)
    return None


def _runtime_for(audio_file: Path) -> DemucsRuntime:
    duration_seconds = _audio_duration_seconds(audio_file)
    long_audio = duration_seconds is not None and duration_seconds > DEMUCS_LONG_AUDIO_SECONDS
    if long_audio:
        return DemucsRuntime(
            model=DEMUCS_LONG_AUDIO_MODEL,
            shifts=max(0, DEMUCS_LONG_AUDIO_SHIFTS),
            segment=DEMUCS_LONG_AUDIO_SEGMENT_SECONDS,
            jobs=max(0, DEMUCS_LONG_AUDIO_JOBS),
            long_audio=True,
            duration_seconds=duration_seconds,
        )
    return DemucsRuntime(
        model=DEMUCS_MODEL,
        shifts=max(0, DEMUCS_SHIFTS),
        segment=DEMUCS_SEGMENT_SECONDS,
        jobs=max(0, DEMUCS_JOBS),
        long_audio=False,
        duration_seconds=duration_seconds,
    )


def _release_torch_cache() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
            torch.mps.empty_cache()
    except Exception:
        log.debug("failed to release torch cache", exc_info=True)


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
    runtime = _runtime_for(video_file)
    log.info(
        "demucs runtime audio=%s duration_seconds=%s long_audio=%s model=%s shifts=%s segment=%s jobs=%s",
        video_file,
        runtime.duration_seconds,
        runtime.long_audio,
        runtime.model,
        runtime.shifts,
        runtime.segment,
        runtime.jobs,
    )

    media_dir = session / "media"
    vocals_file = media_dir / "audio_vocals.wav"
    bgm_file = media_dir / "audio_bgm.wav"
    if vocals_file.exists() and bgm_file.exists():
        return vocals_file, bgm_file

    last_error: Exception | None = None
    for runtime_device in device_candidates():
        try:
            separator = Separator(
                model=runtime.model,
                device=runtime_device,
                progress=True,
                shifts=runtime.shifts,
                segment=runtime.segment,
                jobs=runtime.jobs,
            )
            mix, separated = separator.separate_audio_file(str(video_file))
            break
        except Exception as exc:
            last_error = exc
            _release_torch_cache()
            if runtime_device == "cpu":
                raise
    else:
        raise RuntimeError(f"Demucs failed to separate audio: {last_error}")

    vocals = separated["vocals"]
    if runtime.long_audio:
        for stem in list(separated):
            if stem != "vocals":
                del separated[stem]
        _release_torch_cache()
        bgm = mix - vocals
    else:
        bgm = None
        for stem, source in separated.items():
            if stem == "vocals":
                continue
            bgm = source if bgm is None else bgm + source

    save_audio(vocals, str(vocals_file), samplerate=separator.samplerate)
    save_audio(bgm, str(bgm_file), samplerate=separator.samplerate)
    del separated, vocals, bgm, mix, separator
    _release_torch_cache()
    return vocals_file, bgm_file

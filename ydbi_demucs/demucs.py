from __future__ import annotations

import gc
import json
import logging
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import (
    DEMUCS_JOBS,
    DEMUCS_LONG_AUDIO_CHUNK_SECONDS,
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
            "or configure DEMUCS_REPO to an existing demucs checkout.\n"
            f"Checked:\n  - {checked}"
        ) from exc

    return Separator, save_audio


def _max_model_segment_seconds(model: object) -> float | None:
    max_allowed_segment = getattr(model, "max_allowed_segment", None)
    if max_allowed_segment is not None:
        try:
            value = float(max_allowed_segment)
            return value if value != float("inf") else None
        except (TypeError, ValueError):
            pass

    segment = getattr(model, "segment", None)
    if segment is None:
        return None
    try:
        return float(segment)
    except (TypeError, ValueError):
        return None


def _clamp_segment_to_model(separator: object, requested_segment: float | None) -> float | None:
    if requested_segment is None:
        return None

    max_segment = _max_model_segment_seconds(separator.model)
    if max_segment is None or requested_segment <= max_segment:
        return requested_segment

    log.warning(
        "demucs segment %.3fs exceeds model limit %.3fs; using %.3fs",
        requested_segment,
        max_segment,
        max_segment,
    )
    separator.update_parameter(segment=max_segment)
    return max_segment


def _run_ffmpeg(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _concat_wavs(chunks: list[Path], output_file: Path, list_file: Path) -> None:
    def quote_concat_path(path: Path) -> str:
        return str(path.resolve()).replace("'", "'\\''")

    list_file.parent.mkdir(parents=True, exist_ok=True)
    list_file.write_text(
        "".join(f"file '{quote_concat_path(chunk)}'\n" for chunk in chunks),
        encoding="utf-8",
    )
    _run_ffmpeg(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c:a",
            "pcm_s16le",
            str(output_file),
        ]
    )


def _extract_audio_chunk(
    source_file: Path,
    chunk_file: Path,
    start_seconds: float,
    duration_seconds: float,
) -> None:
    chunk_file.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-t",
            f"{duration_seconds:.3f}",
            "-i",
            str(source_file),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(chunk_file),
        ]
    )


def _separate_long_audio(
    separator: object,
    save_audio,
    video_file: Path,
    session: Path,
    runtime: DemucsRuntime,
    vocals_file: Path,
    bgm_file: Path,
) -> None:
    if runtime.duration_seconds is None:
        raise RuntimeError("long audio duration is required for chunked demucs separation")

    chunk_seconds = max(60.0, DEMUCS_LONG_AUDIO_CHUNK_SECONDS)
    chunk_count = math.ceil(runtime.duration_seconds / chunk_seconds)
    chunks_dir = session / "demucs_chunks"
    vocals_chunks: list[Path] = []
    bgm_chunks: list[Path] = []
    log.info(
        "demucs long audio chunking audio=%s duration_seconds=%.3f chunk_seconds=%.3f chunks=%s",
        video_file,
        runtime.duration_seconds,
        chunk_seconds,
        chunk_count,
    )

    for index in range(chunk_count):
        start = index * chunk_seconds
        duration = min(chunk_seconds, runtime.duration_seconds - start)
        if duration <= 0:
            break

        chunk_file = chunks_dir / f"source_{index:05d}.wav"
        chunk_vocals = chunks_dir / f"vocals_{index:05d}.wav"
        chunk_bgm = chunks_dir / f"bgm_{index:05d}.wav"
        log.info(
            "demucs long audio chunk %s/%s start=%.3f duration=%.3f",
            index + 1,
            chunk_count,
            start,
            duration,
        )
        _extract_audio_chunk(video_file, chunk_file, start, duration)
        mix, separated = separator.separate_audio_file(str(chunk_file))
        vocals = separated["vocals"]
        for stem in list(separated):
            if stem != "vocals":
                del separated[stem]
        bgm = mix - vocals

        save_audio(vocals, str(chunk_vocals), samplerate=separator.samplerate)
        save_audio(bgm, str(chunk_bgm), samplerate=separator.samplerate)
        vocals_chunks.append(chunk_vocals)
        bgm_chunks.append(chunk_bgm)
        del separated, vocals, bgm, mix
        _release_torch_cache()

    if not vocals_chunks or not bgm_chunks:
        raise RuntimeError(f"Demucs produced no chunks for audio: {video_file}")

    _concat_wavs(vocals_chunks, vocals_file, chunks_dir / "vocals_concat.txt")
    _concat_wavs(bgm_chunks, bgm_file, chunks_dir / "bgm_concat.txt")


def separate_audio(video_file: Path, session: Path) -> tuple[Path, Path]:
    Separator, save_audio = _load_demucs_api()
    runtime = _runtime_for(video_file)
    candidate_devices = device_candidates()
    log.info(
        "demucs runtime audio=%s duration_seconds=%s long_audio=%s model=%s shifts=%s segment=%s jobs=%s device_candidates=%s",
        video_file,
        runtime.duration_seconds,
        runtime.long_audio,
        runtime.model,
        runtime.shifts,
        runtime.segment,
        runtime.jobs,
        ",".join(candidate_devices),
    )

    media_dir = session / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    vocals_file = media_dir / "audio_vocals.wav"
    bgm_file = media_dir / "audio_bgm.wav"
    if vocals_file.exists() and bgm_file.exists():
        return vocals_file, bgm_file

    last_error: Exception | None = None
    for runtime_device in candidate_devices:
        try:
            log.info("demucs trying device=%s model=%s audio=%s", runtime_device, runtime.model, video_file)
            separator = Separator(
                model=runtime.model,
                device=runtime_device,
                progress=True,
                shifts=runtime.shifts,
                segment=runtime.segment,
                jobs=runtime.jobs,
            )
            _clamp_segment_to_model(separator, runtime.segment)
            if runtime.long_audio:
                _separate_long_audio(
                    separator,
                    save_audio,
                    video_file,
                    session,
                    runtime,
                    vocals_file,
                    bgm_file,
                )
                del separator
                _release_torch_cache()
                return vocals_file, bgm_file
            mix, separated = separator.separate_audio_file(str(video_file))
            break
        except Exception as exc:
            last_error = exc
            log.warning("demucs failed on device=%s; trying next fallback if available: %s", runtime_device, exc)
            _release_torch_cache()
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
    del separated, vocals, bgm, mix, separator
    _release_torch_cache()
    return vocals_file, bgm_file

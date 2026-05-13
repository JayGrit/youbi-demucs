from __future__ import annotations

from dataclasses import dataclass


PENDING = "pending"
READY = "ready"
RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"
SKIPPED = "skipped"


@dataclass(frozen=True)
class Stage:
    name: str
    table: str
    next_name: str | None
    next_table: str | None


STAGES: tuple[Stage, ...] = (
    Stage("downloader", "yd_downloader", "demucs", "yd_demucs"),
    Stage("demucs", "yd_demucs", "whisper", "yd_whisper"),
    Stage("whisper", "yd_whisper", "translator", "yd_translator"),
    Stage("translator", "yd_translator", "speaker", "yd_speaker"),
    Stage("speaker", "yd_speaker", "combiner", "yd_combiner"),
    Stage("combiner", "yd_combiner", "uploader", "yd_uploader"),
    Stage("uploader", "yd_uploader", None, None),
)

BY_NAME = {stage.name: stage for stage in STAGES}


def stage_for(name: str) -> Stage:
    try:
        return BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"Unknown stage: {name}") from exc


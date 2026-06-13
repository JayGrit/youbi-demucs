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
    Stage("downloader", "downloader", "demucs", "demucs"),
    Stage("demucs", "demucs", "whisper", "whisper"),
    Stage("whisper", "whisper", "translator", "translator"),
    Stage("translator", "translator", "speaker", "speaker"),
    Stage("speaker", "speaker", "combiner", "combiner"),
    Stage("combiner", "combiner", "uploader", "uploader"),
    Stage("uploader", "uploader", None, None),
)

BY_NAME = {stage.name: stage for stage in STAGES}


def stage_for(name: str) -> Stage:
    try:
        return BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"Unknown stage: {name}") from exc


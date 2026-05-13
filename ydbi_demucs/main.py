from __future__ import annotations

import logging
from pathlib import Path

from ydbi_demucs import db
from ydbi_demucs.demucs import separate_audio
from ydbi_demucs.worker import run_polling_worker

log = logging.getLogger(__name__)


def _ensure_existing_file(path: str | Path, field_name: str) -> Path:
    file_path = Path(path)
    if not file_path.exists() or file_path.stat().st_size == 0:
        raise FileNotFoundError(f"{field_name} does not exist or is empty: {file_path}")
    return file_path


def handle(row: dict) -> dict[str, str]:
    task_id = row["task_id"]
    video_source = _ensure_existing_file(row["video_source_path"], "video_source_path")
    session = db.session_path_for(task_id)
    vocals = session / "media" / "audio_vocals.wav"
    bgm = session / "media" / "audio_bgm.wav"

    log.info("demucs task=%s video=%s session=%s", task_id, video_source, session)
    vocals, bgm = separate_audio(video_source, session)

    log.info("demucs outputs task=%s vocals=%s bgm=%s", task_id, vocals, bgm)
    db.set_whisper_audio_vocals_path(task_id, str(vocals))
    db.set_speaker_audio_vocals_path(task_id, str(vocals))
    db.set_combiner_audio_bgm_path(task_id, str(bgm))
    return {"audio_vocals_path": str(vocals), "audio_bgm_path": str(bgm)}


def main() -> None:
    run_polling_worker("demucs", handle)


if __name__ == "__main__":
    main()

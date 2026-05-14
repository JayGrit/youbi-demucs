from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from ydbi_demucs import db
from ydbi_demucs.config import task_work_dir
from ydbi_demucs.demucs import separate_audio
from ydbi_demucs.storage import download, upload
from ydbi_demucs.worker import run_polling_worker

log = logging.getLogger(__name__)


def _ensure_existing_file(path: str | Path, field_name: str) -> Path:
    file_path = Path(path)
    if not file_path.exists() or file_path.stat().st_size == 0:
        raise FileNotFoundError(f"{field_name} does not exist or is empty: {file_path}")
    return file_path


def _download_destination(session: Path, source_ref: str) -> Path:
    suffix = Path(source_ref.split("?", 1)[0]).suffix or ".audio"
    return session / "media" / f"audio_source{suffix}"


def _audio_input_for(row: dict, session: Path) -> Path:
    task_id = row["task_id"]
    local_audio = row.get("audio_source_path")
    downloader_operator = db.downloader_operator_for(task_id)
    current_operator = db.current_operator()

    if downloader_operator == current_operator and local_audio:
        return _ensure_existing_file(local_audio, "audio_source_path")

    audio_source_url = str(row.get("audio_source_url") or "").strip()
    if not audio_source_url:
        if local_audio:
            return _ensure_existing_file(local_audio, "audio_source_path")
        raise FileNotFoundError(f"audio_source_url is missing for task: {task_id}")

    destination = _download_destination(session, audio_source_url)
    log.info(
        "demucs task=%s downloading audio source from minio url=%s destination=%s",
        task_id,
        audio_source_url,
        destination,
    )
    return download(audio_source_url, destination)


def handle(row: dict) -> dict[str, str]:
    task_id = row["task_id"]
    session = task_work_dir(task_id)
    audio_source = _audio_input_for(row, session)

    log.info("demucs task=%s audio=%s session=%s", task_id, audio_source, session)
    vocals, bgm = separate_audio(audio_source, session)
    vocals_url = upload(
        vocals,
        f"{task_id}/demucs/audio_vocals{vocals.suffix}",
        mimetypes.guess_type(vocals.name)[0] or "audio/wav",
    )
    bgm_url = upload(
        bgm,
        f"{task_id}/demucs/audio_bgm{bgm.suffix}",
        mimetypes.guess_type(bgm.name)[0] or "audio/wav",
    )

    log.info(
        "demucs outputs task=%s vocals=%s vocals_url=%s bgm=%s bgm_url=%s",
        task_id,
        vocals,
        vocals_url,
        bgm,
        bgm_url,
    )
    return {
        "audio_vocals_path": str(vocals),
        "audio_vocals_url": vocals_url,
        "audio_bgm_path": str(bgm),
        "audio_bgm_url": bgm_url,
    }


def main() -> None:
    run_polling_worker("demucs", handle)


if __name__ == "__main__":
    main()

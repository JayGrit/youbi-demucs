from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import mysql.connector

from .config import MYSQL_CONFIG

TABLE = "video_info"

COLUMNS: dict[str, str] = {
    "source_url": "TEXT",
    "submitter_video_id": "BIGINT UNSIGNED",
    "metadata_url": "TEXT",
    "video_source_url": "TEXT",
    "audio_source_path": "TEXT",
    "audio_source_url": "TEXT",
    "audio_vocals_path": "TEXT",
    "audio_vocals_url": "TEXT",
    "audio_bgm_path": "TEXT",
    "audio_bgm_url": "TEXT",
    "asr_json_path": "TEXT",
    "translation_json_path": "TEXT",
    "target_language": "VARCHAR(16)",
    "tts_segments_dir": "TEXT",
    "audio_dubbing_url": "TEXT",
    "timings_json_path": "TEXT",
    "final_video_url": "TEXT",
}

_schema_ready = False


def _row_value(row: Any, index: int = 0) -> Any:
    if isinstance(row, Mapping):
        return list(row.values())[index]
    return row[index]


def _connect():
    return mysql.connector.connect(**MYSQL_CONFIG)


def _ensure_schema_with_cursor(cur) -> None:
    global _schema_ready
    _schema_ready = True


def ensure_schema() -> None:
    global _schema_ready
    _schema_ready = True


def upsert(task_id: str, fields: Mapping[str, Any], cur=None) -> None:
    values = {key: value for key, value in fields.items() if key in COLUMNS and value is not None}
    if not values:
        return

    def execute(cursor) -> None:
        _ensure_schema_with_cursor(cursor)
        names = list(values)
        columns = ", ".join(["task_id", *names])
        placeholders = ", ".join(["%s"] * (len(names) + 1))
        updates = ", ".join(f"{name} = VALUES({name})" for name in names)
        cursor.execute(
            f"""
            INSERT INTO {TABLE} ({columns})
            VALUES ({placeholders})
            ON DUPLICATE KEY UPDATE {updates}
            """,
            [task_id, *[values[name] for name in names]],
        )

    if cur is not None:
        if not _schema_ready:
            ensure_schema()
        execute(cur)
        return

    with _connect() as conn:
        cursor = conn.cursor()
        execute(cursor)
        conn.commit()


def get(task_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with _connect() as conn:
        cur = conn.cursor(dictionary=True)
        columns = ", ".join(["task_id", *COLUMNS])
        cur.execute(f"SELECT {columns} FROM {TABLE} WHERE task_id = %s", (task_id,))
        return cur.fetchone()


def merge_into(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return row
    info = get(str(row["task_id"]))
    if not info:
        return row
    row["video_info"] = info
    for key in COLUMNS:
        value = info.get(key)
        if value is not None:
            row[key] = value
    return row

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import mysql.connector

from .config import MYSQL_CONFIG

TABLE = "task_info"
TASK_FIELDS = {"submitter_video_id", "topic", "task_type"}
TASK_SOURCE_FIELDS = {
    "source_url",
    "source_language",
    "native_subtitle_language",
    "metadata_url",
    "video_source_url",
    "audio_source_url",
    "source_thumbnail_url",
    "source_cover_url",
    "source_subtitles_url",
    "source_subtitle_srt_url",
    "source_subtitle_txt_url",
    "native_subtitle_txt_url",
    "native_subtitle_srt_url",
    "source_title",
    "source_description",
    "source_uploader",
    "source_webpage_url",
    "source_tags_json",
    "source_duration_seconds",
}
TASK_OPTIONS_FIELDS = {
    "has_background_audio",
    "has_native_subtitle",
    "has_manual_english_subtitle",
    "has_manual_chinese_subtitle",
    "has_other_manual_subtitle",
    "has_auto_english_subtitle",
    "has_auto_chinese_subtitle",
    "has_other_auto_subtitle",
    "target_language",
    "need_dubbing",
    "need_subtitle",
    "need_separation",
    "upload_platforms",
}
TASK_PROCESSING_FIELDS = {
    "audio_vocals_url",
    "audio_bgm_url",
    "audio_dubbing_url",
    "audio_mixed_url",
    "tts_segments_dir",
    "asr_json_path",
    "translation_json_path",
    "timings_json_path",
    "dialogue_srt_url",
    "source_transcript_txt_url",
}
TASK_METADATA_FIELDS = {
    "upload_title",
    "upload_description",
    "upload_tags",
    "cover_text",
    "final_cover_url",
    "cover_4_3",
    "cover_3_4",
    "final_video_url",
}
PRODUCT_PPT_FIELDS = {
    "ppt_dialogue_json",
    "ppt_dialogue_json_url",
    "ppt_dialogue_audio_url",
}
SOURCE_METADATA_COLUMNS = {"title"}
COLUMNS = {
    *TASK_FIELDS,
    *TASK_SOURCE_FIELDS,
    *TASK_OPTIONS_FIELDS,
    *TASK_PROCESSING_FIELDS,
    *TASK_METADATA_FIELDS,
    *PRODUCT_PPT_FIELDS,
}
FIELD_TABLES = {
    **{key: "task" for key in TASK_FIELDS},
    **{key: "task_source" for key in TASK_SOURCE_FIELDS},
    **{key: "task_options" for key in TASK_OPTIONS_FIELDS},
    **{key: "task_processing" for key in TASK_PROCESSING_FIELDS},
    **{key: "task_metadata" for key in TASK_METADATA_FIELDS},
    **{key: "product_ppt" for key in PRODUCT_PPT_FIELDS},
    "title": "task_source",
}
FIELD_SELECTS = {
    "submitter_video_id": "t.submitter_video_id",
    "topic": "t.topic",
    "task_type": "t.task_type",
    **{key: f"ts.{key}" for key in TASK_SOURCE_FIELDS},
    **{key: f"opts.{key}" for key in TASK_OPTIONS_FIELDS},
    **{key: f"proc.{key}" for key in TASK_PROCESSING_FIELDS},
    **{key: f"meta.{key}" for key in TASK_METADATA_FIELDS},
    **{key: f"ppt.{key}" for key in PRODUCT_PPT_FIELDS},
    "title": "ts.source_title",
}
TABLE_JOINS = {
    "task_source": "LEFT JOIN task_source ts ON ts.task_id = t.id",
    "task_options": "LEFT JOIN task_options opts ON opts.task_id = t.id",
    "task_processing": "LEFT JOIN task_processing proc ON proc.task_id = t.id",
    "task_metadata": "LEFT JOIN task_metadata meta ON meta.task_id = t.id",
    "product_ppt": "LEFT JOIN product_ppt ppt ON ppt.task_id = t.id",
}
MINIO_COVER_URL_COLUMNS = [
    "final_cover_url",
    "cover_4_3",
    "cover_3_4",
    "source_cover_url",
    "source_thumbnail_url",
]


def _connect():
    return mysql.connector.connect(**MYSQL_CONFIG)


def _quote_identifier(name: str) -> str:
    return f"`{name.replace('`', '``')}`"


def _row_value(row: Any, index: int = 0) -> Any:
    if isinstance(row, Mapping):
        return list(row.values())[index]
    return row[index]


def _ensure_schema_with_cursor(cur) -> None:
    return


def ensure_schema() -> None:
    return


def _split_fields(fields: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    unknown = sorted(set(fields) - COLUMNS)
    if unknown:
        raise ValueError(f"unknown task_info field(s): {', '.join(unknown)}")
    values = {key: value for key, value in fields.items() if value is not None}
    return {
        "task": {key: value for key, value in values.items() if key in TASK_FIELDS},
        "task_source": {key: value for key, value in values.items() if key in TASK_SOURCE_FIELDS},
        "task_options": {key: value for key, value in values.items() if key in TASK_OPTIONS_FIELDS},
        "task_processing": {key: value for key, value in values.items() if key in TASK_PROCESSING_FIELDS},
        "task_metadata": {key: value for key, value in values.items() if key in TASK_METADATA_FIELDS},
        "product_ppt": {key: value for key, value in values.items() if key in PRODUCT_PPT_FIELDS},
    }


def _upsert_child(cursor, table: str, task_id: str, values: Mapping[str, Any]) -> None:
    if not values:
        return
    names = list(values)
    columns = ", ".join(["task_id", *[_quote_identifier(name) for name in names]])
    placeholders = ", ".join(["%s"] * (len(names) + 1))
    updates = ", ".join(f"{_quote_identifier(name)} = VALUES({_quote_identifier(name)})" for name in names)
    cursor.execute(
        f"""
        INSERT INTO {table} ({columns})
        VALUES ({placeholders})
        ON DUPLICATE KEY UPDATE {updates}
        """,
        [task_id, *[values[name] for name in names]],
    )


def upsert(task_id: str, fields: Mapping[str, Any], cur=None) -> None:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        raise ValueError("task_id is required")
    groups = _split_fields(dict(fields or {}))
    if not any(groups.values()):
        return

    def execute(cursor) -> None:
        task_values = groups["task"]
        if task_values:
            assignments = ", ".join(f"{_quote_identifier(name)} = %s" for name in task_values)
            cursor.execute(
                f"UPDATE task SET {assignments} WHERE id = %s",
                [*[task_values[name] for name in task_values], normalized_task_id],
            )
        for table in ("task_source", "task_options", "task_processing", "task_metadata", "product_ppt"):
            _upsert_child(cursor, table, normalized_task_id, groups[table])

    if cur is not None:
        execute(cur)
        return

    with _connect() as conn:
        cursor = conn.cursor()
        execute(cursor)
        conn.commit()


def update_existing(task_id: str, fields: Mapping[str, Any], cur=None) -> bool:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        return False
    groups = _split_fields(dict(fields or {}))
    if not any(groups.values()):
        return False

    def execute(cursor) -> bool:
        updated = False
        task_values = groups["task"]
        if task_values:
            assignments = ", ".join(f"{_quote_identifier(name)} = %s" for name in task_values)
            cursor.execute(
                f"UPDATE task SET {assignments} WHERE id = %s",
                [*[task_values[name] for name in task_values], normalized_task_id],
            )
            updated = cursor.rowcount > 0 or updated
        for table in ("task_source", "task_options", "task_processing", "task_metadata", "product_ppt"):
            values = groups[table]
            if not values:
                continue
            assignments = ", ".join(f"{_quote_identifier(name)} = %s" for name in values)
            cursor.execute(
                f"UPDATE {table} SET {assignments} WHERE task_id = %s",
                [*[values[name] for name in values], normalized_task_id],
            )
            updated = cursor.rowcount > 0 or updated
        return updated

    if cur is not None:
        return execute(cur)

    with _connect() as conn:
        cursor = conn.cursor()
        updated = execute(cursor)
        conn.commit()
        return updated


def update_existing_many(rows: Mapping[str, Mapping[str, Any]]) -> tuple[int, int]:
    updated = 0
    skipped = 0
    with _connect() as conn:
        cur = conn.cursor()
        for task_id, fields in dict(rows or {}).items():
            if update_existing(str(task_id), fields, cur=cur):
                updated += 1
            else:
                skipped += 1
        conn.commit()
    return updated, skipped


def _normalize_fields(fields: Iterable[str] | None) -> list[str]:
    if fields is None:
        return sorted(COLUMNS | set(SOURCE_METADATA_COLUMNS))
    normalized = []
    seen = set()
    unknown = []
    for field in fields:
        key = str(field or "").strip()
        if not key:
            continue
        if key not in FIELD_SELECTS:
            unknown.append(key)
            continue
        if key not in seen:
            normalized.append(key)
            seen.add(key)
    if unknown:
        raise ValueError(f"unknown task_info field(s): {', '.join(sorted(unknown))}")
    return normalized


def get(task_id: str, fields: Iterable[str] | None = None) -> dict[str, Any] | None:
    selected_fields = _normalize_fields(fields)
    select_columns = ["t.id AS task_id"]
    select_columns.extend(f"{FIELD_SELECTS[field]} AS {_quote_identifier(field)}" for field in selected_fields)
    needed_tables = {FIELD_TABLES[field] for field in selected_fields}
    joins = [TABLE_JOINS[name] for name in TABLE_JOINS if name in needed_tables]
    with _connect() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            f"""
            SELECT {', '.join(select_columns)}
            FROM task t
            {' '.join(joins)}
            WHERE t.id = %s
            """,
            (task_id,),
        )
        return cur.fetchone()


def count_url_references(url: str, *, excluding_task_id: str | None = None) -> int:
    value = str(url or "").strip()
    if not value:
        return 0
    with _connect() as conn:
        cur = conn.cursor()
        predicates = [
            "meta.final_cover_url = %s",
            "meta.cover_4_3 = %s",
            "meta.cover_3_4 = %s",
            "ts.source_cover_url = %s",
            "ts.source_thumbnail_url = %s",
        ]
        params: list[Any] = [value] * len(predicates)
        task_predicate = ""
        if excluding_task_id:
            task_predicate = "AND t.id <> %s"
            params.append(str(excluding_task_id))
        cur.execute(
            f"""
            SELECT COUNT(*)
            FROM task t
            LEFT JOIN task_source ts ON ts.task_id = t.id
            LEFT JOIN task_metadata meta ON meta.task_id = t.id
            WHERE ({' OR '.join(predicates)})
              {task_predicate}
            """,
            params,
        )
        row = cur.fetchone()
        return int(_row_value(row) or 0)


def merge_into(row: dict[str, Any] | None, fields: Iterable[str] | None = None) -> dict[str, Any] | None:
    if not row:
        return row
    task_id = str(row.get("task_id") or row.get("id") or "").strip()
    if not task_id:
        return row
    info = get(task_id, fields=fields)
    if not info:
        return row
    row["task_info"] = info
    for key in _normalize_fields(fields):
        value = info.get(key)
        if value is not None:
            row[key] = value
    return row

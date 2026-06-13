from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import mysql.connector

from . import video_info
from .config import MYSQL_CONFIG
from .stages import FAILED, READY, RUNNING, SUCCESS, stage_for

HEARTBEAT_TABLE = "service_heartbeat"
SUBMISSION_TABLE = "downloader_submission"
UPLOADER_ACCOUNT_TABLE = "uploader_account"
UPLOAD_SUBMISSION_TABLES = (
    "uploader_task_bilibili",
    "uploader_task_douyin",
    "uploader_task_xiaohongshu",
    "uploader_task_shipinhao",
    "uploader_task_kuaishou",
    "uploader_task_jinritoutiao",
)
HEARTBEAT_DEVICE_COLUMNS = ("Macbook Air M4", "Macmini M2", "LPXB", "MY_HP", "LPXB_HP", "TXY")
OPERATOR_COLUMN = "operator"
OPERATOR_COLUMN_DEFINITION = "VARCHAR(128) NULL"
STAGE_RUNNING_TIMEOUT_SECONDS = 2 * 60 * 60
_heartbeat_schema_ready = False


def connect():
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    return conn


def _dict_cursor(conn):
    return conn.cursor(dictionary=True)


def _quote_identifier(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"


def _staged_row_value(row: Any, index: int = 0) -> Any:
    if isinstance(row, Mapping):
        return list(row.values())[index]
    return row[index]


def _staged_table_exists_cur(cur, table: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (table,),
    )
    row = cur.fetchone()
    return bool(row and int(_staged_row_value(row)) > 0)


def _staged_column_exists_cur(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (table, column),
    )
    row = cur.fetchone()
    return bool(row and int(_staged_row_value(row)) > 0)


def _ensure_staged_account_columns_cur(cur) -> bool:
    if not _staged_table_exists_cur(cur, UPLOADER_ACCOUNT_TABLE):
        return False
    if not _staged_column_exists_cur(cur, UPLOADER_ACCOUNT_TABLE, "staged_running_count"):
        cur.execute(
            f"""
            ALTER TABLE {UPLOADER_ACCOUNT_TABLE}
            ADD COLUMN staged_running_count INT NOT NULL DEFAULT 0
            """
        )
    if not _staged_column_exists_cur(cur, UPLOADER_ACCOUNT_TABLE, "staged_failed_count"):
        cur.execute(
            f"""
            ALTER TABLE {UPLOADER_ACCOUNT_TABLE}
            ADD COLUMN staged_failed_count INT NOT NULL DEFAULT 0
            """
        )
    return True


def _task_has_upload_submission_cur(cur, task_id: str, account_key: str) -> bool:
    if not task_id or not account_key:
        return False
    for table in UPLOAD_SUBMISSION_TABLES:
        if not _staged_table_exists_cur(cur, table):
            continue
        cur.execute(
            f"""
            SELECT 1
            FROM {table}
            WHERE task_id = %s AND account_key = %s
            LIMIT 1
            """,
            (task_id, account_key),
        )
        if cur.fetchone():
            return True
    return False


def _apply_staged_pipeline_failure_cur(cur, task_id: str, old_task_status: str | None) -> None:
    if str(old_task_status or "").strip().lower() == FAILED:
        return
    if not _ensure_staged_account_columns_cur(cur) or not _staged_table_exists_cur(cur, SUBMISSION_TABLE):
        return
    cur.execute(
        f"""
        SELECT type
        FROM {SUBMISSION_TABLE}
        WHERE task_id = %s
          AND status = %s
          AND NULLIF(type, '') IS NOT NULL
        FOR UPDATE
        """,
        (task_id, SUCCESS),
    )
    row = cur.fetchone()
    account_key = str(_staged_row_value(row) if row else "").strip()
    if not account_key or _task_has_upload_submission_cur(cur, task_id, account_key):
        return
    cur.execute(
        f"""
        UPDATE {UPLOADER_ACCOUNT_TABLE}
        SET staged_running_count = GREATEST(staged_running_count - 1, 0),
            staged_failed_count = staged_failed_count + 1,
            metrics_updated_at = NOW(),
            updated_at = NOW()
        WHERE account_key = %s
        """,
        (account_key,),
    )


def _first_column(row: Any) -> Any:
    if isinstance(row, Mapping):
        return next(iter(row.values()))
    return row[0]


def _heartbeat_device_column() -> str | None:
    device = os.environ.get("DEVICE", "").strip() or "Macbook Air M4"
    return device if device in HEARTBEAT_DEVICE_COLUMNS else None


def _operator_value() -> str:
    return os.environ.get("DEVICE", "").strip() or "Macbook Air M4"


def current_operator() -> str:
    return _operator_value()


def _ensure_operator_columns(cur, tables: tuple[str, ...]) -> None:
    return


def ensure_service_heartbeat_schema() -> None:
    global _heartbeat_schema_ready
    _heartbeat_schema_ready = True


def record_service_poll(stage_name: str) -> None:
    column = _heartbeat_device_column()
    if not column:
        return

    ensure_service_heartbeat_schema()
    quoted_column = _quote_identifier(column)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            INSERT INTO {HEARTBEAT_TABLE} (service_name, {quoted_column})
            VALUES (%s, NOW())
            ON DUPLICATE KEY UPDATE {quoted_column} = VALUES({quoted_column})
            """,
            (stage_name,),
        )
        conn.commit()


def get_task(task_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        cur = _dict_cursor(conn)
        cur.execute("SELECT * FROM task WHERE id = %s", (task_id,))
        task = cur.fetchone()
        if not task:
            return None
        task["video_info"] = video_info.get(task_id)
        return task


def downloader_operator_for(task_id: str) -> str | None:
    with connect() as conn:
        cur = _dict_cursor(conn)
        _ensure_operator_columns(cur, ("downloader",))
        cur.execute("SELECT `operator` FROM downloader WHERE task_id = %s", (task_id,))
        row = cur.fetchone()
        if not row:
            return None
        operator = row.get("operator")
        return str(operator).strip() if operator else None


def find_ready(stage_name: str) -> dict[str, Any] | None:
    stage = stage_for(stage_name)
    with connect() as conn:
        cur = _dict_cursor(conn)
        cur.execute(
            f"""
            SELECT s.*
            FROM {stage.table} s
            JOIN task t ON t.id = s.task_id
            WHERE s.status = %s
              AND t.status <> 'failed'
            ORDER BY s.task_id ASC
            LIMIT 1
            """,
            (READY,),
        )
        return video_info.merge_into(cur.fetchone())


def mark_running(stage_name: str, task_id: str) -> bool:
    stage = stage_for(stage_name)
    operator = _operator_value()
    with connect() as conn:
        cur = conn.cursor()
        _ensure_operator_columns(cur, ("task", stage.table))
        cur.execute(
            f"""
            UPDATE {stage.table}
            SET status = %s,
                started_at = COALESCE(started_at, NOW()),
                error_message = NULL,
                `operator` = %s
            WHERE task_id = %s AND status = %s
              AND EXISTS (
                  SELECT 1 FROM task t
                  WHERE t.id = %s AND t.status <> 'failed'
              )
            """,
            (RUNNING, operator, task_id, READY, task_id),
        )
        stage_updated = cur.rowcount == 1
        if stage_updated:
            cur.execute(
                """
                UPDATE task
                SET status = 'running',
                    current_stage = %s,
                    started_at = COALESCE(started_at, NOW()),
                    `operator` = %s
                WHERE id = %s
                """,
                (stage_name, operator, task_id),
            )
        conn.commit()
        return stage_updated


def recycle_stale_running(stage_name: str) -> int:
    stage = stage_for(stage_name)
    timeout_seconds = STAGE_RUNNING_TIMEOUT_SECONDS
    message = f"{stage_name} task timed out after {timeout_seconds}s; retrying"
    with connect() as conn:
        cur = conn.cursor()
        _ensure_operator_columns(cur, (stage.table,))
        cur.execute(
            f"""
            UPDATE {stage.table}
            SET status = %s,
                started_at = NULL,
                completed_at = NULL,
                error_message = %s,
                `operator` = NULL
            WHERE status = %s
              AND started_at IS NOT NULL
              AND TIMESTAMPDIFF(SECOND, started_at, NOW()) > %s
            """,
            (READY, message, RUNNING, timeout_seconds),
        )
        recycled = cur.rowcount
        conn.commit()
        return int(recycled)


def _update_stage_fields(stage_name: str, task_id: str, fields: Mapping[str, Any]) -> None:
    stage = stage_for(stage_name)
    assignments = ", ".join(f"{key} = %s" for key in fields)
    values = list(fields.values()) + [task_id]
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE {stage.table} SET {assignments} WHERE task_id = %s", values)
        conn.commit()


def set_whisper_audio_vocals_path(task_id: str, audio_vocals_path: str) -> None:
    video_info.upsert(task_id, {"audio_vocals_path": audio_vocals_path})


def set_speaker_audio_vocals_path(task_id: str, audio_vocals_path: str) -> None:
    video_info.upsert(task_id, {"audio_vocals_path": audio_vocals_path})


def set_combiner_audio_bgm_path(task_id: str, audio_bgm_path: str) -> None:
    video_info.upsert(task_id, {"audio_bgm_path": audio_bgm_path})


def session_path_for(task_id: str) -> Path:
    task = get_task(task_id)
    if not task:
        raise RuntimeError(f"Task not found: {task_id}")
    info = task.get("video_info") or {}
    session_path = info.get("session_path")
    if not session_path:
        raise RuntimeError(f"Task missing downloader session_path: {task_id}")
    return Path(session_path)


def mark_success(stage_name: str, task_id: str, outputs: Mapping[str, Any] | None = None) -> None:
    stage = stage_for(stage_name)
    fields = dict(outputs or {})
    stage_fields = {key: value for key, value in fields.items() if key not in video_info.COLUMNS}
    assignments = ["status = %s", "completed_at = NOW()", "error_message = NULL"]
    values: list[Any] = [SUCCESS]
    for key, value in stage_fields.items():
        assignments.append(f"{key} = %s")
        values.append(value)
    values.append(task_id)

    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM task WHERE id = %s", (task_id,))
        task_row = cur.fetchone()
        if not task_row or task_row[0] == "failed":
            conn.commit()
            return
        video_info.upsert(task_id, fields, cur)
        cur.execute(
            f"UPDATE {stage.table} SET {', '.join(assignments)} WHERE task_id = %s",
            values,
        )
        if stage.next_table:
            cur.execute(
                f"UPDATE {stage.next_table} SET status = %s WHERE task_id = %s AND status = 'pending'",
                (READY, task_id),
            )
            cur.execute(
                "UPDATE task SET current_stage = %s WHERE id = %s",
                (stage.next_name, task_id),
            )
        else:
            cur.execute(
                """
                UPDATE task
                SET status = 'success', current_stage = 'done', completed_at = NOW(), error_message = NULL
                WHERE id = %s
                """,
                (task_id,),
            )
        conn.commit()


def mark_failed(stage_name: str, task_id: str, message: str) -> None:
    stage = stage_for(stage_name)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM task WHERE id = %s FOR UPDATE", (task_id,))
        task_row = cur.fetchone()
        old_task_status = _staged_row_value(task_row) if task_row else None
        cur.execute(
            f"""
            UPDATE {stage.table}
            SET status = %s, error_message = %s, completed_at = NOW()
            WHERE task_id = %s
            """,
            (FAILED, message, task_id),
        )
        cur.execute(
            """
            UPDATE task
            SET status = 'failed', current_stage = %s, error_message = %s, completed_at = NOW()
            WHERE id = %s
            """,
            (stage_name, message, task_id),
        )
        _apply_staged_pipeline_failure_cur(cur, task_id, old_task_status)
        conn.commit()

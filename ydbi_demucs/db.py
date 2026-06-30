from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import mysql.connector

from . import video_info
from .config import MYSQL_CONFIG
from .service import FAILED, READY, RUNNING, SERVICE_NAME, SERVICE_TABLE, SUCCESS

HEARTBEAT_TABLE = "service_heartbeat"
SUBMISSION_TABLE = "downloader_submission"
UPLOADER_ACCOUNT_TABLE = "uploader_account"
UPLOAD_SUBMISSION_TABLES = (
    "uploader_task",
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


def _service_table_for(stage_name: str) -> str:
    if stage_name != SERVICE_NAME:
        raise ValueError(f"{SERVICE_NAME} service cannot handle stage: {stage_name}")
    return SERVICE_TABLE


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
    return False


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
    return


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
        cur.execute(
            """
            SELECT `operator`
            FROM distributor_task_stages
            WHERE task_id = %s AND stage_name = 'downloader' AND sub_stage = 'main'
            """,
            (task_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        operator = row.get("operator")
        return str(operator).strip() if operator else None


def find_ready(stage_name: str) -> dict[str, Any] | None:
    table = _service_table_for(stage_name)
    with connect() as conn:
        cur = _dict_cursor(conn)
        cur.execute(
            f"""
            SELECT s.*
            FROM {table} s
            JOIN task t ON t.id = s.task_id
            WHERE s.stage_name = %s
              AND s.status = %s
              AND t.status <> 'failed'
            ORDER BY s.task_id ASC
            LIMIT 1
            """,
            (stage_name, READY),
        )
        return video_info.merge_into(cur.fetchone())


def mark_running(stage_name: str, task_id: str) -> bool:
    table = _service_table_for(stage_name)
    operator = _operator_value()
    with connect() as conn:
        cur = conn.cursor()
        _ensure_operator_columns(cur, (table,))
        cur.execute(
            f"""
            UPDATE {table}
            SET status = %s,
                started_at = COALESCE(started_at, NOW()),
                error_message = NULL,
                `operator` = %s
            WHERE task_id = %s AND stage_name = %s AND sub_stage = 'main' AND status = %s
              AND EXISTS (
                  SELECT 1 FROM task t
                  WHERE t.id = %s AND t.status <> 'failed'
              )
            """,
            (RUNNING, operator, task_id, stage_name, READY, task_id),
        )
        stage_updated = cur.rowcount == 1
        if stage_updated:
            cur.execute(
                """
                UPDATE task
                SET status = 'running',
                    current_stage = %s,
                    started_at = COALESCE(started_at, NOW())
                WHERE id = %s
                """,
                (stage_name, task_id),
            )
        conn.commit()
        return stage_updated


def recycle_stale_running(stage_name: str) -> int:
    table = _service_table_for(stage_name)
    timeout_seconds = STAGE_RUNNING_TIMEOUT_SECONDS
    message = f"{stage_name} task timed out after {timeout_seconds}s; retrying"
    with connect() as conn:
        cur = conn.cursor()
        _ensure_operator_columns(cur, (table,))
        cur.execute(
            f"""
            UPDATE {table}
            SET status = %s,
                started_at = NULL,
                completed_at = NULL,
                error_message = %s,
                `operator` = NULL
            WHERE stage_name = %s
              AND status = %s
              AND started_at IS NOT NULL
              AND TIMESTAMPDIFF(SECOND, started_at, NOW()) > %s
            """,
            (READY, message, stage_name, RUNNING, timeout_seconds),
        )
        recycled = cur.rowcount
        conn.commit()
        return int(recycled)


def _update_stage_fields(stage_name: str, task_id: str, fields: Mapping[str, Any]) -> None:
    return
    table = _service_table_for(stage_name)
    assignments = ", ".join(f"{key} = %s" for key in fields)
    values = list(fields.values()) + [task_id]
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE {table} SET {assignments} WHERE task_id = %s", values)
        conn.commit()


def mark_success(stage_name: str, task_id: str, outputs: Mapping[str, Any] | None = None) -> None:
    table = _service_table_for(stage_name)
    fields = dict(outputs or {})
    stage_fields: dict[str, Any] = {}
    assignments = ["status = %s", "completed_at = NOW()", "error_message = NULL"]
    values: list[Any] = [SUCCESS]
    for key, value in stage_fields.items():
        assignments.append(f"{key} = %s")
        values.append(value)
    values.extend([task_id, stage_name])

    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM task WHERE id = %s", (task_id,))
        task_row = cur.fetchone()
        if not task_row:
            conn.commit()
            return
        # Another stage may fail while this stage is still running. Preserve
        # the task failure, but persist this stage's result and outputs.
        video_info.upsert(task_id, fields, cur)
        cur.execute(
            f"UPDATE {table} SET {', '.join(assignments)} WHERE task_id = %s AND stage_name = %s AND sub_stage = 'main'",
            values,
        )
        conn.commit()


def mark_failed(stage_name: str, task_id: str, message: str) -> None:
    table = _service_table_for(stage_name)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM task WHERE id = %s FOR UPDATE", (task_id,))
        task_row = cur.fetchone()
        old_task_status = _staged_row_value(task_row) if task_row else None
        cur.execute(
            f"""
            UPDATE {table}
            SET status = %s, error_message = %s, completed_at = NOW()
            WHERE task_id = %s AND stage_name = %s AND sub_stage = 'main'
            """,
            (FAILED, message, task_id, stage_name),
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

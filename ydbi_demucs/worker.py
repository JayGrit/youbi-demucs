from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from . import db
from .config import POLL_INTERVAL_SECONDS

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Mapping[str, Any] | None]


def _start_task_heartbeat(stage_name: str) -> threading.Event:
    stop_event = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_event.wait(POLL_INTERVAL_SECONDS):
            try:
                db.record_service_poll(stage_name)
            except Exception:
                log.exception("%s failed to update task heartbeat", stage_name)

    try:
        db.record_service_poll(stage_name)
    except Exception:
        log.exception("%s failed to update task heartbeat", stage_name)
    thread = threading.Thread(target=heartbeat_loop, name=f"{stage_name}-heartbeat", daemon=True)
    thread.start()
    return stop_event


def run_polling_worker(stage_name: str, handler: Handler) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("%s service started; polling every %ss", stage_name, POLL_INTERVAL_SECONDS)
    while True:
        try:
            db.record_service_poll(stage_name)
            recycled = db.recycle_stale_running(stage_name)
            if recycled:
                log.warning("%s recycled %d stale running task(s)", stage_name, recycled)
            row = db.find_ready(stage_name)
        except Exception:
            log.exception("%s failed to poll database; retrying in %ss", stage_name, POLL_INTERVAL_SECONDS)
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        if not row:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        task_id = row["task_id"]
        if not db.mark_running(stage_name, task_id):
            continue

        log.info("%s task %s started", stage_name, task_id)
        heartbeat_stop = _start_task_heartbeat(stage_name)
        try:
            outputs = handler(row) or {}
            db.mark_success(stage_name, task_id, outputs)
            log.info("%s task %s succeeded", stage_name, task_id)
        except Exception as exc:
            log.exception("%s task %s failed", stage_name, task_id)
            db.mark_failed(stage_name, task_id, str(exc))
        finally:
            heartbeat_stop.set()

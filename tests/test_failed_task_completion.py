from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ydbi_demucs import db


class FailedTaskCompletionTest(unittest.TestCase):
    def test_success_is_persisted_after_another_stage_failed_task(self) -> None:
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.fetchone.return_value = ("failed",)
        outputs = {"audio_vocals_url": "http://120.53.92.66:9000/ydbi/vocals.wav"}

        with (
            patch.object(db, "connect", return_value=conn),
            patch.object(db.task_info, "upsert") as upsert,
        ):
            db.mark_success("demucs", "task-1", outputs)

        statements = [call.args[0] for call in conn.cursor.return_value.execute.call_args_list]
        self.assertTrue(any("UPDATE demucs SET" in sql for sql in statements))
        upsert.assert_called_once_with("task-1", outputs, conn.cursor.return_value)


if __name__ == "__main__":
    unittest.main()

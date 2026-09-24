import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "syncthing-monitor.py"

spec = importlib.util.spec_from_file_location("syncthing_monitor", MODULE_PATH)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class NotificationQueueTests(unittest.TestCase):
    def test_queue_notice_appends_notification(self):
        state = {"pendingNotifications": []}

        monitor.queue_notice(
            state,
            "Test subject",
            "Test body",
        )

        self.assertEqual(len(state["pendingNotifications"]), 1)

        notice = state["pendingNotifications"][0]
        self.assertEqual(notice["subject"], "Test subject")
        self.assertEqual(notice["body"], "Test body")

        created = monitor.datetime.fromisoformat(notice["created"])
        self.assertIsNotNone(created.tzinfo)

    def test_queue_notice_preserves_fifo_order(self):
        state = {"pendingNotifications": []}

        monitor.queue_notice(state, "First", "First body")
        monitor.queue_notice(state, "Second", "Second body")

        self.assertEqual(
            [n["subject"] for n in state["pendingNotifications"]],
            ["First", "Second"],
        )

    def test_flush_notice_failure_keeps_notification_queued(self):
        notice = {
            "subject": "Test subject",
            "body": "Test body",
            "created": "2026-09-24T18:00:00+00:00",
        }
        state = {"pendingNotifications": [notice.copy()]}

        with patch.object(
            monitor,
            "send_notice",
            side_effect=RuntimeError("simulated delivery failure"),
        ):
            with patch.object(monitor, "atomic_save") as save:
                monitor.flush_notice(state)

        self.assertEqual(
            state["pendingNotifications"],
            [notice],
        )
        save.assert_not_called()

    def test_flush_notice_success_removes_first_notification_and_saves(self):
        first = {
            "subject": "First",
            "body": "First body",
            "created": "2026-09-24T18:00:00+00:00",
        }
        second = {
            "subject": "Second",
            "body": "Second body",
            "created": "2026-09-24T18:01:00+00:00",
        }
        state = {
            "pendingNotifications": [
                first.copy(),
                second.copy(),
            ]
        }

        with patch.object(monitor, "send_notice") as send:
            with patch.object(monitor, "atomic_save") as save:
                monitor.flush_notice(state)

        send.assert_called_once_with("First", "First body")

        self.assertEqual(
            state["pendingNotifications"],
            [second],
        )

        save.assert_called_once_with(state)


if __name__ == "__main__":
    unittest.main()

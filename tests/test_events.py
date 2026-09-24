import importlib.util
import unittest
from pathlib import Path
from unittest.mock import call, patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "syncthing-monitor.py"

spec = importlib.util.spec_from_file_location("syncthing_monitor", MODULE_PATH)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class EventBatchTests(unittest.TestCase):
    def test_process_event_batch_processes_events_in_order(self):
        state = {
            "lastEventId": 100,
            "pendingNotifications": [],
        }
        events = [
            {"id": 101, "type": "RemoteChangeDetected"},
            {"id": 102, "type": "RemoteChangeDetected"},
        ]

        with patch.object(monitor, "process_event") as process:
            with patch.object(monitor, "atomic_save"):
                monitor.process_event_batch(state, events)

        self.assertEqual(
            process.call_args_list,
            [
                call(state, events[0]),
                call(state, events[1]),
            ],
        )

        self.assertEqual(state["lastEventId"], 102)

    def test_process_event_batch_saves_after_each_event(self):
        state = {
            "lastEventId": 100,
            "pendingNotifications": [],
        }
        events = [
            {"id": 101, "type": "RemoteChangeDetected"},
            {"id": 102, "type": "RemoteChangeDetected"},
        ]

        saved_event_ids = []

        def capture_save(current_state):
            saved_event_ids.append(current_state["lastEventId"])

        with patch.object(monitor, "process_event"):
            with patch.object(
                monitor,
                "atomic_save",
                side_effect=capture_save,
            ) as save:
                monitor.process_event_batch(state, events)

        self.assertEqual(saved_event_ids, [101, 102])
        self.assertEqual(save.call_count, 2)

    def test_process_event_batch_stops_on_failure_without_advancing_cursor(self):
        state = {
            "lastEventId": 100,
            "pendingNotifications": [],
        }
        events = [
            {"id": 101, "type": "RemoteChangeDetected"},
            {"id": 102, "type": "RemoteChangeDetected"},
            {"id": 103, "type": "RemoteChangeDetected"},
        ]

        def process_side_effect(current_state, event):
            if event["id"] == 102:
                raise RuntimeError("simulated event failure")

        saved_event_ids = []

        def capture_save(current_state):
            saved_event_ids.append(current_state["lastEventId"])

        with patch.object(
            monitor,
            "process_event",
            side_effect=process_side_effect,
        ) as process:
            with patch.object(
                monitor,
                "atomic_save",
                side_effect=capture_save,
            ):
                monitor.process_event_batch(state, events)

        self.assertEqual(
            [call.args[1]["id"] for call in process.call_args_list],
            [101, 102],
        )
        self.assertEqual(saved_event_ids, [101])
        self.assertEqual(state["lastEventId"], 101)


if __name__ == "__main__":
    unittest.main()

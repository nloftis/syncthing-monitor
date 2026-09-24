import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "syncthing-monitor.py"

spec = importlib.util.spec_from_file_location("syncthing_monitor", MODULE_PATH)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class StatePersistenceTests(unittest.TestCase):
    def test_missing_state_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "monitor-state.json"

            with patch.object(monitor, "STATE_FILE", state_file):
                self.assertIsNone(monitor.load_state())

    def test_valid_object_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "monitor-state.json"
            expected = {"version": 4, "lastEventId": 123}
            state_file.write_text(json.dumps(expected))

            with patch.object(monitor, "STATE_FILE", state_file):
                self.assertEqual(monitor.load_state(), expected)

    def test_corrupt_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "monitor-state.json"
            state_file.write_text("{not valid json")

            with patch.object(monitor, "STATE_FILE", state_file):
                with self.assertRaises(RuntimeError):
                    monitor.load_state()

    def test_non_object_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "monitor-state.json"
            state_file.write_text("[]")

            with patch.object(monitor, "STATE_FILE", state_file):
                with self.assertRaises(RuntimeError):
                    monitor.load_state()

    def test_atomic_save_replaces_existing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "monitor-state.json"
            state_file.write_text(
                json.dumps({"version": 4, "lastEventId": 100})
            )
            new_state = {
                "version": 4,
                "lastEventId": 200,
                "pendingNotifications": [],
            }

            with patch.object(monitor, "STATE_FILE", state_file):
                monitor.atomic_save(new_state)

            self.assertEqual(
                json.loads(state_file.read_text()),
                new_state,
            )
    def test_atomic_save_failure_preserves_existing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "monitor-state.json"
            original_state = {"version": 4, "lastEventId": 100}
            state_file.write_text(json.dumps(original_state))

            new_state = {"version": 4, "lastEventId": 200}

            with patch.object(monitor, "STATE_FILE", state_file):
                with patch.object(
                    monitor.json,
                    "dump",
                    side_effect=OSError("simulated write failure"),
                ):
                    with self.assertRaises(OSError):
                        monitor.atomic_save(new_state)

            self.assertEqual(
                json.loads(state_file.read_text()),
                original_state,
            )

if __name__ == "__main__":
    unittest.main()

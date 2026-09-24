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

    def test_normalize_state_migrates_legacy_receive_only_booleans(self):
        folder_id = "test-folder"

        for legacy_value in (False, True):
            with self.subTest(legacy_value=legacy_value):
                state = {
                    "version": 4,
                    "syncthingStartTime": "example",
                    "lastEventId": 123,
                    "receiveOnly": {
                        folder_id: legacy_value,
                    },
                    "remoteDeletes": {},
                    "pendingNotifications": [],
                }

                with patch.object(
                    monitor,
                    "FOLDERS",
                    {folder_id: "Test Folder"},
                ):
                    normalized = monitor.normalize_state(state)

                self.assertIsNone(
                    normalized["receiveOnly"][folder_id]
                )

    def test_normalize_state_preserves_stage2_receive_only_state(self):
        folder_id = "test-folder"
        receive_only_state = {
            "receiveOnlyChangedFiles": 2,
            "receiveOnlyChangedDirectories": 0,
            "receiveOnlyChangedSymlinks": 0,
            "receiveOnlyChangedDeletes": 0,
            "receiveOnlyChangedBytes": 100,
            "receiveOnlyTotalItems": 2,
            "lastObservedCount": 2,
            "lastAlertedCount": 2,
            "lastAlertTime": "2026-09-24T20:00:00+00:00",
        }
        state = {
            "version": 4,
            "syncthingStartTime": "example",
            "lastEventId": 123,
            "receiveOnly": {
                folder_id: receive_only_state.copy(),
            },
            "remoteDeletes": {},
            "pendingNotifications": [],
        }

        with patch.object(monitor, "FOLDERS", {folder_id: "Test Folder"}):
            normalized = monitor.normalize_state(state)

        self.assertEqual(
            normalized["receiveOnly"][folder_id],
            receive_only_state,
        )

    def test_normalize_state_preserves_active_remote_delete_incident(self):
        folder_id = "test-folder"
        remote_delete_state = {
            "events": [
                [1790251200.0, "one.txt"],
                [1790251210.0, "two.txt"],
            ],
            "active": True,
            "burstTotal": 57,
            "firstTime": 1790251200.0,
            "lastTime": 1790251210.0,
            "samplePaths": ["one.txt", "two.txt"],
        }
        state = {
            "version": 4,
            "syncthingStartTime": "example",
            "lastEventId": 350,
            "receiveOnly": {folder_id: None},
            "remoteDeletes": {
                folder_id: remote_delete_state.copy(),
            },
            "pendingNotifications": [],
        }

        with patch.object(monitor, "FOLDERS", {folder_id: "Test Folder"}):
            normalized = monitor.normalize_state(state)

        self.assertEqual(
            normalized["remoteDeletes"][folder_id],
            remote_delete_state,
        )
        self.assertEqual(normalized["lastEventId"], 350)

    def test_fresh_state_has_no_receive_only_observation(self):
        folder_id = "test-folder"

        with patch.object(monitor, "FOLDERS", {folder_id: "Test Folder"}):
            state = monitor.fresh_state("example", 123)

        self.assertIsNone(state["receiveOnly"][folder_id])


if __name__ == "__main__":
    unittest.main()

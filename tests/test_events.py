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

    def test_process_event_batch_saves_once_after_batch(self):
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

        self.assertEqual(saved_event_ids, [102])
        self.assertEqual(save.call_count, 1)

    def test_process_event_batch_skips_malformed_data_and_continues(self):
        state = {
            "lastEventId": 100,
            "remoteDeletes": {},
            "pendingNotifications": [],
        }
        events = [
            {
                "id": 101,
                "type": "RemoteChangeDetected",
                "time": "2026-09-24T12:00:00Z",
                "data": None,
            },
            {
                "id": 102,
                "type": "RemoteChangeDetected",
                "time": "2026-09-24T12:00:01Z",
                "data": {
                    "action": "deleted",
                    "type": "file",
                    "folder": "monitored-folder",
                    "path": "valid.txt",
                },
            },
        ]

        with (
            patch.object(
                monitor,
                "FOLDERS",
                {"monitored-folder": "Monitored Folder"},
            ),
            patch.object(monitor, "atomic_save") as save,
        ):
            monitor.process_event_batch(state, events)

        self.assertEqual(state["lastEventId"], 102)
        self.assertEqual(
            state["remoteDeletes"]["monitored-folder"]["events"],
            [[1790251201.0, "valid.txt"]],
        )
        self.assertEqual(save.call_count, 1)

    def test_process_event_batch_skips_missing_time_and_continues(self):
        state = {
            "lastEventId": 100,
            "remoteDeletes": {},
            "pendingNotifications": [],
        }
        events = [
            {
                "id": 101,
                "type": "RemoteChangeDetected",
                "data": {
                    "action": "deleted",
                    "type": "file",
                    "folder": "monitored-folder",
                    "path": "malformed.txt",
                },
            },
            {
                "id": 102,
                "type": "RemoteChangeDetected",
                "time": "2026-09-24T12:00:01Z",
                "data": {
                    "action": "deleted",
                    "type": "file",
                    "folder": "monitored-folder",
                    "path": "valid.txt",
                },
            },
        ]

        with (
            patch.object(
                monitor,
                "FOLDERS",
                {"monitored-folder": "Monitored Folder"},
            ),
            patch.object(monitor, "atomic_save") as save,
        ):
            monitor.process_event_batch(state, events)

        self.assertEqual(state["lastEventId"], 102)
        self.assertEqual(
            state["remoteDeletes"]["monitored-folder"]["events"],
            [[1790251201.0, "valid.txt"]],
        )
        self.assertEqual(save.call_count, 1)

    def test_process_event_batch_skips_malformed_event_and_continues(self):
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
                raise ValueError("malformed event")

        with patch.object(
            monitor,
            "process_event",
            side_effect=process_side_effect,
        ) as process:
            with patch.object(monitor, "atomic_save") as save:
                monitor.process_event_batch(state, events)

        self.assertEqual(
            [call.args[1]["id"] for call in process.call_args_list],
            [101, 102, 103],
        )
        self.assertEqual(state["lastEventId"], 103)
        self.assertEqual(save.call_count, 1)


class RemoteDeletionTests(unittest.TestCase):
    def test_unmonitored_folder_is_ignored(self):
        state = {
            "remoteDeletes": {},
            "pendingNotifications": [],
        }
        event = {
            "id": 101,
            "type": "RemoteChangeDetected",
            "time": "2026-09-24T12:00:00Z",
            "data": {
                "action": "deleted",
                "type": "file",
                "folder": "unmonitored-folder",
                "path": "example.txt",
            },
        }

        with patch.object(
            monitor,
            "FOLDERS",
            {"monitored-folder": "Monitored Folder"},
        ):
            monitor.process_event(state, event)

        self.assertEqual(state["remoteDeletes"], {})
        self.assertEqual(state["pendingNotifications"], [])


    def test_remote_addition_is_ignored(self):
        state = {"remoteDeletes": {}, "pendingNotifications": []}
        event = {
            "id": 101,
            "type": "RemoteChangeDetected",
            "time": "2026-09-24T12:00:00Z",
            "data": {
                "action": "added",
                "type": "file",
                "folder": "monitored-folder",
                "path": "example.txt",
            },
        }

        with patch.object(
            monitor,
            "FOLDERS",
            {"monitored-folder": "Monitored Folder"},
        ):
            monitor.process_event(state, event)

        self.assertEqual(state["remoteDeletes"], {})

    def test_remote_modification_is_ignored(self):
        state = {"remoteDeletes": {}, "pendingNotifications": []}
        event = {
            "id": 101,
            "type": "RemoteChangeDetected",
            "time": "2026-09-24T12:00:00Z",
            "data": {
                "action": "modified",
                "type": "file",
                "folder": "monitored-folder",
                "path": "example.txt",
            },
        }

        with patch.object(
            monitor,
            "FOLDERS",
            {"monitored-folder": "Monitored Folder"},
        ):
            monitor.process_event(state, event)

        self.assertEqual(state["remoteDeletes"], {})

    def test_remote_directory_deletion_is_ignored(self):
        state = {"remoteDeletes": {}, "pendingNotifications": []}
        event = {
            "id": 101,
            "type": "RemoteChangeDetected",
            "time": "2026-09-24T12:00:00Z",
            "data": {
                "action": "deleted",
                "type": "dir",
                "folder": "monitored-folder",
                "path": "example-directory",
            },
        }

        with patch.object(
            monitor,
            "FOLDERS",
            {"monitored-folder": "Monitored Folder"},
        ):
            monitor.process_event(state, event)

        self.assertEqual(state["remoteDeletes"], {})

    def test_monitored_file_deletion_is_counted(self):
        state = {"remoteDeletes": {}, "pendingNotifications": []}
        event = {
            "id": 101,
            "type": "RemoteChangeDetected",
            "time": "2026-09-24T12:00:00Z",
            "data": {
                "action": "deleted",
                "type": "file",
                "folder": "monitored-folder",
                "path": "example.txt",
            },
        }

        with patch.object(
            monitor,
            "FOLDERS",
            {"monitored-folder": "Monitored Folder"},
        ):
            monitor.process_event(state, event)

        self.assertEqual(
            state["remoteDeletes"]["monitored-folder"]["events"],
            [[1790251200.0, "example.txt"]],
        )


    def test_deletions_outside_rolling_window_do_not_trigger_incident(self):
        state = {"remoteDeletes": {}, "pendingNotifications": []}

        def deletion(event_id, timestamp, path):
            return {
                "id": event_id,
                "type": "RemoteChangeDetected",
                "time": timestamp,
                "data": {
                    "action": "deleted",
                    "type": "file",
                    "folder": "monitored-folder",
                    "path": path,
                },
            }

        with (
            patch.object(
                monitor,
                "FOLDERS",
                {"monitored-folder": "Monitored Folder"},
            ),
            patch.object(monitor, "REMOTE_DELETE_THRESHOLD", 3),
            patch.object(monitor, "REMOTE_DELETE_WINDOW", 300),
        ):
            monitor.process_event(
                state,
                deletion(101, "2026-09-24T12:00:00Z", "one.txt"),
            )
            monitor.process_event(
                state,
                deletion(102, "2026-09-24T12:05:01Z", "two.txt"),
            )
            monitor.process_event(
                state,
                deletion(103, "2026-09-24T12:05:02Z", "three.txt"),
            )

        incident = state["remoteDeletes"]["monitored-folder"]
        self.assertFalse(incident["active"])
        self.assertEqual(len(incident["events"]), 2)
        self.assertEqual(state["pendingNotifications"], [])

    def test_incident_threshold_and_quiet_close_report_final_count(self):
        state = {"remoteDeletes": {}, "pendingNotifications": []}

        def deletion(event_id, second, path):
            return {
                "id": event_id,
                "type": "RemoteChangeDetected",
                "time": f"2026-09-24T12:00:{second:02d}Z",
                "data": {
                    "action": "deleted",
                    "type": "file",
                    "folder": "monitored-folder",
                    "path": path,
                },
            }

        with (
            patch.object(
                monitor,
                "FOLDERS",
                {"monitored-folder": "Monitored Folder"},
            ),
            patch.object(monitor, "REMOTE_DELETE_THRESHOLD", 3),
            patch.object(monitor, "REMOTE_DELETE_WINDOW", 300),
        ):
            monitor.process_event(state, deletion(101, 0, "one.txt"))
            monitor.process_event(state, deletion(102, 10, "two.txt"))

            self.assertEqual(state["pendingNotifications"], [])

            monitor.process_event(state, deletion(103, 20, "three.txt"))

            incident = state["remoteDeletes"]["monitored-folder"]
            self.assertTrue(incident["active"])
            self.assertEqual(incident["burstTotal"], 3)
            self.assertEqual(len(state["pendingNotifications"]), 1)

            monitor.process_event(state, deletion(104, 30, "four.txt"))

            self.assertEqual(incident["burstTotal"], 4)
            self.assertEqual(len(state["pendingNotifications"]), 1)

            with patch.object(
                monitor.time,
                "time",
                return_value=incident["lastTime"] + 300,
            ):
                with patch.object(monitor, "atomic_save") as save:
                    monitor.close_quiet_incidents(state)

            self.assertEqual(len(state["pendingNotifications"]), 2)
            self.assertIn(
                "Total observed file deletions: 4",
                state["pendingNotifications"][1]["body"],
            )
            self.assertFalse(
                state["remoteDeletes"]["monitored-folder"]["active"]
            )
            self.assertEqual(save.call_count, 1)


class RestartTests(unittest.TestCase):
    def test_syncthing_restart_closes_active_incident_before_reset(self):
        state = {
            "syncthingStartTime": "old-start",
            "lastEventId": 350,
            "receiveOnly": {},
            "remoteDeletes": {
                "monitored-folder": {
                    "events": [],
                    "active": True,
                    "burstTotal": 57,
                    "firstTime": 1000.0,
                    "lastTime": 1100.0,
                    "samplePaths": ["one.txt", "two.txt"],
                },
            },
            "pendingNotifications": [],
        }

        saved_states = []

        def capture_save(current_state):
            import copy
            saved_states.append(copy.deepcopy(current_state))

        verification_saves_seen = []

        def capture_verification(current_state, force_save=False):
            verification_saves_seen.append(
                (len(saved_states), force_save)
            )

        with (
            patch.object(
                monitor,
                "FOLDERS",
                {"monitored-folder": "Monitored Folder"},
            ),
            patch.object(
                monitor,
                "atomic_save",
                side_effect=capture_save,
            ) as save,
            patch.object(
                monitor,
                "check_backup_state",
                side_effect=capture_verification,
            ) as check_backup_state,
        ):
            monitor.restart(state, "new-start")

        self.assertEqual(save.call_count, 1)
        self.assertEqual(len(saved_states), 1)
        check_backup_state.assert_called_once_with(
            state,
            force_save=True,
        )
        self.assertEqual(
            verification_saves_seen,
            [(1, True)],
        )

        saved = saved_states[0]
        self.assertEqual(len(saved["pendingNotifications"]), 1)
        self.assertIn(
            "Total observed file deletions: 57",
            saved["pendingNotifications"][0]["body"],
        )
        self.assertIn(
            "Syncthing restarted",
            saved["pendingNotifications"][0]["body"],
        )
        self.assertEqual(saved["remoteDeletes"], {})
        self.assertEqual(saved["lastEventId"], 0)
        self.assertEqual(saved["syncthingStartTime"], "new-start")


if __name__ == "__main__":
    unittest.main()

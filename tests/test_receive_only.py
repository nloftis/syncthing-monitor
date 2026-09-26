import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "syncthing-monitor.py"
FIXTURE_PATH = (
    ROOT
    / "tests"
    / "fixtures"
    / "db-status-clean-syncthing-2.0.10.json"
)

spec = importlib.util.spec_from_file_location("syncthing_monitor", MODULE_PATH)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def observation(total_items, **overrides):
    data = json.loads(FIXTURE_PATH.read_text())
    data["receiveOnlyTotalItems"] = total_items
    data.update(overrides)
    return data


class ReceiveOnlyEvaluatorTests(unittest.TestCase):
    def test_startup_clean_establishes_silent_baseline(self):
        new_state, alerts = monitor.evaluate_receive_only(
            None,
            observation(0),
            observed_at="2026-09-24T20:00:00+00:00",
        )

        self.assertEqual(new_state["lastObservedCount"], 0)
        self.assertIsNone(new_state["lastAlertedCount"])
        self.assertIsNone(new_state["lastAlertTime"])
        self.assertEqual(alerts, [])

    def test_total_items_is_authoritative(self):
        new_state, alerts = monitor.evaluate_receive_only(
            None,
            observation(
                0,
                receiveOnlyChangedFiles=3,
                receiveOnlyChangedDirectories=2,
                receiveOnlyChangedDeletes=1,
            ),
            observed_at="2026-09-24T20:00:00+00:00",
        )

        self.assertEqual(new_state["receiveOnlyTotalItems"], 0)
        self.assertEqual(new_state["lastObservedCount"], 0)
        self.assertEqual(alerts, [])

    def test_startup_dirty_alerts(self):
        new_state, alerts = monitor.evaluate_receive_only(
            None,
            observation(
                1,
                receiveOnlyChangedFiles=1,
                receiveOnlyChangedBytes=27,
            ),
            observed_at="2026-09-24T20:00:00+00:00",
        )

        self.assertEqual(new_state["lastObservedCount"], 1)
        self.assertEqual(new_state["lastAlertedCount"], 1)
        self.assertEqual(
            new_state["lastAlertTime"],
            "2026-09-24T20:00:00+00:00",
        )
        self.assertEqual(len(alerts), 1)

    def test_clean_to_dirty_alerts(self):
        previous = {
            "lastObservedCount": 0,
            "lastAlertedCount": None,
            "lastAlertTime": None,
        }

        new_state, alerts = monitor.evaluate_receive_only(
            previous,
            observation(1, receiveOnlyChangedFiles=1),
            observed_at="2026-09-24T20:01:00+00:00",
        )

        self.assertEqual(new_state["lastObservedCount"], 1)
        self.assertEqual(new_state["lastAlertedCount"], 1)
        self.assertEqual(
            new_state["lastAlertTime"],
            "2026-09-24T20:01:00+00:00",
        )
        self.assertEqual(len(alerts), 1)

    def test_dirty_same_is_silent(self):
        previous = {
            "lastObservedCount": 4,
            "lastAlertedCount": 4,
            "lastAlertTime": "2026-09-24T20:00:00+00:00",
        }

        new_state, alerts = monitor.evaluate_receive_only(
            previous,
            observation(4, receiveOnlyChangedFiles=4),
            observed_at="2026-09-24T20:01:00+00:00",
        )

        self.assertEqual(new_state["lastObservedCount"], 4)
        self.assertEqual(new_state["lastAlertedCount"], 4)
        self.assertEqual(alerts, [])

    def test_same_total_persists_changed_component_counters(self):
        previous = {
            "receiveOnlyChangedFiles": 4,
            "receiveOnlyChangedDirectories": 0,
            "receiveOnlyChangedSymlinks": 0,
            "receiveOnlyChangedDeletes": 0,
            "receiveOnlyChangedBytes": 100,
            "receiveOnlyTotalItems": 4,
            "lastObservedCount": 4,
            "lastAlertedCount": 4,
            "lastAlertTime": "2026-09-24T20:00:00+00:00",
        }

        new_state, alerts = monitor.evaluate_receive_only(
            previous,
            observation(
                4,
                receiveOnlyChangedFiles=3,
                receiveOnlyChangedDirectories=1,
                receiveOnlyChangedBytes=200,
            ),
            observed_at="2026-09-24T20:01:00+00:00",
        )

        self.assertEqual(new_state["receiveOnlyChangedFiles"], 3)
        self.assertEqual(new_state["receiveOnlyChangedDirectories"], 1)
        self.assertEqual(new_state["receiveOnlyChangedBytes"], 200)
        self.assertEqual(new_state["receiveOnlyTotalItems"], 4)
        self.assertEqual(new_state["lastObservedCount"], 4)
        self.assertEqual(alerts, [])

    def test_dirty_to_worse_reports_worsening_candidate(self):
        previous = {
            "lastObservedCount": 4,
            "lastAlertedCount": 4,
            "lastAlertTime": "2026-09-24T20:00:00+00:00",
        }

        new_state, alerts = monitor.evaluate_receive_only(
            previous,
            observation(8, receiveOnlyChangedFiles=8),
            observed_at="2026-09-24T20:01:00+00:00",
        )

        self.assertEqual(new_state["lastObservedCount"], 8)
        self.assertEqual(new_state["lastAlertedCount"], 4)
        self.assertEqual(
            new_state["lastAlertTime"],
            "2026-09-24T20:00:00+00:00",
        )
        self.assertEqual(alerts, ["worsened"])

    def test_partial_recovery_is_silent_and_persisted(self):
        previous = {
            "lastObservedCount": 8,
            "lastAlertedCount": 8,
            "lastAlertTime": "2026-09-24T20:00:00+00:00",
        }

        new_state, alerts = monitor.evaluate_receive_only(
            previous,
            observation(4, receiveOnlyChangedFiles=4),
            observed_at="2026-09-24T20:01:00+00:00",
        )

        self.assertEqual(new_state["lastObservedCount"], 4)
        self.assertEqual(new_state["lastAlertedCount"], 8)
        self.assertEqual(alerts, [])

    def test_after_api_unknown_recovery_compares_with_last_known_state(self):
        previous = {
            "lastObservedCount": 4,
            "lastAlertedCount": 4,
            "lastAlertTime": "2026-09-24T20:00:00+00:00",
        }

        new_state, alerts = monitor.evaluate_receive_only(
            previous,
            observation(0),
            observed_at="2026-09-24T20:02:00+00:00",
        )

        self.assertEqual(new_state["lastObservedCount"], 0)
        self.assertIsNone(new_state["lastAlertedCount"])
        self.assertIsNone(new_state["lastAlertTime"])
        self.assertEqual(alerts, [])

    def test_after_api_unknown_still_dirty_compares_with_last_known_state(self):
        previous = {
            "lastObservedCount": 4,
            "lastAlertedCount": 4,
            "lastAlertTime": "2026-09-24T20:00:00+00:00",
        }

        new_state, alerts = monitor.evaluate_receive_only(
            previous,
            observation(4, receiveOnlyChangedFiles=4),
            observed_at="2026-09-24T20:02:00+00:00",
        )

        self.assertEqual(new_state["lastObservedCount"], 4)
        self.assertEqual(new_state["lastAlertedCount"], 4)
        self.assertEqual(
            new_state["lastAlertTime"],
            "2026-09-24T20:00:00+00:00",
        )
        self.assertEqual(alerts, [])

    def test_full_recovery_is_silent_and_rearms(self):
        previous = {
            "lastObservedCount": 4,
            "lastAlertedCount": 4,
            "lastAlertTime": "2026-09-24T20:00:00+00:00",
        }

        new_state, alerts = monitor.evaluate_receive_only(
            previous,
            observation(0),
            observed_at="2026-09-24T20:01:00+00:00",
        )

        self.assertEqual(new_state["lastObservedCount"], 0)
        self.assertIsNone(new_state["lastAlertedCount"])
        self.assertIsNone(new_state["lastAlertTime"])
        self.assertEqual(alerts, [])

    def test_successful_status_check_returns_true(self):
        folder_id = "test-folder"
        state = {
            "receiveOnly": {
                folder_id: None,
            },
            "pendingNotifications": [],
        }

        with patch.object(
            monitor,
            "FOLDERS",
            {folder_id: "Test Folder"},
        ):
            with patch.object(
                monitor,
                "ro_status",
                return_value=observation(0),
            ):
                with patch.object(monitor, "atomic_save"):
                    succeeded = monitor.check_receive_only(state)

        self.assertIs(succeeded, True)

    def test_partial_status_failure_updates_successful_folder_and_returns_false(self):
        successful_id = "successful-folder"
        failed_id = "failed-folder"

        failed_previous = {
            "lastObservedCount": 4,
            "lastAlertedCount": 4,
            "lastAlertTime": "2026-09-24T20:00:00+00:00",
        }

        state = {
            "receiveOnly": {
                successful_id: None,
                failed_id: failed_previous.copy(),
            },
            "pendingNotifications": [],
        }

        def status_for_folder(folder_id):
            if folder_id == successful_id:
                return observation(0)
            raise RuntimeError("simulated API failure")

        with patch.object(
            monitor,
            "FOLDERS",
            {
                successful_id: "Successful Folder",
                failed_id: "Failed Folder",
            },
        ):
            with patch.object(
                monitor,
                "ro_status",
                side_effect=status_for_folder,
            ):
                with patch.object(monitor, "atomic_save") as save:
                    succeeded = monitor.check_receive_only(state)

        self.assertIs(succeeded, False)
        self.assertEqual(
            state["receiveOnly"][successful_id]["lastObservedCount"],
            0,
        )
        self.assertEqual(
            state["receiveOnly"][failed_id],
            failed_previous,
        )
        save.assert_called_once_with(state)

    def test_missing_required_receive_only_counter_preserves_last_known_state(self):
        folder_id = "test-folder"
        previous = {
            "receiveOnlyChangedFiles": 4,
            "receiveOnlyChangedDirectories": 0,
            "receiveOnlyChangedSymlinks": 0,
            "receiveOnlyChangedDeletes": 0,
            "receiveOnlyChangedBytes": 100,
            "receiveOnlyTotalItems": 4,
            "lastObservedCount": 4,
            "lastAlertedCount": 4,
            "lastAlertTime": "2026-09-24T20:00:00+00:00",
        }

        for missing_counter in monitor.RECEIVE_ONLY_COUNTERS:
            with self.subTest(missing_counter=missing_counter):
                state = {
                    "receiveOnly": {
                        folder_id: previous.copy(),
                    },
                    "pendingNotifications": [],
                }

                incomplete_status = observation(
                    4,
                    receiveOnlyChangedFiles=4,
                    receiveOnlyChangedBytes=100,
                )
                del incomplete_status[missing_counter]

                with patch.object(
                    monitor,
                    "FOLDERS",
                    {folder_id: "Test Folder"},
                ):
                    with patch.object(
                        monitor,
                        "api",
                        return_value=incomplete_status,
                    ):
                        with patch.object(monitor, "atomic_save") as save:
                            succeeded = monitor.check_receive_only(state)

                self.assertIs(succeeded, False)
                self.assertEqual(
                    state["receiveOnly"][folder_id],
                    previous,
                )
                self.assertEqual(state["pendingNotifications"], [])
                save.assert_not_called()

    def test_null_required_receive_only_counter_preserves_last_known_state(self):
        folder_id = "test-folder"
        previous = {
            "receiveOnlyChangedFiles": 4,
            "receiveOnlyChangedDirectories": 0,
            "receiveOnlyChangedSymlinks": 0,
            "receiveOnlyChangedDeletes": 0,
            "receiveOnlyChangedBytes": 100,
            "receiveOnlyTotalItems": 4,
            "lastObservedCount": 4,
            "lastAlertedCount": 4,
            "lastAlertTime": "2026-09-24T20:00:00+00:00",
        }
        state = {
            "receiveOnly": {
                folder_id: previous.copy(),
            },
            "pendingNotifications": [],
        }

        malformed_status = observation(
            4,
            receiveOnlyChangedFiles=0,
            receiveOnlyChangedBytes=0,
        )
        malformed_status["receiveOnlyTotalItems"] = None

        with patch.object(
            monitor,
            "FOLDERS",
            {folder_id: "Test Folder"},
        ):
            with patch.object(
                monitor,
                "api",
                return_value=malformed_status,
            ):
                with patch.object(monitor, "atomic_save") as save:
                    succeeded = monitor.check_receive_only(state)

        self.assertIs(succeeded, False)
        self.assertEqual(state["receiveOnly"][folder_id], previous)
        self.assertEqual(state["pendingNotifications"], [])
        save.assert_not_called()

    def test_status_api_failure_preserves_last_known_state(self):
        folder_id = "test-folder"
        previous = {
            "receiveOnlyChangedFiles": 4,
            "receiveOnlyChangedDirectories": 0,
            "receiveOnlyChangedSymlinks": 0,
            "receiveOnlyChangedDeletes": 0,
            "receiveOnlyChangedBytes": 100,
            "receiveOnlyTotalItems": 4,
            "lastObservedCount": 4,
            "lastAlertedCount": 4,
            "lastAlertTime": "2026-09-24T20:00:00+00:00",
        }
        state = {
            "receiveOnly": {
                folder_id: previous.copy(),
            },
            "pendingNotifications": [],
        }

        with patch.object(
            monitor,
            "FOLDERS",
            {folder_id: "Test Folder"},
        ):
            with patch.object(
                monitor,
                "ro_status",
                side_effect=RuntimeError("simulated API failure"),
            ):
                with patch.object(monitor, "atomic_save") as save:
                    succeeded = monitor.check_receive_only(state)

        self.assertIs(succeeded, False)
        self.assertEqual(state["receiveOnly"][folder_id], previous)
        self.assertEqual(state["pendingNotifications"], [])
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()

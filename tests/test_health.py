import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import call, patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "syncthing-monitor.py"
DB_STATUS_FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "db-status-clean-syncthing-2.0.10.json"
)
FOLDER_ERRORS_FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "folder-errors-clean-syncthing-2.0.10.json"
)

spec = importlib.util.spec_from_file_location(
    "syncthing_monitor",
    MODULE_PATH,
)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def clean_status(**overrides):
    data = json.loads(DB_STATUS_FIXTURE.read_text())
    data.update(overrides)
    return data


def clean_folder_errors(**overrides):
    data = json.loads(FOLDER_ERRORS_FIXTURE.read_text())
    data.update(overrides)
    return data


class FolderHealthObservationTests(unittest.TestCase):
    def test_folder_health_observation_uses_both_endpoints(self):
        status = clean_status(
            watchError="filesystem watcher failed",
        )
        folder_errors = clean_folder_errors()

        with (
            patch.object(
                monitor,
                "FOLDERS",
                {"test-folder": "Test Folder"},
            ),
            patch.object(
                monitor,
                "api",
                side_effect=[status, folder_errors],
            ) as api,
        ):
            observation = monitor.observe_folder_health()

        self.assertEqual(
            observation,
            {
                "test-folder": {
                    "violations": ["watchError"],
                },
            },
        )
        self.assertEqual(
            api.call_args_list,
            [
                call(
                    "/rest/db/status",
                    {"folder": "test-folder"},
                    timeout=15,
                ),
                call(
                    "/rest/folder/errors",
                    {"folder": "test-folder"},
                    timeout=15,
                ),
            ],
        )


class FolderHealthCheckerTests(unittest.TestCase):
    def test_clean_to_unhealthy_queues_notice_and_saves_state(self):
        previous = {
            "test-folder": {
                "violations": [],
            },
        }
        observation = {
            "test-folder": {
                "violations": ["watchError"],
            },
        }
        state = {
            "folderHealth": previous,
            "pendingNotifications": [],
        }

        with (
            patch.object(
                monitor,
                "observe_folder_health",
                return_value=observation,
            ),
            patch.object(
                monitor,
                "FOLDERS",
                {"test-folder": "Test Folder"},
            ),
            patch.object(monitor, "atomic_save") as save,
        ):
            succeeded = monitor.check_folder_health(state)

        self.assertIs(succeeded, True)
        self.assertEqual(state["folderHealth"], observation)
        self.assertEqual(len(state["pendingNotifications"]), 1)
        save.assert_called_once_with(state)


    def test_observation_failure_preserves_last_known_state(self):
        previous = {
            "test-folder": {
                "violations": [],
            },
        }
        state = {
            "folderHealth": previous,
            "pendingNotifications": [],
        }

        with (
            patch.object(
                monitor,
                "observe_folder_health",
                side_effect=RuntimeError("simulated API failure"),
            ),
            patch.object(monitor, "atomic_save") as save,
        ):
            succeeded = monitor.check_folder_health(state)

        self.assertIs(succeeded, False)
        self.assertEqual(state["folderHealth"], previous)
        self.assertEqual(state["pendingNotifications"], [])
        save.assert_not_called()


class HealthStateEvaluatorTests(unittest.TestCase):
    def test_initial_clean_observation_is_silent(self):
        observation = {
            "test-folder": {
                "violations": [],
            },
        }

        state, alerts = monitor.evaluate_health(
            None,
            observation,
        )

        self.assertEqual(state, observation)
        self.assertEqual(alerts, [])

    def test_initial_unhealthy_observation_requests_alert(self):
        observation = {
            "test-folder": {
                "violations": ["watchError"],
            },
        }

        state, alerts = monitor.evaluate_health(
            None,
            observation,
        )

        self.assertEqual(state, observation)
        self.assertEqual(alerts, ["initial"])

    def test_clean_to_unhealthy_requests_alert(self):
        previous = {
            "test-folder": {
                "violations": [],
            },
        }
        observation = {
            "test-folder": {
                "violations": ["state"],
            },
        }

        state, alerts = monitor.evaluate_health(
            previous,
            observation,
        )

        self.assertEqual(state, observation)
        self.assertEqual(alerts, ["initial"])

    def test_unchanged_unhealthy_observation_is_silent(self):
        previous = {
            "test-folder": {
                "violations": ["folderErrors"],
            },
        }
        observation = {
            "test-folder": {
                "violations": ["folderErrors"],
            },
        }

        state, alerts = monitor.evaluate_health(
            previous,
            observation,
        )

        self.assertEqual(state, observation)
        self.assertEqual(alerts, [])


class FolderHealthEvaluatorTests(unittest.TestCase):
    def test_clean_folder_has_no_violations(self):
        violations = monitor.evaluate_folder_health(
            clean_status(),
            clean_folder_errors(),
        )

        self.assertEqual(violations, [])

    def test_error_state_is_reported(self):
        violations = monitor.evaluate_folder_health(
            clean_status(state="error"),
            clean_folder_errors(),
        )

        self.assertEqual(violations, ["state"])

    def test_watch_error_is_reported_independently(self):
        violations = monitor.evaluate_folder_health(
            clean_status(watchError="filesystem watcher failed"),
            clean_folder_errors(),
        )

        self.assertEqual(violations, ["watchError"])

    def test_multiple_health_violations_are_reported_together(self):
        violations = monitor.evaluate_folder_health(
            clean_status(
                state="error",
                watchError="filesystem watcher failed",
            ),
            clean_folder_errors(
                errors=[
                    {
                        "path": "example.txt",
                        "error": "permission denied",
                    },
                ],
            ),
        )

        self.assertEqual(
            violations,
            ["state", "watchError", "folderErrors"],
        )

    def test_folder_errors_are_reported(self):
        violations = monitor.evaluate_folder_health(
            clean_status(),
            clean_folder_errors(
                errors=[
                    {
                        "path": "example.txt",
                        "error": "permission denied",
                    },
                ],
            ),
        )

        self.assertEqual(violations, ["folderErrors"])


if __name__ == "__main__":
    unittest.main()

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "syncthing-monitor.py"

spec = importlib.util.spec_from_file_location("syncthing_monitor", MODULE_PATH)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class ParseFoldersTests(unittest.TestCase):
    def test_empty_value_returns_empty_mapping(self):
        self.assertEqual(monitor.parse_folders(""), {})

    def test_single_folder(self):
        self.assertEqual(
            monitor.parse_folders("tdtbs-2don4:documents"),
            {"tdtbs-2don4": "documents"},
        )

    def test_multiple_folders(self):
        self.assertEqual(
            monitor.parse_folders(
                "tdtbs-2don4:documents,rpaem-rd2ho:github"
            ),
            {
                "tdtbs-2don4": "documents",
                "rpaem-rd2ho": "github",
            },
        )

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(
            monitor.parse_folders(
                " tdtbs-2don4 : documents , rpaem-rd2ho : github "
            ),
            {
                "tdtbs-2don4": "documents",
                "rpaem-rd2ho": "github",
            },
        )

    def test_missing_colon_is_rejected(self):
        with self.assertRaises(ValueError):
            monitor.parse_folders("tdtbs-2don4")

    def test_empty_id_is_rejected(self):
        with self.assertRaises(ValueError):
            monitor.parse_folders(":documents")

    def test_empty_label_is_rejected(self):
        with self.assertRaises(ValueError):
            monitor.parse_folders("tdtbs-2don4:")

class AuthoritativeDeviceTests(unittest.TestCase):
    def test_authoritative_device_id_is_derived_from_device_topology(self):
        devices = [
            {"deviceID": "synology-id"},
            {"deviceID": "source-host-id"},
        ]

        self.assertEqual(
            monitor.authoritative_device_id(
                devices,
                "synology-id",
            ),
            "source-host-id",
        )

    def test_missing_remote_device_is_rejected(self):
        devices = [
            {"deviceID": "synology-id"},
        ]

        with self.assertRaisesRegex(RuntimeError, "authoritative device"):
            monitor.authoritative_device_id(
                devices,
                "synology-id",
            )

    def test_multiple_remote_devices_are_rejected_as_ambiguous(self):
        devices = [
            {"deviceID": "synology-id"},
            {"deviceID": "source-a-id"},
            {"deviceID": "source-b-id"},
        ]

        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            monitor.authoritative_device_id(
                devices,
                "synology-id",
            )

    def test_missing_local_device_is_rejected(self):
        devices = [
            {"deviceID": "source-host-id"},
        ]

        with self.assertRaisesRegex(RuntimeError, "local device"):
            monitor.authoritative_device_id(
                devices,
                "synology-id",
            )


class FolderConfigEvaluatorTests(unittest.TestCase):
    def valid_config(self):
        return {
            "type": "receiveonly",
            "paused": False,
            "fsWatcherEnabled": True,
            "versioning": {
                "type": "staggered",
                "params": {
                    "maxAge": "31536000",
                },
            },
            "devices": [
                {"deviceID": "synology-id"},
                {"deviceID": "source-host-id"},
            ],
        }

    def test_valid_configuration_has_no_violations(self):
        violations = monitor.evaluate_folder_config(
            self.valid_config(),
            "source-host-id",
        )

        self.assertEqual(violations, [])

    def test_wrong_folder_type_is_reported(self):
        config = self.valid_config()
        config["type"] = "sendreceive"

        violations = monitor.evaluate_folder_config(config, "source-host-id")

        self.assertIn("type", violations)

    def test_paused_folder_is_reported(self):
        config = self.valid_config()
        config["paused"] = True

        violations = monitor.evaluate_folder_config(config, "source-host-id")

        self.assertIn("paused", violations)

    def test_disabled_filesystem_watcher_is_reported(self):
        config = self.valid_config()
        config["fsWatcherEnabled"] = False

        violations = monitor.evaluate_folder_config(config, "source-host-id")

        self.assertIn("fsWatcherEnabled", violations)

    def test_wrong_versioning_configuration_is_reported(self):
        config = self.valid_config()
        config["versioning"] = {
            "type": "simple",
            "params": {
                "maxAge": "86400",
            },
        }

        violations = monitor.evaluate_folder_config(config, "source-host-id")

        self.assertIn("versioning", violations)

    def test_missing_authoritative_device_is_reported(self):
        config = self.valid_config()
        config["devices"] = [{"deviceID": "synology-id"}]

        violations = monitor.evaluate_folder_config(config, "source-host-id")

        self.assertIn("authoritativeDevice", violations)


class ConfigurationObservationTests(unittest.TestCase):
    def test_missing_local_device_id_is_rejected(self):
        with patch.object(
            monitor,
            "api",
            return_value={},
        ) as api:
            with self.assertRaisesRegex(RuntimeError, "missing myID"):
                monitor.observe_configuration()

        api.assert_called_once_with("/rest/system/status")

    def test_clean_configuration_is_observed(self):
        status = {"myID": "synology-id"}
        devices = [
            {"deviceID": "synology-id"},
            {"deviceID": "source-host-id"},
        ]
        folder_config = {
            "type": "receiveonly",
            "paused": False,
            "fsWatcherEnabled": True,
            "versioning": {
                "type": "staggered",
                "params": {"maxAge": "31536000"},
            },
            "devices": [
                {"deviceID": "synology-id"},
                {"deviceID": "source-host-id"},
            ],
        }

        with (
            patch.object(
                monitor,
                "FOLDERS",
                {"documents-id": "documents"},
            ),
            patch.object(
                monitor,
                "api",
                side_effect=[status, devices, folder_config],
            ),
        ):
            observation = monitor.observe_configuration()

        self.assertEqual(
            observation,
            {
                "authoritativeDeviceId": "source-host-id",
                "folders": {
                    "documents-id": {
                        "violations": [],
                    },
                },
            },
        )

    def test_authoritative_device_missing_from_folder_is_violation(self):
        status = {"myID": "synology-id"}
        devices = [
            {"deviceID": "synology-id"},
            {"deviceID": "source-host-id"},
        ]
        folder_config = {
            "type": "receiveonly",
            "paused": False,
            "fsWatcherEnabled": True,
            "versioning": {
                "type": "staggered",
                "params": {"maxAge": "31536000"},
            },
            "devices": [
                {"deviceID": "synology-id"},
            ],
        }

        with (
            patch.object(
                monitor,
                "FOLDERS",
                {"documents-id": "documents"},
            ),
            patch.object(
                monitor,
                "api",
                side_effect=[status, devices, folder_config],
            ),
        ):
            observation = monitor.observe_configuration()

        self.assertEqual(
            observation,
            {
                "authoritativeDeviceId": "source-host-id",
                "folders": {
                    "documents-id": {
                        "violations": ["authoritativeDevice"],
                    },
                },
            },
        )

    def test_authoritative_device_id_change_is_observed_as_folder_violation(self):
        status = {"myID": "synology-id"}
        devices = [
            {"deviceID": "synology-id"},
            {"deviceID": "new-source-host-id"},
        ]
        folder_config = {
            "type": "receiveonly",
            "paused": False,
            "fsWatcherEnabled": True,
            "versioning": {
                "type": "staggered",
                "params": {"maxAge": "31536000"},
            },
            "devices": [
                {"deviceID": "synology-id"},
                {"deviceID": "old-source-host-id"},
            ],
        }

        with (
            patch.object(
                monitor,
                "FOLDERS",
                {"documents-id": "documents"},
            ),
            patch.object(
                monitor,
                "api",
                side_effect=[status, devices, folder_config],
            ),
        ):
            observation = monitor.observe_configuration()

        self.assertEqual(
            observation["authoritativeDeviceId"],
            "new-source-host-id",
        )
        self.assertEqual(
            observation["folders"]["documents-id"]["violations"],
            ["authoritativeDevice"],
        )


class ConfigurationEvaluatorTests(unittest.TestCase):
    def test_initial_clean_observation_establishes_clean_state(self):
        observation = {
            "authoritativeDeviceId": "source-host-id",
            "folders": {
                "documents-id": {
                    "violations": [],
                },
            },
        }

        state, alerts = monitor.evaluate_configuration(
            None,
            observation,
        )

        self.assertEqual(state, observation)
        self.assertEqual(alerts, [])

    def test_clean_to_violation_requests_alert(self):
        previous = {
            "authoritativeDeviceId": "source-host-id",
            "folders": {
                "documents-id": {
                    "violations": [],
                },
            },
        }
        observation = {
            "authoritativeDeviceId": "source-host-id",
            "folders": {
                "documents-id": {
                    "violations": ["fsWatcherEnabled"],
                },
            },
        }

        state, alerts = monitor.evaluate_configuration(
            previous,
            observation,
        )

        self.assertEqual(state, observation)
        self.assertEqual(alerts, ["initial"])

    def test_unchanged_violation_does_not_repeat_alert(self):
        observation = {
            "authoritativeDeviceId": "source-host-id",
            "folders": {
                "documents-id": {
                    "violations": ["fsWatcherEnabled"],
                },
            },
        }

        state, alerts = monitor.evaluate_configuration(
            observation,
            observation,
        )

        self.assertEqual(state, observation)
        self.assertEqual(alerts, [])

    def test_initial_violation_requests_alert(self):
        observation = {
            "authoritativeDeviceId": "source-host-id",
            "folders": {
                "documents-id": {
                    "violations": ["fsWatcherEnabled"],
                },
            },
        }

        state, alerts = monitor.evaluate_configuration(
            None,
            observation,
        )

        self.assertEqual(state, observation)
        self.assertEqual(alerts, ["initial"])


class ConfigurationCheckerTests(unittest.TestCase):
    def test_observation_failure_preserves_last_known_state(self):
        previous = {
            "authoritativeDeviceId": "source-host-id",
            "folders": {
                "documents-id": {
                    "violations": [],
                },
            },
        }
        state = {
            "configuration": previous,
            "pendingNotifications": [],
        }

        with (
            patch.object(
                monitor,
                "observe_configuration",
                side_effect=RuntimeError("simulated API failure"),
            ),
            patch.object(monitor, "atomic_save") as save,
        ):
            succeeded = monitor.check_configuration(state)

        self.assertIs(succeeded, False)
        self.assertEqual(state["configuration"], previous)
        self.assertEqual(state["pendingNotifications"], [])
        save.assert_not_called()

    def test_clean_to_violation_queues_notice_and_saves_state(self):
        previous = {
            "authoritativeDeviceId": "source-host-id",
            "folders": {
                "documents-id": {
                    "violations": [],
                },
            },
        }
        observation = {
            "authoritativeDeviceId": "source-host-id",
            "folders": {
                "documents-id": {
                    "violations": ["fsWatcherEnabled"],
                },
            },
        }
        state = {
            "configuration": previous,
            "pendingNotifications": [],
        }

        with (
            patch.object(
                monitor,
                "observe_configuration",
                return_value=observation,
            ),
            patch.object(monitor, "atomic_save") as save,
        ):
            succeeded = monitor.check_configuration(state)

        self.assertIs(succeeded, True)
        self.assertEqual(state["configuration"], observation)
        self.assertEqual(len(state["pendingNotifications"]), 1)
        save.assert_called_once_with(state)


class ValidateConfigTests(unittest.TestCase):
    def valid_config(self):
        return {
            "st_api_key": "test-api-key",
            "folders": {"folder-id": "documents"},
            "notify_method": "email",
            "smtp_user": "sender@example.com",
            "smtp_password": "secret",
            "mail_from": "sender@example.com",
            "mail_to": "recipient@example.com",
        }

    def test_valid_config_is_accepted(self):
        monitor.validate_config(**self.valid_config())

    def test_missing_api_key_is_rejected(self):
        config = self.valid_config()
        config["st_api_key"] = ""

        with self.assertRaisesRegex(RuntimeError, "ST_API_KEY"):
            monitor.validate_config(**config)

    def test_empty_folders_is_rejected(self):
        config = self.valid_config()
        config["folders"] = {}

        with self.assertRaisesRegex(RuntimeError, "FOLDERS"):
            monitor.validate_config(**config)

    def test_source_identity_configuration_is_not_required(self):
        monitor.validate_config(**self.valid_config())

    def test_missing_smtp_user_is_rejected(self):
        config = self.valid_config()
        config["smtp_user"] = ""

        with self.assertRaisesRegex(RuntimeError, "SMTP_USER"):
            monitor.validate_config(**config)

    def test_missing_smtp_password_is_rejected(self):
        config = self.valid_config()
        config["smtp_password"] = ""

        with self.assertRaisesRegex(RuntimeError, "SMTP_PASSWORD"):
            monitor.validate_config(**config)

    def test_missing_mail_from_is_rejected(self):
        config = self.valid_config()
        config["mail_from"] = ""

        with self.assertRaisesRegex(RuntimeError, "MAIL_FROM"):
            monitor.validate_config(**config)

    def test_missing_mail_to_is_rejected(self):
        config = self.valid_config()
        config["mail_to"] = ""

        with self.assertRaisesRegex(RuntimeError, "MAIL_TO"):
            monitor.validate_config(**config)

    def test_unsupported_notify_method_is_rejected(self):
        config = self.valid_config()
        config["notify_method"] = "carrier-pigeon"

        with self.assertRaisesRegex(RuntimeError, "NOTIFY_METHOD"):
            monitor.validate_config(**config)


if __name__ == "__main__":
    unittest.main()

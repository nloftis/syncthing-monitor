import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import call, patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "syncthing-monitor.py"
DEVICE_STATS_FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "device-stats-connected-syncthing-2.0.10.json"
)
CONNECTIONS_FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "system-connections-connected-syncthing-2.0.10.json"
)

spec = importlib.util.spec_from_file_location(
    "syncthing_monitor",
    MODULE_PATH,
)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class SourceConnectivityCheckerTests(unittest.TestCase):
    def test_observation_failure_preserves_last_known_state(self):
        previous = {
            "deviceId": "SOURCE-DEVICE-ID",
            "connected": True,
        }
        state = {
            "sourceConnectivity": previous,
            "pendingNotifications": [],
        }

        with (
            patch.object(
                monitor,
                "observe_source_connectivity",
                side_effect=RuntimeError("simulated API failure"),
            ),
            patch.object(monitor, "atomic_save") as save,
        ):
            succeeded = monitor.check_source_connectivity(state)

        self.assertIs(succeeded, False)
        self.assertEqual(
            state["sourceConnectivity"],
            previous,
        )
        self.assertEqual(state["pendingNotifications"], [])
        save.assert_not_called()


    def test_unchanged_successful_observation_returns_true_without_save(self):
        observation = {
            "deviceId": "SOURCE-DEVICE-ID",
            "lastSeen": "2026-09-24T16:18:32-10:00",
            "lastConnectionDurationS": 14551.207307229,
            "connected": True,
            "connectionStartedAt": "2026-09-24T16:15:55-10:00",
            "connectionObservedAt": "2026-09-24T16:19:03-10:00",
        }
        health_state = {
            "deviceId": "SOURCE-DEVICE-ID",
            "connected": True,
        }
        state = {
            "sourceConnectivity": health_state.copy(),
            "pendingNotifications": [],
        }

        with (
            patch.object(
                monitor,
                "observe_source_connectivity",
                return_value=observation,
            ),
            patch.object(monitor, "atomic_save") as save,
            patch.object(monitor, "log") as log,
        ):
            succeeded = monitor.check_source_connectivity(state)

        self.assertIs(succeeded, True)
        self.assertEqual(state["sourceConnectivity"], health_state)
        self.assertEqual(state["pendingNotifications"], [])
        save.assert_not_called()
        log.assert_not_called()


    def test_changed_diagnostics_without_health_change_do_not_save(self):
        previous = {
            "deviceId": "SOURCE-DEVICE-ID",
            "connected": True,
        }
        observation = {
            "deviceId": "SOURCE-DEVICE-ID",
            "lastSeen": "2026-09-24T16:20:32-10:00",
            "lastConnectionDurationS": 14671.207307229,
            "connected": True,
            "connectionStartedAt": "2026-09-24T16:15:55-10:00",
            "connectionObservedAt": "2026-09-24T16:21:03-10:00",
        }
        state = {
            "sourceConnectivity": previous.copy(),
            "pendingNotifications": [],
        }

        with (
            patch.object(
                monitor,
                "observe_source_connectivity",
                return_value=observation,
            ),
            patch.object(monitor, "atomic_save") as save,
            patch.object(monitor, "log") as log,
        ):
            succeeded = monitor.check_source_connectivity(state)

        self.assertIs(succeeded, True)
        self.assertEqual(state["sourceConnectivity"], previous)
        self.assertEqual(state["pendingNotifications"], [])
        save.assert_not_called()
        log.assert_not_called()


    def test_connectivity_change_is_persisted_without_notification(self):
        previous = {
            "deviceId": "SOURCE-DEVICE-ID",
            "connected": True,
        }
        observation = {
            "deviceId": "SOURCE-DEVICE-ID",
            "lastSeen": "2026-09-24T16:18:32-10:00",
            "lastConnectionDurationS": 14551.207307229,
            "connected": False,
            "connectionStartedAt": None,
            "connectionObservedAt": None,
        }
        state = {
            "sourceConnectivity": previous,
            "pendingNotifications": [],
        }

        with (
            patch.object(
                monitor,
                "observe_source_connectivity",
                return_value=observation,
            ),
            patch.object(monitor, "atomic_save") as save,
            patch.object(monitor, "log") as log,
        ):
            succeeded = monitor.check_source_connectivity(state)

        self.assertIs(succeeded, True)
        self.assertEqual(
            state["sourceConnectivity"],
            {
                "deviceId": "SOURCE-DEVICE-ID",
                "connected": False,
            },
        )
        self.assertEqual(state["pendingNotifications"], [])
        save.assert_called_once_with(state)
        log.assert_called_once()


    def test_successful_observation_is_persisted_without_notification(self):
        observation = {
            "deviceId": "SOURCE-DEVICE-ID",
            "lastSeen": "2026-09-24T16:18:32-10:00",
            "lastConnectionDurationS": 14551.207307229,
            "connected": True,
            "connectionStartedAt": "2026-09-24T16:15:55-10:00",
            "connectionObservedAt": "2026-09-24T16:19:03-10:00",
        }
        state = {
            "sourceConnectivity": None,
            "pendingNotifications": [],
        }

        with (
            patch.object(
                monitor,
                "observe_source_connectivity",
                return_value=observation,
            ),
            patch.object(monitor, "atomic_save") as save,
            patch.object(monitor, "log") as log,
        ):
            succeeded = monitor.check_source_connectivity(state)

        self.assertIs(succeeded, True)
        self.assertEqual(
            state["sourceConnectivity"],
            {
                "deviceId": "SOURCE-DEVICE-ID",
                "connected": True,
            },
        )
        self.assertEqual(state["pendingNotifications"], [])
        save.assert_called_once_with(state)
        log.assert_called_once()


class SourceConnectivityObservationTests(unittest.TestCase):
    def test_missing_local_device_id_is_rejected(self):
        with patch.object(
            monitor,
            "api",
            return_value={},
        ) as api:
            with self.assertRaisesRegex(RuntimeError, "missing myID"):
                monitor.observe_source_connectivity()

        api.assert_called_once_with("/rest/system/status")

    def test_missing_connection_entry_is_observed_as_disconnected(self):
        status = {"myID": "LOCAL-DEVICE-ID"}
        stats = json.loads(DEVICE_STATS_FIXTURE.read_text())
        connections = {
            "connections": {},
            "total": {
                "at": "2026-09-24T16:19:03-10:00",
            },
        }
        devices = [
            {"deviceID": "LOCAL-DEVICE-ID"},
            {"deviceID": "SOURCE-DEVICE-ID"},
        ]

        with patch.object(
            monitor,
            "api",
            side_effect=[status, devices, stats, connections],
        ):
            observation = monitor.observe_source_connectivity()

        self.assertEqual(
            observation,
            {
                "deviceId": "SOURCE-DEVICE-ID",
                "lastSeen": "2026-09-24T16:18:32-10:00",
                "lastConnectionDurationS": 14551.207307229,
                "connected": False,
                "connectionStartedAt": None,
                "connectionObservedAt": None,
            },
        )

    def test_observes_authoritative_source_from_both_endpoints(self):
        status = {"myID": "LOCAL-DEVICE-ID"}
        stats = json.loads(DEVICE_STATS_FIXTURE.read_text())
        connections = json.loads(CONNECTIONS_FIXTURE.read_text())
        devices = [
            {"deviceID": "LOCAL-DEVICE-ID"},
            {"deviceID": "SOURCE-DEVICE-ID"},
        ]

        with patch.object(
            monitor,
            "api",
            side_effect=[status, devices, stats, connections],
        ) as api:
            observation = monitor.observe_source_connectivity()

        self.assertEqual(
            observation,
            {
                "deviceId": "SOURCE-DEVICE-ID",
                "lastSeen": "2026-09-24T16:18:32-10:00",
                "lastConnectionDurationS": 14551.207307229,
                "connected": True,
                "connectionStartedAt": "2026-09-24T16:15:55-10:00",
                "connectionObservedAt": "2026-09-24T16:19:03-10:00",
            },
        )
        self.assertEqual(
            api.call_args_list,
            [
                call("/rest/system/status"),
                call("/rest/config/devices"),
                call("/rest/stats/device"),
                call("/rest/system/connections"),
            ],
        )


if __name__ == "__main__":
    unittest.main()

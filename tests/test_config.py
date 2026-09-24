import importlib.util
import unittest
from pathlib import Path


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

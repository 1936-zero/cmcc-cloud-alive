import argparse
import unittest
from unittest import mock

from cmcc_cloud_alive import main


class InteractiveBootArgumentTests(unittest.TestCase):
    def test_parser_provides_boot_defaults_for_webui_child_command(self):
        args = main.build_parser().parse_args(
            [
                "--state",
                "profile.json",
                "product-keepalive",
                "interactive",
                "desktop-id",
                "--non-interactive",
            ]
        )

        self.assertEqual(args.boot_wait, 180)
        self.assertEqual(args.boot_timeout, 15)

    def test_programmatic_namespace_without_boot_fields_uses_defaults(self):
        args = argparse.Namespace(
            state="profile.json",
            username="account",
            password="password",
            account_type="main",
            user_service_id="desktop-id",
            non_interactive=True,
            report_file=None,
        )

        with (
            mock.patch.object(main, "_interactive_login", return_value=("account", "password")),
            mock.patch.object(main, "_interactive_select", return_value="desktop-id"),
            mock.patch.object(main.cloud, "status", return_value={"vmStatus": "OFF"}),
            mock.patch.object(main.cloud, "is_running", return_value=False),
            mock.patch.object(
                main.cag_boot,
                "ensure_running",
                side_effect=RuntimeError("stop after boot call"),
            ) as ensure_running,
            mock.patch.object(main, "_write_report"),
        ):
            main.cmd_interactive(args)

        ensure_running.assert_called_once_with(
            "desktop-id",
            "profile.json",
            180,
            15,
        )


if __name__ == "__main__":
    unittest.main()

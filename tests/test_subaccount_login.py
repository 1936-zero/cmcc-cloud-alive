import argparse
import unittest
from unittest import mock

from cmcc_cloud_alive import core


class PasswordLoginPayloadTests(unittest.TestCase):
    def _run_login(self, account_type):
        args = argparse.Namespace(
            username="example-account",
            password="secret",
            verification_code="",
            random_code="",
            account_type=account_type,
        )
        calls = []

        def fake_api_request(path, data=None, args=None, **kwargs):
            calls.append((path, data, kwargs))
            if path == "/login/publicKey/v1":
                return {"code": 2000, "msg": "SUCCESS", "data": "login-key"}
            return {
                "code": 2000,
                "msg": "SUCCESS",
                "data": {
                    "userId": 123,
                    "subAccount": "example-account" if account_type == "sub" else None,
                    "sohoToken": "token",
                },
            }

        with (
            mock.patch.object(core, "load_state", return_value={"publicKey": "transport-key"}),
            mock.patch.object(core, "ensure_public_key"),
            mock.patch.object(core, "rsa_encrypt_string", return_value="encrypted-password"),
            mock.patch.object(core, "api_request", side_effect=fake_api_request),
            mock.patch.object(core, "merge_state"),
        ):
            core.password_login(args)

        return calls[-1]

    def test_subaccount_uses_dedicated_field(self):
        path, payload, _ = self._run_login("sub")
        self.assertEqual(path, "/login/home/namePwdLogin/v1")
        self.assertEqual(payload["subAccount"], "example-account")
        self.assertNotIn("username", payload)

    def test_main_account_keeps_username_field(self):
        path, payload, _ = self._run_login("main")
        self.assertEqual(path, "/login/namePwdLogin/v1")
        self.assertEqual(payload["username"], "example-account")
        self.assertNotIn("subAccount", payload)


if __name__ == "__main__":
    unittest.main()

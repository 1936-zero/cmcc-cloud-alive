#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``product-keepalive`` CLI entry (P11).

All firmAuth / route / backend calls are mocked — no real network, no real
SCG binary, no real ZTE CAG. The tests assert:

* the ``product-keepalive`` subcommand is registered with the expected flags;
* firmAuth is loaded once and routed to SCG or ZTE by ``classify_firm_auth_route``;
* the redacted report always carries the no-spin schema
  (route/stage/ok/duration/error/nextStep);
* no raw credential (token / password / connectStr / scAuthCode) is ever
  emitted in the report.
"""

import argparse
import io
import json
import unittest
from unittest import mock

from cmcc_cloud_alive import main as cli_main
from cmcc_cloud_alive import product_router
from cmcc_cloud_alive.product_router import ProductRoute, RouteKind
from cmcc_cloud_alive.scg_route import SCGKeepaliveResult
from cmcc_cloud_alive.zte_route import MaterialReport


# --- fixtures ---------------------------------------------------------------

# A firmAuth dict that classifies as SCG (scAuthCode present).
SCG_AUTH = {
    "scAuthCode": "SUPER-SECRET-SCG-CODE-12345",
    "vmId": "vm-scg-001",
    "userServiceId": "us-scg-001",
    "token": "RAW-TOKEN-MUST-NOT-LEAK",
    "password": "RAW-PASSWORD-MUST-NOT-LEAK",
}

# A firmAuth dict that classifies as ZTE (no scAuthCode, ZTE fields complete).
ZTE_AUTH = {
    "vmId": "vm-zte-001",
    "vmUserName": "admin",
    "vmPassword": "RAW-ZTE-PASSWORD-MUST-NOT-LEAK",
    "vmcIp": "10.0.0.1",
    "vmcPort": 443,
    "cagIp": "10.0.0.2",
    "cagPort": 8443,
    "token": "RAW-TOKEN-MUST-NOT-LEAK",
}

# A redacted summary that mirrors what redacted_firm_auth_summary produces.
REDACTED_SUMMARY = {
    "kind": "scg",
    "hasScAuthCode": True,
    "hasToken": True,
    "vmId": "vm-scg-001",
}

SENSITIVE_VALUES = [
    "SUPER-SECRET-SCG-CODE-12345",
    "RAW-TOKEN-MUST-NOT-LEAK",
    "RAW-PASSWORD-MUST-NOT-LEAK",
    "RAW-ZTE-PASSWORD-MUST-NOT-LEAK",
]


def _make_args(**overrides):
    """Build an argparse.Namespace matching the product-keepalive subcommand."""
    defaults = dict(
        cmd="product-keepalive",
        state=None,
        duration=None,
        forever=False,
        user_service_id=None,
        vm_id=None,
        binary=None,
        config_dir=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _capture_report():
    """Return (patcher, holder) so the emitted report dict is captured."""
    holder = {}

    def fake_print(obj):
        holder["report"] = obj

    patcher = mock.patch.object(cli_main, "_print", side_effect=fake_print)
    return patcher, holder


def _patch_plumbing(auth, route, summary=REDACTED_SUMMARY, sc_auth_code="SC"):
    """Patch cloud/core/product_router plumbing used by cmd_product_keepalive."""
    return [
        mock.patch.object(cli_main.cloud, "selected_user_service_id",
                          return_value=auth.get("userServiceId", "us-001")),
        mock.patch.object(cli_main.core, "get_firm_auth", return_value=auth),
        mock.patch.object(cli_main.product_router, "classify_firm_auth_route",
                          return_value=route),
        mock.patch.object(cli_main.product_router, "redacted_firm_auth_summary",
                          return_value=summary),
        mock.patch.object(cli_main.product_router, "extract_sc_auth_code",
                          return_value=sc_auth_code),
    ]


# --- tests ------------------------------------------------------------------

class ProductKeepaliveParserTests(unittest.TestCase):
    """The subcommand must be registered with the expected flags."""

    def test_subcommand_registered(self):
        parser = cli_main.build_parser()
        args = parser.parse_args(["product-keepalive"])
        self.assertEqual(args.cmd, "product-keepalive")
        self.assertIsNone(args.duration)
        self.assertFalse(args.forever)
        self.assertIsNone(args.user_service_id)
        self.assertIsNone(args.vm_id)
        self.assertIsNone(args.binary)
        self.assertIsNone(args.config_dir)
        self.assertTrue(callable(getattr(args, "func", None)))

    def test_duration_and_forever_flags(self):
        parser = cli_main.build_parser()
        args = parser.parse_args(
            ["product-keepalive", "--duration", "300", "--forever"])
        self.assertEqual(args.duration, 300)
        self.assertTrue(args.forever)

    def test_vm_id_and_binary_overrides(self):
        parser = cli_main.build_parser()
        args = parser.parse_args(
            ["product-keepalive", "--vm-id", "vm-override",
             "--binary", "/tmp/scg", "--config-dir", "/tmp/cfg"])
        self.assertEqual(args.vm_id, "vm-override")
        self.assertEqual(args.binary, "/tmp/scg")
        self.assertEqual(args.config_dir, "/tmp/cfg")


class ProductKeepaliveScgTests(unittest.TestCase):
    """SCG route dispatches to scg_route.run_scg_keepalive."""

    def _scg_route(self):
        return ProductRoute(kind=RouteKind.SCG, reason="scAuthCode present",
                            userServiceId="us-scg-001", vmId="vm-scg-001")

    def test_scg_success(self):
        route = self._scg_route()
        result = SCGKeepaliveResult(returncode=0, stdout=b"ok", stderr=b"",
                                    command=["scg"], config_path="/tmp/c.json")
        patches = _patch_plumbing(SCG_AUTH, route)
        patches.append(mock.patch("cmcc_cloud_alive.scg_route.run_scg_keepalive",
                                  return_value=result))
        cap_patcher, holder = _capture_report()
        for p in patches:
            p.start()
        try:
            cap_patcher.start()
            cli_main.cmd_product_keepalive(_make_args())
        finally:
            cap_patcher.stop()
            for p in reversed(patches):
                p.stop()

        report = holder["report"]
        self.assertTrue(report["ok"])
        self.assertEqual(report["kind"], "scg")
        self.assertEqual(report["stage"], "scg-keepalive-done")
        self.assertEqual(report["vmId"], "vm-scg-001")
        self.assertEqual(report["error"], "")
        self.assertGreaterEqual(report["duration"], 0)
        # no-spin schema
        for key in ("route", "stage", "ok", "duration", "error", "nextStep"):
            self.assertIn(key, report)
        # no raw credentials
        blob = json.dumps(report)
        for secret in SENSITIVE_VALUES:
            self.assertNotIn(secret, blob)

    def test_scg_failure_sets_error_and_nextstep(self):
        route = self._scg_route()
        result = SCGKeepaliveResult(returncode=2, stdout=b"", stderr=b"boom",
                                    command=["scg"], config_path="/tmp/c.json")
        patches = _patch_plumbing(SCG_AUTH, route)
        patches.append(mock.patch("cmcc_cloud_alive.scg_route.run_scg_keepalive",
                                  return_value=result))
        cap_patcher, holder = _capture_report()
        for p in patches:
            p.start()
        try:
            cap_patcher.start()
            cli_main.cmd_product_keepalive(_make_args())
        finally:
            cap_patcher.stop()
            for p in reversed(patches):
                p.stop()

        report = holder["report"]
        self.assertFalse(report["ok"])
        self.assertEqual(report["stage"], "scg-keepalive-failed")
        self.assertIn("boom", report["error"])
        self.assertNotEqual(report["nextStep"], "")

    def test_scg_forever_returns_running(self):
        route = self._scg_route()
        fake_proc = mock.Mock()
        fake_proc.pid = 4242
        patches = _patch_plumbing(SCG_AUTH, route)
        patches.append(mock.patch("cmcc_cloud_alive.scg_route.run_scg_keepalive",
                                  return_value=fake_proc))
        cap_patcher, holder = _capture_report()
        for p in patches:
            p.start()
        try:
            cap_patcher.start()
            cli_main.cmd_product_keepalive(_make_args(forever=True))
        finally:
            cap_patcher.stop()
            for p in reversed(patches):
                p.stop()

        report = holder["report"]
        self.assertTrue(report["ok"])
        self.assertEqual(report["stage"], "scg-keepalive-running")
        self.assertIn("4242", report["nextStep"])

    def test_scg_binary_missing_reports_build_step(self):
        route = self._scg_route()
        patches = _patch_plumbing(SCG_AUTH, route)
        patches.append(mock.patch("cmcc_cloud_alive.scg_route.run_scg_keepalive",
                                  side_effect=FileNotFoundError("no binary")))
        cap_patcher, holder = _capture_report()
        for p in patches:
            p.start()
        try:
            cap_patcher.start()
            cli_main.cmd_product_keepalive(_make_args())
        finally:
            cap_patcher.stop()
            for p in reversed(patches):
                p.stop()

        report = holder["report"]
        self.assertFalse(report["ok"])
        self.assertIn("binary", report["nextStep"].lower())


class ProductKeepaliveZteTests(unittest.TestCase):
    """ZTE route dispatches to zte_route.run_material."""

    def test_zte_success(self):
        route = ProductRoute(kind=RouteKind.ZTE, reason="ZTE fields complete",
                             userServiceId="us-zte-001", vmId="vm-zte-001")
        material = MaterialReport(stage="zte-material", ok=True, error="",
                                  next_step="", has_token=True, desktop_count=1,
                                  target_desktop_found=True, has_connect_str=True)
        patches = _patch_plumbing(ZTE_AUTH, route, summary={"kind": "zte"})
        patches.append(mock.patch("cmcc_cloud_alive.zte_route.run_material",
                                  return_value=material))
        patches.append(mock.patch("cmcc_cloud_alive.zte_route.ZTEFirmAuth"))
        cap_patcher, holder = _capture_report()
        for p in patches:
            p.start()
        try:
            cap_patcher.start()
            cli_main.cmd_product_keepalive(_make_args())
        finally:
            cap_patcher.stop()
            for p in reversed(patches):
                p.stop()

        report = holder["report"]
        self.assertTrue(report["ok"])
        self.assertEqual(report["kind"], "zte")
        self.assertEqual(report["stage"], "zte-material")
        self.assertEqual(report["error"], "")
        # no raw credentials
        blob = json.dumps(report)
        for secret in SENSITIVE_VALUES:
            self.assertNotIn(secret, blob)

    def test_zte_failure_surfaces_error(self):
        route = ProductRoute(kind=RouteKind.ZTE, reason="ZTE fields complete",
                             userServiceId="us-zte-001", vmId="vm-zte-001")
        material = MaterialReport(stage="zte-cag", ok=False,
                                  error="cag unreachable", next_step="retry cag")
        patches = _patch_plumbing(ZTE_AUTH, route, summary={"kind": "zte"})
        patches.append(mock.patch("cmcc_cloud_alive.zte_route.run_material",
                                  return_value=material))
        patches.append(mock.patch("cmcc_cloud_alive.zte_route.ZTEFirmAuth"))
        cap_patcher, holder = _capture_report()
        for p in patches:
            p.start()
        try:
            cap_patcher.start()
            cli_main.cmd_product_keepalive(_make_args())
        finally:
            cap_patcher.stop()
            for p in reversed(patches):
                p.stop()

        report = holder["report"]
        self.assertFalse(report["ok"])
        self.assertIn("cag", report["error"])
        self.assertNotEqual(report["nextStep"], "")


class ProductKeepaliveErrorTests(unittest.TestCase):
    """firmAuth failure / ERROR route must report, not crash."""

    def test_firmauth_failure_reports_error_kind(self):
        patches = [
            mock.patch.object(cli_main.cloud, "selected_user_service_id",
                              return_value="us-001"),
            mock.patch.object(cli_main.core, "get_firm_auth",
                              side_effect=RuntimeError("not logged in")),
        ]
        cap_patcher, holder = _capture_report()
        for p in patches:
            p.start()
        try:
            cap_patcher.start()
            cli_main.cmd_product_keepalive(_make_args())
        finally:
            cap_patcher.stop()
            for p in reversed(patches):
                p.stop()

        report = holder["report"]
        self.assertFalse(report["ok"])
        self.assertEqual(report["kind"], "error")
        self.assertIn("not logged in", report["error"])
        self.assertNotEqual(report["nextStep"], "")

    def test_error_route_kind_reports_stop(self):
        route = ProductRoute(kind=RouteKind.ERROR,
                             reason="no scAuthCode and ZTE incomplete",
                             userServiceId="us-001", vmId="")
        patches = _patch_plumbing(SCG_AUTH, route, summary={"kind": "error"})
        cap_patcher, holder = _capture_report()
        for p in patches:
            p.start()
        try:
            cap_patcher.start()
            cli_main.cmd_product_keepalive(_make_args())
        finally:
            cap_patcher.stop()
            for p in reversed(patches):
                p.stop()

        report = holder["report"]
        self.assertFalse(report["ok"])
        self.assertEqual(report["kind"], "error")
        self.assertIn("fix firmAuth", report["nextStep"])


class ProductKeepaliveNoSpinTests(unittest.TestCase):
    """Every exit path must emit the full no-spin schema."""

    REQUIRED = ("route", "stage", "ok", "duration", "error", "nextStep")

    def _run_and_get(self, patches):
        cap_patcher, holder = _capture_report()
        for p in patches:
            p.start()
        try:
            cap_patcher.start()
            cli_main.cmd_product_keepalive(_make_args())
        finally:
            cap_patcher.stop()
            for p in reversed(patches):
                p.stop()
        return holder["report"]

    def test_scg_path_has_full_schema(self):
        route = ProductRoute(kind=RouteKind.SCG, reason="x", vmId="v1")
        result = SCGKeepaliveResult(0, b"", b"", ["s"], "/c")
        patches = _patch_plumbing(SCG_AUTH, route)
        patches.append(mock.patch("cmcc_cloud_alive.scg_route.run_scg_keepalive",
                                  return_value=result))
        report = self._run_and_get(patches)
        for key in self.REQUIRED:
            self.assertIn(key, report, "missing %s in SCG path" % key)

    def test_error_path_has_full_schema(self):
        patches = [
            mock.patch.object(cli_main.cloud, "selected_user_service_id",
                              return_value="us-001"),
            mock.patch.object(cli_main.core, "get_firm_auth",
                              side_effect=RuntimeError("boom")),
        ]
        report = self._run_and_get(patches)
        for key in self.REQUIRED:
            self.assertIn(key, report, "missing %s in error path" % key)


if __name__ == "__main__":
    unittest.main()

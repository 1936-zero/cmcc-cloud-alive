"""Product pin lock for LIVE product paths (I-E-PIN-GATE / I-E-P1-MODULE-GUARD).

Hard-assert selected product identity before any product-keepalive / LIVE spawn:
  usid=38654967  vmId=1230486  spu=sc-cloud-pc
  FORBIDDEN: usid=2663816 / spu=zte-cloud-pc
  refuse RC=4 on mismatch (fail-closed).

Shared by:
  - cmcc_cloud_alive.main cmd_product_keepalive (module path)
  - scripts/e_shorttest_runner.py (harness path)

Never logs secrets.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import core

PRODUCT_USID = "38654967"
PRODUCT_VMID = "1230486"
PRODUCT_SPU = "sc-cloud-pc"
FORBIDDEN_USID = "2663816"
FORBIDDEN_SPU = "zte-cloud-pc"
PIN_REFUSE_RC = 4


def default_state_path() -> Path:
    """Resolve state.json path (CMCC_ALIVE_STATE or package default)."""
    return core.state_path(None)


def load_state_product_fields(state_file: Path | None = None) -> dict:
    """Load product pin fields only; never return secrets."""
    path = Path(state_file) if state_file is not None else default_state_path()
    out: dict[str, Any] = {
        "selectedUserServiceId": None,
        "lastVmId": None,
        "lastSpuCode": None,
        "desk_usid": None,
        "desk_spu": None,
        "state_exists": False,
        "state_path": str(path),
    }
    if not path.is_file():
        return out
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        out["error"] = f"state unreadable: {type(exc).__name__}"
        return out
    if not isinstance(raw, dict):
        out["error"] = "state unreadable: not an object"
        return out
    out["state_exists"] = True
    out["selectedUserServiceId"] = str(raw.get("selectedUserServiceId") or "") or None
    out["lastVmId"] = str(raw.get("lastVmId") or "") or None
    out["lastSpuCode"] = str(raw.get("lastSpuCode") or "") or None
    desk = raw.get("selectedDesktop") or {}
    if isinstance(desk, dict):
        out["desk_usid"] = str(desk.get("userServiceId") or "") or None
        out["desk_spu"] = str(desk.get("spuCode") or "") or None
    return out


def assert_product_pin(
    cli_usid: str | None = None,
    state_file: Path | None = None,
) -> tuple[bool, str, dict]:
    """Return (ok, reason, fields). Fail-closed on missing/mismatch/forbidden SKU.

    Checks:
      - selectedUserServiceId == 38654967
      - lastSpuCode / desk spu == sc-cloud-pc (when present; missing fails)
      - lastVmId == 1230486 (missing fails)
      - FORBIDDEN: 2663816 / zte-cloud-pc
      - CLI --user-service-id must match PRODUCT_USID when provided
    Never logs secrets.
    """
    fields = load_state_product_fields(state_file)
    reasons: list[str] = []

    if cli_usid is not None and str(cli_usid).strip() != "":
        if str(cli_usid).strip() != PRODUCT_USID:
            reasons.append(
                f"cli --user-service-id={cli_usid!r} != PRODUCT_USID={PRODUCT_USID}"
            )

    if not fields.get("state_exists"):
        reasons.append(f"missing state: {fields.get('state_path')}")
        return False, "; ".join(reasons), fields

    if fields.get("error"):
        reasons.append(str(fields["error"]))
        return False, "; ".join(reasons), fields

    usid = fields.get("selectedUserServiceId")
    spu = fields.get("lastSpuCode")
    vmid = fields.get("lastVmId")
    desk_usid = fields.get("desk_usid")
    desk_spu = fields.get("desk_spu")

    if usid == FORBIDDEN_USID or desk_usid == FORBIDDEN_USID:
        reasons.append(f"FORBIDDEN usid={FORBIDDEN_USID} (non-product SKU)")
    if spu == FORBIDDEN_SPU or desk_spu == FORBIDDEN_SPU:
        reasons.append(f"FORBIDDEN spu={FORBIDDEN_SPU}")

    if usid != PRODUCT_USID:
        reasons.append(f"selectedUserServiceId={usid!r} != {PRODUCT_USID}")
    if desk_usid is not None and desk_usid != PRODUCT_USID:
        reasons.append(f"selectedDesktop.userServiceId={desk_usid!r} != {PRODUCT_USID}")

    # spu hard when present
    if spu is not None and spu != PRODUCT_SPU:
        reasons.append(f"lastSpuCode={spu!r} != {PRODUCT_SPU}")
    if desk_spu is not None and desk_spu != PRODUCT_SPU:
        reasons.append(f"selectedDesktop.spuCode={desk_spu!r} != {PRODUCT_SPU}")
    if spu is None and desk_spu is None:
        reasons.append("spu missing (need sc-cloud-pc)")

    # vm: fail if present and wrong; if missing, fail-closed for LIVE pin gate
    if vmid is None:
        reasons.append("lastVmId missing (need 1230486)")
    elif vmid != PRODUCT_VMID:
        reasons.append(f"lastVmId={vmid!r} != {PRODUCT_VMID}")

    if reasons:
        return False, "; ".join(reasons), fields
    return True, "pin ok", fields


def refuse_pin(
    reason: str,
    fields: dict | None = None,
    *,
    tag: str = "PRODUCT-PIN",
) -> int:
    """Print redacted refuse lines and return PIN_REFUSE_RC (does not exit)."""
    print(f"[{tag}] REFUSE LIVE: product pin mismatch — {reason}", file=sys.stderr)
    safe = {
        "selectedUserServiceId": (fields or {}).get("selectedUserServiceId"),
        "lastVmId": (fields or {}).get("lastVmId"),
        "lastSpuCode": (fields or {}).get("lastSpuCode"),
        "desk_usid": (fields or {}).get("desk_usid"),
        "desk_spu": (fields or {}).get("desk_spu"),
        "expected": {"usid": PRODUCT_USID, "vmId": PRODUCT_VMID, "spu": PRODUCT_SPU},
        "forbidden": {"usid": FORBIDDEN_USID, "spu": FORBIDDEN_SPU},
    }
    print(f"[{tag}] pin_fields: {json.dumps(safe, ensure_ascii=False)}")
    print(f"[{tag}] runbook: reports/I_E_PIN_GATE.md + reports/I_E_P1_MODULE_GUARD.md")
    return PIN_REFUSE_RC


def enforce_product_pin(
    cli_usid: str | None = None,
    state_file: Path | None = None,
    *,
    tag: str = "PRODUCT-PIN",
) -> dict:
    """Assert pin; on failure print refuse and raise SystemExit(PIN_REFUSE_RC).

    Returns pin fields when ok.
    """
    ok, reason, fields = assert_product_pin(cli_usid, state_file=state_file)
    if not ok:
        refuse_pin(reason, fields, tag=tag)
        raise SystemExit(PIN_REFUSE_RC)
    return fields

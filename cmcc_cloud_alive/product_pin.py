"""Optional product pin lock for LIVE product paths (I-E-PIN-GATE).

Default: pin enforcement is **OFF**. Public / third-party clones work with any
cloud-PC selection from the interactive menu.

Enable only for developer acceptance / LIVE harness:

  export CMCC_ENFORCE_PIN=1
  export CMCC_PRODUCT_USID=<your-usid>
  export CMCC_PRODUCT_VMID=<your-vmid>
  export CMCC_PRODUCT_SPU=sc-cloud-pc   # optional; default sc-cloud-pc when pin on

Optional forbidden SKU block (dev only; empty = no forbidden list):

  export CMCC_FORBIDDEN_USID=
  export CMCC_FORBIDDEN_SPU=zte-cloud-pc

Shared by:
  - cmcc_cloud_alive.main cmd_product_keepalive (module path)
  - scripts/e_shorttest_runner.py (harness path)

Never logs secrets. Never ships hard-coded personal product IDs.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from . import core

PIN_REFUSE_RC = 4

# Defaults for public tree: empty pin triad => enforcement inactive unless
# CMCC_ENFORCE_PIN=1 *and* at least USID is configured.
_DEFAULT_SPU = "sc-cloud-pc"


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def pin_enforced() -> bool:
    """True only when CMCC_ENFORCE_PIN is truthy (1/true/yes/on)."""
    return _env("CMCC_ENFORCE_PIN").lower() in ("1", "true", "yes", "on")


def _load_pin_config() -> dict[str, str]:
    """Resolve pin values from env. Empty strings mean 'not configured'."""
    return {
        "usid": _env("CMCC_PRODUCT_USID"),
        "vmid": _env("CMCC_PRODUCT_VMID"),
        "spu": _env("CMCC_PRODUCT_SPU") or (_DEFAULT_SPU if pin_enforced() else ""),
        "forbidden_usid": _env("CMCC_FORBIDDEN_USID"),
        "forbidden_spu": _env("CMCC_FORBIDDEN_SPU"),
    }


def _pin_configured(cfg: dict[str, str] | None = None) -> bool:
    cfg = cfg or _load_pin_config()
    # Require at least USID to treat pin as configured when enforcement is on.
    return bool(cfg.get("usid"))


# Module-level attributes kept for backward-compatible imports in tests/callers.
# Values are re-read from env on each access via property-like helpers below;
# these snapshots are refreshed by refresh_pin_constants().
PRODUCT_USID = ""
PRODUCT_VMID = ""
PRODUCT_SPU = ""
FORBIDDEN_USID = ""
FORBIDDEN_SPU = ""


def refresh_pin_constants() -> None:
    """Refresh module-level PRODUCT_* / FORBIDDEN_* from current env."""
    global PRODUCT_USID, PRODUCT_VMID, PRODUCT_SPU, FORBIDDEN_USID, FORBIDDEN_SPU
    cfg = _load_pin_config()
    PRODUCT_USID = cfg["usid"]
    PRODUCT_VMID = cfg["vmid"]
    PRODUCT_SPU = cfg["spu"]
    FORBIDDEN_USID = cfg["forbidden_usid"]
    FORBIDDEN_SPU = cfg["forbidden_spu"]


refresh_pin_constants()


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
    """Return (ok, reason, fields).

    When pin is not enforced (default public mode), always returns ok=True
    with reason 'pin disabled' — any cloud-PC may be selected.

    When CMCC_ENFORCE_PIN=1 and CMCC_PRODUCT_USID is set, fail-closed on
    missing/mismatch/forbidden SKU against env-configured expected values.
    Never logs secrets.
    """
    refresh_pin_constants()
    cfg = _load_pin_config()
    fields = load_state_product_fields(state_file)
    fields["pin_enforced"] = pin_enforced()
    fields["expected"] = {
        "usid": cfg["usid"] or None,
        "vmId": cfg["vmid"] or None,
        "spu": cfg["spu"] or None,
    }

    if not pin_enforced():
        return True, "pin disabled (CMCC_ENFORCE_PIN not set)", fields

    if not _pin_configured(cfg):
        # Enforcement requested but no expected USID — refuse to avoid silent
        # "always match empty" behaviour; operator must set CMCC_PRODUCT_USID.
        return (
            False,
            "CMCC_ENFORCE_PIN=1 but CMCC_PRODUCT_USID is empty",
            fields,
        )

    reasons: list[str] = []
    product_usid = cfg["usid"]
    product_vmid = cfg["vmid"]
    product_spu = cfg["spu"]
    forbidden_usid = cfg["forbidden_usid"]
    forbidden_spu = cfg["forbidden_spu"]

    if cli_usid is not None and str(cli_usid).strip() != "":
        if str(cli_usid).strip() != product_usid:
            reasons.append(
                f"cli --user-service-id={cli_usid!r} != PRODUCT_USID={product_usid}"
            )

    if not fields.get("state_exists"):
        reasons.append(f"missing state: {fields.get('state_path')}")
        return False, "; ".join(reasons), fields

    if fields.get("error"):
        reasons.append(str(fields["error"]))
        return False, "; ".join(reasons), fields

    usid = fields.get("selectedUserServiceId")
    desk_usid = fields.get("desk_usid")
    vmid = fields.get("lastVmId")
    spu = fields.get("lastSpuCode") or fields.get("desk_spu")

    if forbidden_usid and (usid == forbidden_usid or desk_usid == forbidden_usid):
        reasons.append(f"FORBIDDEN usid={forbidden_usid} (non-product SKU)")
    if forbidden_spu and (spu == forbidden_spu or fields.get("desk_spu") == forbidden_spu):
        reasons.append(f"FORBIDDEN spu={forbidden_spu}")

    if usid != product_usid:
        reasons.append(f"selectedUserServiceId={usid!r} != {product_usid}")
    if desk_usid is not None and desk_usid != product_usid:
        reasons.append(f"selectedDesktop.userServiceId={desk_usid!r} != {product_usid}")

    if product_spu:
        if spu is None:
            reasons.append(f"lastSpuCode/desk spu missing (need {product_spu})")
        elif spu != product_spu:
            reasons.append(f"spu={spu!r} != {product_spu}")

    if product_vmid:
        if vmid is None:
            reasons.append(f"lastVmId missing (need {product_vmid})")
        elif vmid != product_vmid:
            reasons.append(f"lastVmId={vmid!r} != {product_vmid}")

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
    refresh_pin_constants()
    print(f"[{tag}] REFUSE LIVE: product pin mismatch — {reason}", file=sys.stderr)
    safe = {
        "selectedUserServiceId": (fields or {}).get("selectedUserServiceId"),
        "lastVmId": (fields or {}).get("lastVmId"),
        "lastSpuCode": (fields or {}).get("lastSpuCode"),
        "desk_usid": (fields or {}).get("desk_usid"),
        "desk_spu": (fields or {}).get("desk_spu"),
        "expected": {
            "usid": PRODUCT_USID or None,
            "vmId": PRODUCT_VMID or None,
            "spu": PRODUCT_SPU or None,
        },
        "forbidden": {
            "usid": FORBIDDEN_USID or None,
            "spu": FORBIDDEN_SPU or None,
        },
        "pin_enforced": pin_enforced(),
    }
    print(f"[{tag}] pin_fields: {json.dumps(safe, ensure_ascii=False)}")
    print(
        f"[{tag}] hint: pin is optional; unset CMCC_ENFORCE_PIN for public use, "
        "or set CMCC_PRODUCT_USID/VMID/SPU to your own product"
    )
    return PIN_REFUSE_RC


def enforce_product_pin(
    cli_usid: str | None = None,
    state_file: Path | None = None,
    *,
    tag: str = "PRODUCT-PIN",
) -> dict:
    """Assert pin when enforced; on failure print refuse and SystemExit(RC=4).

    When pin is disabled (default), returns state fields without exiting.
    """
    ok, reason, fields = assert_product_pin(cli_usid, state_file=state_file)
    if not ok:
        refuse_pin(reason, fields, tag=tag)
        raise SystemExit(PIN_REFUSE_RC)
    return fields

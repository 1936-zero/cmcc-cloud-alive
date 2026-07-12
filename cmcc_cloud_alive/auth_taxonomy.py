# -*- coding: utf-8 -*-
"""Auth/session error taxonomy + retry budget (G6 / D1 §5.1 + §8.4).

Closed fail classes for Phase-I / Phase-T reporting. Offline-safe: no network,
no desk unlock, no password material in logs.

Maps:
  * D1 §8.4 closed FAIL set (Phase-T ladder)
  * scg_route FAIL_REASON_TAXONOMY (wire honesty flags)
  * token.py INVALID_TOKEN_CODES + transient gateway blips
  * I-G1C auth-smoke fail classes (MAIN auth / MAIN_INIT / material)

Policy highlights:
  * transient gateway/network blips NEVER force re-login
  * permanent auth failures may allow ONE re-login budget then fail-closed
  * password / token / scAuthCode never appear in safe_log_fields output
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Mapping, Optional, Set

# ---------------------------------------------------------------------------
# D1 §8.4 closed Phase-T FAIL set (authoritative for ladder reports)
# ---------------------------------------------------------------------------
PHASE_T_FAIL_CLOSED: frozenset = frozenset(
    {
        "auth_fail",
        "channel_open_fail",
        "hold_timeout",
        "vm_powered_off",
        "client_interference",
        "degraded_mode_used",
        "exception",
        "sku_mismatch",
    }
)

# ---------------------------------------------------------------------------
# Wire-level fail_reason strings used by scg_route.enforce_honesty_flags
# (empty string = PASS path). Keep in sync with scg_route.FAIL_REASON_TAXONOMY.
# ---------------------------------------------------------------------------
WIRE_FAIL_REASONS: frozenset = frozenset(
    {
        "",
        "tls_hold_mode_spice_skipped",
        "spice_main_init_timeout_or_missing",
        "auth_failed",
        "tls_hold_interrupted",
        "scg_exception",
        "unknown",
    }
)

# ---------------------------------------------------------------------------
# Auth subclasses under the D1 umbrella class "auth_fail"
# ---------------------------------------------------------------------------
AUTH_SUBCLASSES: frozenset = frozenset(
    {
        "token_invalid",  # business INVALID_TOKEN_CODES
        "token_transient",  # gateway 5xx / network — do NOT re-login
        "token_missing",  # no token / empty state
        "sc_auth_code_missing",  # material: scAuthCode empty → not SCG
        "main_channel_auth_fail",  # authenticate_channel MAIN returns non-zero
        "main_init_timeout",  # MAIN_INIT never observed
        "spice_session_missing",  # spice_session_id empty after handshake
        "pre_tls_connect_fail",  # TCP/TLS before channel auth
        "relogin_exhausted",  # re-login budget spent, still invalid
        "relogin_skipped_transient",  # deliberately no re-login on blip
    }
)

# SOHO business codes that mean "token is really bad" (from token.py).
INVALID_TOKEN_CODES: frozenset = frozenset({4014, 4015, 4016, 4017, 4200, 4201})

# Keys that must never appear unredacted in taxonomy logs/reports.
SENSITIVE_AUTH_KEYS: frozenset = frozenset(
    {
        "password",
        "vmpassword",
        "vm_password",
        "scauthcode",
        "sc_auth_code",
        "token",
        "accesstoken",
        "access_token",
        "sohotoken",
        "soho_token",
        "authorization",
        "cookie",
        "set-cookie",
        "secret",
        "passwd",
        "credential",
        "credentials",
    }
)

# ---------------------------------------------------------------------------
# Retry budgets (attempts inclusive of first try)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryBudget:
    """How many times to retry and whether re-login is allowed."""

    max_attempts: int
    allow_relogin: bool
    backoff_s: float = 1.5
    notes: str = ""


# Default budgets keyed by auth subclass or high-level class.
RETRY_BUDGETS: Dict[str, RetryBudget] = {
    "token_transient": RetryBudget(
        max_attempts=3,
        allow_relogin=False,
        backoff_s=1.5,
        notes="gateway blip; keep existing token; never re-login into 502",
    ),
    "token_invalid": RetryBudget(
        max_attempts=1,
        allow_relogin=True,
        backoff_s=0.0,
        notes="one re-login then fail-closed as auth_fail",
    ),
    "token_missing": RetryBudget(
        max_attempts=1,
        allow_relogin=True,
        backoff_s=0.0,
        notes="login_from_cached_credentials once if available",
    ),
    "main_channel_auth_fail": RetryBudget(
        max_attempts=1,
        allow_relogin=False,
        backoff_s=0.0,
        notes="ticket/HyScg material issue; fail-closed, no blind retry",
    ),
    "main_init_timeout": RetryBudget(
        max_attempts=1,
        allow_relogin=False,
        backoff_s=0.0,
        notes="handshake incomplete; fail-closed for smoke",
    ),
    "pre_tls_connect_fail": RetryBudget(
        max_attempts=2,
        allow_relogin=False,
        backoff_s=1.0,
        notes="network only; short retry then channel_open_fail",
    ),
    "sc_auth_code_missing": RetryBudget(
        max_attempts=1,
        allow_relogin=False,
        backoff_s=0.0,
        notes="material missing; not an SCG route",
    ),
    "relogin_exhausted": RetryBudget(
        max_attempts=0,
        allow_relogin=False,
        backoff_s=0.0,
        notes="budget spent",
    ),
    # High-level Phase-T classes (defaults)
    "auth_fail": RetryBudget(max_attempts=1, allow_relogin=True, backoff_s=0.0),
    "channel_open_fail": RetryBudget(
        max_attempts=2, allow_relogin=False, backoff_s=1.0
    ),
    "hold_timeout": RetryBudget(max_attempts=0, allow_relogin=False, backoff_s=0.0),
    "exception": RetryBudget(max_attempts=0, allow_relogin=False, backoff_s=0.0),
    "degraded_mode_used": RetryBudget(
        max_attempts=0, allow_relogin=False, backoff_s=0.0
    ),
    "vm_powered_off": RetryBudget(max_attempts=0, allow_relogin=False, backoff_s=0.0),
    "client_interference": RetryBudget(
        max_attempts=0, allow_relogin=False, backoff_s=0.0
    ),
    "sku_mismatch": RetryBudget(max_attempts=0, allow_relogin=False, backoff_s=0.0),
}

# ---------------------------------------------------------------------------
# Lane ownership map (hive) — who investigates which fail class
# ---------------------------------------------------------------------------
LANE_MAP: Dict[str, str] = {
    "token_invalid": "I-AUTH-SMOKE / product auth",
    "token_transient": "I-AUTH-SMOKE / gateway ops",
    "token_missing": "I-AUTH-SMOKE / product auth",
    "sc_auth_code_missing": "I-AUTH-SMOKE / CEM material",
    "main_channel_auth_fail": "I-AUTH-SMOKE / ticket-HyScg P0",
    "main_init_timeout": "I-AUTH-SMOKE / spice handshake",
    "spice_session_missing": "I-AUTH-SMOKE / spice handshake",
    "pre_tls_connect_fail": "I-AUTH-SMOKE / network",
    "relogin_exhausted": "I-AUTH-SMOKE / product auth",
    "relogin_skipped_transient": "I-AUTH-SMOKE / gateway ops",
    "auth_fail": "I-AUTH-SMOKE",
    "channel_open_fail": "I-AUTH-SMOKE / hold wire",
    "hold_timeout": "I-HOLD / dual-plane",
    "vm_powered_off": "I-G4 KPI / VM sample",
    "client_interference": "Phase-T operator",
    "degraded_mode_used": "I-PHASE-I-FLAGS honesty",
    "exception": "owner of raising module",
    "sku_mismatch": "product material",
    # wire reasons
    "auth_failed": "I-AUTH-SMOKE / ticket-HyScg P0",
    "spice_main_init_timeout_or_missing": "I-AUTH-SMOKE",
    "tls_hold_mode_spice_skipped": "I-PHASE-I-FLAGS (not auth smoke)",
    "tls_hold_interrupted": "I-HOLD",
    "scg_exception": "scg_route",
    "unknown": "triage",
}


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------


@dataclass
class AuthClass:
    """Normalized classification of an auth/session failure."""

    phase_t_class: str  # member of PHASE_T_FAIL_CLOSED or "" for pass/unknown soft
    auth_subclass: str  # member of AUTH_SUBCLASSES or ""
    wire_reason: str  # member of WIRE_FAIL_REASONS (or raw coerced)
    retry: RetryBudget
    lane: str
    permanent: bool  # True => do not retry / do not treat as transient
    allow_relogin: bool
    detail: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["retry"] = asdict(self.retry)
        return d


def get_retry_budget(key: str) -> RetryBudget:
    if key in RETRY_BUDGETS:
        return RETRY_BUDGETS[key]
    return RetryBudget(max_attempts=0, allow_relogin=False, notes="default fail-closed")


def redact_sensitive(obj: Any) -> Any:
    """Recursively redact password/token-like keys. Safe for reports/logs."""
    if isinstance(obj, Mapping):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).lower().replace("-", "_")
            if lk in SENSITIVE_AUTH_KEYS or any(
                s in lk for s in ("password", "token", "authcode", "secret", "passwd")
            ):
                out[str(k)] = "<redacted>"
            else:
                out[str(k)] = redact_sensitive(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_sensitive(x) for x in obj]
    if isinstance(obj, str):
        # never return huge opaque blobs that might be tokens
        if len(obj) > 64 and obj.isalnum():
            return "<redacted:long>"
        return obj
    return obj


def safe_log_fields(
    phase_t_class: str = "",
    auth_subclass: str = "",
    wire_reason: str = "",
    code: Any = None,
    msg: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Build a log/report dict that is guaranteed free of password material."""
    base = {
        "phase_t_class": phase_t_class or "",
        "auth_subclass": auth_subclass or "",
        "wire_reason": wire_reason or "",
        "code": code,
        "msg": str(msg)[:200] if msg is not None else "",
    }
    base.update(kwargs)
    return redact_sensitive(base)


def classify_token_response(response: Optional[Mapping[str, Any]]) -> AuthClass:
    """Classify a token.check_token / ensure_token response dict."""
    if not isinstance(response, Mapping):
        return AuthClass(
            phase_t_class="auth_fail",
            auth_subclass="token_missing",
            wire_reason="auth_failed",
            retry=get_retry_budget("token_missing"),
            lane=LANE_MAP["token_missing"],
            permanent=False,
            allow_relogin=True,
            detail="non-dict token response",
        )

    transient = bool(response.get("transient"))
    try:
        code = int(response.get("code") or 0)
    except (TypeError, ValueError):
        code = 0
    msg = str(response.get("msg") or "")

    if transient:
        return AuthClass(
            phase_t_class="auth_fail",
            auth_subclass="token_transient",
            wire_reason="auth_failed",
            retry=get_retry_budget("token_transient"),
            lane=LANE_MAP["token_transient"],
            permanent=False,
            allow_relogin=False,
            detail="transient gateway/network; skip re-login",
            extra=safe_log_fields(code=code, msg=msg),
        )

    if code in INVALID_TOKEN_CODES or (code != 2000 and code != 0):
        # business invalid (or non-success non-transient)
        if code in INVALID_TOKEN_CODES or code not in (0, 2000):
            return AuthClass(
                phase_t_class="auth_fail",
                auth_subclass="token_invalid",
                wire_reason="auth_failed",
                retry=get_retry_budget("token_invalid"),
                lane=LANE_MAP["token_invalid"],
                permanent=True,
                allow_relogin=True,
                detail=f"business token invalid code={code}",
                extra=safe_log_fields(code=code, msg=msg),
            )

    if code == 2000:
        return AuthClass(
            phase_t_class="",
            auth_subclass="",
            wire_reason="",
            retry=RetryBudget(max_attempts=0, allow_relogin=False, notes="token ok"),
            lane="",
            permanent=False,
            allow_relogin=False,
            detail="token valid",
            extra=safe_log_fields(code=code),
        )

    return AuthClass(
        phase_t_class="auth_fail",
        auth_subclass="token_missing",
        wire_reason="auth_failed",
        retry=get_retry_budget("token_missing"),
        lane=LANE_MAP["token_missing"],
        permanent=False,
        allow_relogin=True,
        detail="empty or unknown token response",
        extra=safe_log_fields(code=code, msg=msg),
    )


def classify_wire_fail_reason(fail_reason: Optional[str]) -> AuthClass:
    """Map scg_route fail_reason string → AuthClass + Phase-T umbrella."""
    fr = "" if fail_reason is None else str(fail_reason)
    if fr not in WIRE_FAIL_REASONS:
        # coerce like enforce_honesty_flags
        raw = fr
        fr = "unknown"
        return AuthClass(
            phase_t_class="exception",
            auth_subclass="",
            wire_reason="unknown",
            retry=get_retry_budget("exception"),
            lane=LANE_MAP["unknown"],
            permanent=True,
            allow_relogin=False,
            detail=f"coerced unknown wire reason; raw={raw[:80]}",
        )

    if fr == "":
        return AuthClass(
            phase_t_class="",
            auth_subclass="",
            wire_reason="",
            retry=RetryBudget(max_attempts=0, allow_relogin=False, notes="pass"),
            lane="",
            permanent=False,
            allow_relogin=False,
            detail="PASS path",
        )

    if fr == "auth_failed":
        return AuthClass(
            phase_t_class="auth_fail",
            auth_subclass="main_channel_auth_fail",
            wire_reason=fr,
            retry=get_retry_budget("main_channel_auth_fail"),
            lane=LANE_MAP["auth_failed"],
            permanent=True,
            allow_relogin=False,
            detail="MAIN authenticate_channel failed",
        )

    if fr == "spice_main_init_timeout_or_missing":
        return AuthClass(
            phase_t_class="channel_open_fail",
            auth_subclass="main_init_timeout",
            wire_reason=fr,
            retry=get_retry_budget("main_init_timeout"),
            lane=LANE_MAP[fr],
            permanent=True,
            allow_relogin=False,
            detail="MAIN_INIT not observed",
        )

    if fr == "tls_hold_mode_spice_skipped":
        return AuthClass(
            phase_t_class="degraded_mode_used",
            auth_subclass="",
            wire_reason=fr,
            retry=get_retry_budget("degraded_mode_used"),
            lane=LANE_MAP[fr],
            permanent=True,
            allow_relogin=False,
            detail="tls_hold is not auth-smoke evidence",
        )

    if fr == "tls_hold_interrupted":
        return AuthClass(
            phase_t_class="hold_timeout",
            auth_subclass="",
            wire_reason=fr,
            retry=get_retry_budget("hold_timeout"),
            lane=LANE_MAP[fr],
            permanent=True,
            allow_relogin=False,
            detail="tls_hold loop interrupted",
        )

    if fr == "scg_exception":
        return AuthClass(
            phase_t_class="exception",
            auth_subclass="",
            wire_reason=fr,
            retry=get_retry_budget("exception"),
            lane=LANE_MAP[fr],
            permanent=True,
            allow_relogin=False,
            detail="unhandled scg_route exception",
        )

    # unknown already handled; remaining wire reasons
    return AuthClass(
        phase_t_class="exception",
        auth_subclass="",
        wire_reason=fr,
        retry=get_retry_budget("exception"),
        lane=LANE_MAP.get(fr, "triage"),
        permanent=True,
        allow_relogin=False,
        detail=fr,
    )


def classify_spice_handshake(
    *,
    auth_ok: bool,
    spice_session_id: Any = None,
    connected_channels: Optional[Any] = None,
    sc_auth_code_present: bool = True,
    pre_tls_error: Optional[str] = None,
) -> AuthClass:
    """Classify offline/mockable spice_handshake outcomes (I-G1C predicates)."""
    if pre_tls_error:
        return AuthClass(
            phase_t_class="channel_open_fail",
            auth_subclass="pre_tls_connect_fail",
            wire_reason="scg_exception",
            retry=get_retry_budget("pre_tls_connect_fail"),
            lane=LANE_MAP["pre_tls_connect_fail"],
            permanent=False,
            allow_relogin=False,
            detail=str(pre_tls_error)[:120],
        )

    if not sc_auth_code_present:
        return AuthClass(
            phase_t_class="auth_fail",
            auth_subclass="sc_auth_code_missing",
            wire_reason="auth_failed",
            retry=get_retry_budget("sc_auth_code_missing"),
            lane=LANE_MAP["sc_auth_code_missing"],
            permanent=True,
            allow_relogin=False,
            detail="scAuthCode empty; not SCG route",
        )

    if not auth_ok:
        return AuthClass(
            phase_t_class="auth_fail",
            auth_subclass="main_channel_auth_fail",
            wire_reason="auth_failed",
            retry=get_retry_budget("main_channel_auth_fail"),
            lane=LANE_MAP["main_channel_auth_fail"],
            permanent=True,
            allow_relogin=False,
            detail="MAIN channel auth non-zero",
        )

    if not spice_session_id:
        return AuthClass(
            phase_t_class="channel_open_fail",
            auth_subclass="main_init_timeout",
            wire_reason="spice_main_init_timeout_or_missing",
            retry=get_retry_budget("main_init_timeout"),
            lane=LANE_MAP["main_init_timeout"],
            permanent=True,
            allow_relogin=False,
            detail="spice_session_id empty after handshake",
            extra={
                "connected_channels": list(connected_channels or []),
            },
        )

    return AuthClass(
        phase_t_class="",
        auth_subclass="",
        wire_reason="",
        retry=RetryBudget(max_attempts=0, allow_relogin=False, notes="handshake ok"),
        lane="",
        permanent=False,
        allow_relogin=False,
        detail="auth smoke PASS predicates met",
        extra={
            "spice_session_id": bool(spice_session_id),
            "connected_channels": list(connected_channels or []),
        },
    )


def map_to_phase_t(fail_reason_or_class: str) -> str:
    """Normalize any known label to D1 §8.4 closed set (or empty PASS)."""
    s = str(fail_reason_or_class or "")
    if s in PHASE_T_FAIL_CLOSED:
        return s
    if s in ("", "pass", "ok"):
        return ""
    cls = classify_wire_fail_reason(s)
    if cls.phase_t_class:
        return cls.phase_t_class
    if s in AUTH_SUBCLASSES:
        if s.startswith("token_") or s in (
            "main_channel_auth_fail",
            "sc_auth_code_missing",
            "relogin_exhausted",
            "relogin_skipped_transient",
            "spice_session_missing",
        ):
            return "auth_fail"
        if s in ("main_init_timeout", "pre_tls_connect_fail"):
            return "channel_open_fail"
    return "exception"


def should_relogin(auth_class: AuthClass, relogin_attempts_used: int = 0) -> bool:
    """Whether policy allows another re-login given budget."""
    if not auth_class.allow_relogin:
        return False
    budget = auth_class.retry
    if not budget.allow_relogin:
        return False
    # max_attempts counts first try; re-login is at most one for token_invalid
    return relogin_attempts_used < 1 and budget.allow_relogin


def should_retry_transport(auth_class: AuthClass, attempts_used: int) -> bool:
    """Whether to retry transport (token check / pre-TLS) without re-login."""
    budget = auth_class.retry
    if budget.max_attempts <= 0:
        return False
    return attempts_used < budget.max_attempts and not auth_class.permanent



def annotate_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Attach fail-closed auth taxonomy fields onto a result dict (in-place + return).

    Adds (when classifyable from fail_reason / spice handshake fields):
      - auth_class / auth_subclass / phase_t_class
      - auth_lane / auth_permanent / auth_allow_relogin
      - auth_retry (dict of RetryBudget)
      - auth_detail (safe, redacted)

    Never raises; never logs secrets. Safe to call after enforce_honesty_flags.
    """
    if not isinstance(result, dict):
        return result  # type: ignore[return-value]
    try:
        fr = result.get("fail_reason")
        # Prefer wire-level classification when fail_reason present
        if fr is not None and str(fr) != "":
            ac = classify_wire_fail_reason(str(fr))
        else:
            # Prefer explicit wire PASS; optionally refine via spice handshake fields
            auth_failed = bool(result.get("auth_failed"))
            spice_ok = result.get("spice_ok")
            spice_sess = (
                result.get("spice_session_id")
                or result.get("spice_session")
                or result.get("session_id")
            )
            main_init = result.get("main_init_ok")
            # Only invoke handshake classifier when we have negative signals
            if auth_failed or spice_ok is False or main_init is False or spice_sess in (None, "", False):
                ac = classify_spice_handshake(
                    auth_ok=not auth_failed and spice_ok is not False,
                    spice_session_id=spice_sess if spice_sess not in (None, "", False) else None,
                    sc_auth_code_present=bool(result.get("sc_auth_code_present", True)),
                    pre_tls_error=result.get("pre_tls_error"),
                )
            else:
                ac = classify_wire_fail_reason("")
        d = ac.to_dict()
        result["auth_class"] = d.get("phase_t_class") or d.get("auth_subclass") or ""
        result["auth_subclass"] = d.get("auth_subclass") or ""
        result["phase_t_class"] = d.get("phase_t_class") or ""
        result["auth_lane"] = d.get("lane") or ""
        result["auth_permanent"] = bool(d.get("permanent"))
        result["auth_allow_relogin"] = bool(d.get("allow_relogin"))
        result["auth_retry"] = d.get("retry") or {}
        result["auth_detail"] = str(d.get("detail") or "")[:200]
        # nested full object for consumers that want everything
        result["auth_taxonomy"] = d
        # P1-5 DiD: non-PASS taxonomy must not leave spice_ok=True (sparse honesty)
        ptc = result.get("phase_t_class") or ""
        if ptc in PHASE_T_FAIL_CLOSED or (ptc and ptc not in ("", "pass", "ok")):
            if result.get("spice_ok") is not False:
                result["spice_ok"] = False
    except Exception as exc:  # fail-closed annotate never breaks keepalive path
        result.setdefault("auth_class", "exception")
        result.setdefault("phase_t_class", "exception")
        result.setdefault("auth_detail", f"annotate_error:{type(exc).__name__}")
        # exception path is non-PASS: clear spice_ok if still truthy
        if result.get("spice_ok") is not False:
            result["spice_ok"] = False
    return result

__all__ = [
    "PHASE_T_FAIL_CLOSED",
    "WIRE_FAIL_REASONS",
    "AUTH_SUBCLASSES",
    "INVALID_TOKEN_CODES",
    "SENSITIVE_AUTH_KEYS",
    "RETRY_BUDGETS",
    "LANE_MAP",
    "RetryBudget",
    "AuthClass",
    "get_retry_budget",
    "redact_sensitive",
    "safe_log_fields",
    "classify_token_response",
    "classify_wire_fail_reason",
    "classify_spice_handshake",
    "map_to_phase_t",
    "should_relogin",
    "should_retry_transport",
    "annotate_result",
]
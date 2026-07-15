"""Starlette WebUI for multi-profile keepalive orchestration (J3).

Parent process only: REST + SSE + static shell. Does NOT run keepalive loops
on the ASGI event-loop thread. Uses in-memory FakeOrchestrator until J2 lands
`cmcc_cloud_alive.webui.orchestrator` (same method names).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Paths / env
# ---------------------------------------------------------------------------

_STATIC_DIR = Path(__file__).resolve().parent / "static"

def _data_dir() -> Path:
    """Unified durable root shared with CLI (X8).

    Priority:
    1. CMCC_DATA_DIR if set (explicit override; may point at either the
       package root or the final data root)
    2. else ``$CMCC_ALIVE_HOME|HOME|~/.cmcc-cloud-alive`` — always the
       ``.cmcc-cloud-alive`` package dir so Docker HOME=/data matches
       entrypoint + core DEFAULT_DATA_DIR (``/data/.cmcc-cloud-alive``).
    """
    explicit = os.environ.get("CMCC_DATA_DIR")
    if explicit:
        p = Path(explicit)
        # Accept either the package root or the volume root.
        if p.name == ".cmcc-cloud-alive":
            return p
        # Common Docker mistake: CMCC_DATA_DIR=/data — nest under package dir.
        return p / ".cmcc-cloud-alive"
    raw = os.environ.get("CMCC_ALIVE_HOME") or os.environ.get("HOME") or str(Path.home())
    home = Path(raw)
    if home.name == ".cmcc-cloud-alive":
        return home
    return home / ".cmcc-cloud-alive"


_LEGACY_PROFILES_MIGRATED = False


def _legacy_profiles_dirs(unified: Path) -> List[Path]:
    """Pre-X8 WebUI wrote profiles under /data/profiles when HOME=/data."""
    candidates: List[Path] = []
    # Sibling of package root: /data/profiles next to /data/.cmcc-cloud-alive
    sibling = unified.parent / "profiles"
    if sibling != (unified / "profiles"):
        candidates.append(sibling)
    # Bare CMCC_DATA_DIR=/data historical
    bare = Path("/data/profiles")
    if bare not in candidates:
        candidates.append(bare)
    return candidates


def _migrate_legacy_profiles(dest: Path) -> int:
    """Copy missing profile JSON from legacy roots into unified profiles/.

    Never overwrites a newer/same-name file already in dest. Returns count
    of files copied. Best-effort; failures are non-fatal.
    """
    global _LEGACY_PROFILES_MIGRATED
    moved = 0
    try:
        dest.mkdir(parents=True, exist_ok=True)
        for legacy in _legacy_profiles_dirs(dest.parent):
            if not legacy.is_dir():
                continue
            if legacy.resolve() == dest.resolve():
                continue
            for src in legacy.glob("*.json"):
                target = dest / src.name
                if target.exists():
                    continue
                try:
                    target.write_bytes(src.read_bytes())
                    try:
                        os.chmod(target, 0o600)
                    except OSError:
                        pass
                    moved += 1
                except OSError:
                    continue
    finally:
        _LEGACY_PROFILES_MIGRATED = True
    return moved


def profiles_dir() -> Path:
    d = _data_dir() / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    # One-shot best-effort migration so old /data/profiles stay visible.
    if not _LEGACY_PROFILES_MIGRATED:
        _migrate_legacy_profiles(d)
    return d


def _now_iso() -> str:
    # HARD_GATE#861: force Asia/Shanghai so API/orch timestamps match child short_time
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    except Exception:
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

try:
    from cmcc_cloud_alive.core import SENSITIVE_REPORT_KEYS as _CORE_SENSITIVE
except Exception:  # pragma: no cover — package may be partial in unit smoke
    _CORE_SENSITIVE = {
        "accessToken",
        "authorization",
        "authPayload",
        "clientId",
        "connectStr",
        "cpsid",
        "jwt",
        "password",
        "sohoToken",
        "token",
    }

_SENSITIVE_LOWER = {k.lower() for k in _CORE_SENSITIVE} | {
    "refreshtoken",
    "accesstoken",
    "sohotoken",
    "authorization",
}


def _mask_username(u: Optional[str]) -> str:
    if not u:
        return ""
    s = str(u)
    if len(s) <= 4:
        return "*" * len(s)
    return s[:3] + "****" + s[-2:]


def redact_obj(value: Any, key: str = "") -> Any:
    if key and key.lower() in _SENSITIVE_LOWER:
        return "<redacted>"
    if isinstance(value, dict):
        return {k: redact_obj(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_obj(v, key) for v in value]
    return value


def api_error(code: str, message: str, status: int = 400, next_step: str = "") -> JSONResponse:
    body: Dict[str, Any] = {
        "ok": False,
        "error": {"code": code, "message": message},
    }
    if next_step:
        body["error"]["nextStep"] = next_step
    return JSONResponse(body, status_code=status)


# WAVE7 frozen contract: intervalSec/trafficSec/durationSec -> CLI flags
_DEFAULT_INTERVAL_SEC = 300
_DEFAULT_TRAFFIC_SEC = 60
_DEFAULT_DURATION_SEC = 0


def _parse_positive_int(raw: Any, field: str, *, allow_zero: bool = False) -> int:
    """Parse body field as int. allow_zero=True for durationSec (0=forever)."""
    try:
        if isinstance(raw, bool):
            raise ValueError
        val = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer")
    if allow_zero:
        if val < 0:
            raise ValueError(f"{field} must be >= 0")
    elif val <= 0:
        raise ValueError(f"{field} must be > 0")
    return val


def parse_job_timing_fields(body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Parse optional timing fields; missing -> defaults. Returns fields + extraArgs.

    Accepts FE alias ``intervalMin`` (minutes) when ``intervalSec`` is absent.
    """
    body = body or {}
    if "intervalSec" in body and body.get("intervalSec") is not None:
        interval = _parse_positive_int(body.get("intervalSec"), "intervalSec")
    elif "intervalMin" in body and body.get("intervalMin") is not None:
        # FE draft uses minutes; convert to seconds for orchestrator/CLI.
        minutes = _parse_positive_int(body.get("intervalMin"), "intervalMin")
        interval = minutes * 60
    else:
        interval = _DEFAULT_INTERVAL_SEC
    if "trafficSec" in body and body.get("trafficSec") is not None:
        traffic = _parse_positive_int(body.get("trafficSec"), "trafficSec")
    else:
        traffic = _DEFAULT_TRAFFIC_SEC
    if "durationSec" in body and body.get("durationSec") is not None:
        duration = _parse_positive_int(body.get("durationSec"), "durationSec", allow_zero=True)
    else:
        duration = _DEFAULT_DURATION_SEC
    # simple-keepalive argv (align Python menu): minutes + traffic seconds + mode
    # mode "1"=单轮, "2"=永久. durationSec==0 => forever; >0 => single round.
    interval_minutes = max(1, int(interval) // 60)
    simple_mode = "2" if int(duration) == 0 else "1"
    body_mode = str((body or {}).get("mode") or "").lower()
    if body_mode in ("once", "single", "dry-run", "dryrun"):
        simple_mode = "1"
    elif body_mode in ("live", "forever", "permanent", "loop"):
        if int(duration) == 0:
            simple_mode = "2"
    extra_args = [
        "--interval-minutes",
        str(interval_minutes),
        "--traffic-seconds",
        str(traffic),
        "--mode",
        simple_mode,
    ]
    return {
        "intervalSec": interval,
        "trafficSec": traffic,
        "durationSec": duration,
        "extraArgs": extra_args,
    }


# ---------------------------------------------------------------------------
# Access token gate (file > env; 8317-style login shell)
# ---------------------------------------------------------------------------

_ACCESS_TOKEN_FILENAME = "webui_access_token"


def _access_token_path() -> Path:
    return _data_dir() / _ACCESS_TOKEN_FILENAME


def _read_access_token() -> str:
    """Resolve expected WebUI access token.

    Priority:
    1. durable file under data dir (UI setup / change)
    2. CMCC_WEBUI_TOKEN env (.env / compose)
    """
    try:
        p = _access_token_path()
        if p.is_file():
            raw = p.read_text(encoding="utf-8", errors="replace").strip()
            # accept single-line token only; ignore comments/blank
            for line in raw.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                return s
    except OSError:
        pass
    return (os.environ.get("CMCC_WEBUI_TOKEN") or "").strip()


def _write_access_token(token: str) -> Path:
    """Persist access token to data dir (mode 0600). Returns path."""
    token = (token or "").strip()
    if not token:
        raise ValueError("empty token")
    if len(token) < 4:
        raise ValueError("token too short (min 4)")
    if len(token) > 256:
        raise ValueError("token too long (max 256)")
    # reject whitespace / control chars
    if any(c.isspace() for c in token):
        raise ValueError("token must not contain whitespace")
    root = _data_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = _access_token_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _clear_access_token() -> Path:
    """Remove file-based access token (disable gate). Env CMCC_WEBUI_TOKEN still wins if set."""
    path = _access_token_path()
    try:
        if path.is_file():
            path.unlink()
    except OSError as e:
        raise OSError(f"无法删除访问密钥文件: {e}") from e
    return path


def _extract_request_token(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (
        request.headers.get("x-api-token")
        or request.query_params.get("token")
        or ""
    ).strip()


def _token_ok(provided: str, expected: str) -> bool:
    if not expected or not provided:
        return False
    # secrets.compare_digest requires equal length; pad-safe via hmac style length check
    try:
        return secrets.compare_digest(provided, expected)
    except (TypeError, ValueError):
        return False


class OptionalTokenMiddleware(BaseHTTPMiddleware):
    """Gate business APIs behind access token (file or env).

    Open always: shell HTML, static, health, system/info, auth setup/login/status.
    gate6:
    - no token configured → open access (auth disabled)
    - token configured → require valid Bearer / x-api-token / ?token=
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Always open: health aliases (compose/X2/T3) + static + root shell + auth bootstrap
        # FLAG#59: /api/health must match docker HEALTHCHECK
        open_exact = {
            "/",
            "/index.html",
            "/health",
            "/api/health",
            "/api/system/health",
            # X9: allow FE to discover tokenRequired / setupRequired before Bearer set
            "/api/system/info",
            "/api/info",
            "/api/auth/status",
            "/api/auth/setup",
            "/api/auth/login",
        }
        open_prefixes = ("/static/", "/favicon")
        if path in open_exact or path.startswith(open_prefixes):
            return await call_next(request)

        expected = _read_access_token()
        # gate6: no token configured → open access (auth disabled)
        if not expected:
            return await call_next(request)

        token = _extract_request_token(request)
        if not _token_ok(token, expected):
            return api_error(
                "TOKEN_INVALID",
                "访问密钥无效或缺失",
                401,
                next_step="请在登录门输入正确访问密钥，或在请求头携带 Bearer / x-api-token",
            )
        return await call_next(request)


# ---------------------------------------------------------------------------
# Fake orchestrator (stable shape for J2 swap)
# ---------------------------------------------------------------------------

class FakeOrchestrator:
    """In-memory job table. Method names match planned J2 orchestrator."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: Dict[str, Dict[str, Any]] = {}  # job_id -> job
        self._by_profile: Dict[str, str] = {}  # profile_id -> job_id
        self._log_buffers: Dict[str, List[Dict[str, str]]] = {}
        self._subscribers: List[asyncio.Queue] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def _emit(self, event: str, data: Dict[str, Any]) -> None:
        payload = {"event": event, "data": data}
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(j) for j in self._jobs.values()]

    def get_status(self, profile_id: str) -> Dict[str, Any]:
        with self._lock:
            jid = self._by_profile.get(profile_id)
            if not jid:
                return {"profileId": profile_id, "status": "idle", "jobId": None}
            j = self._jobs.get(jid) or {}
            return {
                "profileId": profile_id,
                "status": j.get("status", "unknown"),
                "jobId": jid,
                "protocol": j.get("protocol"),
                "pid": j.get("pid"),
                "startedAt": j.get("startedAt"),
            }

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            j = self._jobs.get(job_id)
            return dict(j) if j else None

    def start_job(
        self,
        profile_id: str,
        state_path: Path,
        protocol: str = "ZTE",
        extra_args: Optional[List[str]] = None,
        mode: str = "live",
        interval_sec: Optional[int] = None,
        traffic_sec: Optional[int] = None,
        duration_sec: Optional[int] = None,
    ) -> Dict[str, Any]:
        protocol = (protocol or "ZTE").upper()
        if protocol not in ("ZTE", "SCG"):
            raise ValueError("protocol must be ZTE or SCG")
        with self._lock:
            existing = self._by_profile.get(profile_id)
            if existing and self._jobs.get(existing, {}).get("status") == "running":
                raise RuntimeError("PROFILE_IN_USE")
            job_id = uuid.uuid4().hex[:12]
            job = {
                "id": job_id,
                "jobId": job_id,
                "profileId": profile_id,
                "statePath": str(state_path),
                "protocol": protocol,
                "mode": mode or "live",
                "status": "running",
                "pid": None,  # fake: no subprocess yet (J2)
                "startedAt": _now_iso(),
                "stoppedAt": None,
                "detail": "fake orchestrator dry-run (no LIVE child)",
                "extraArgs": list(extra_args or []),
                "intervalSec": interval_sec,
                "trafficSec": traffic_sec,
                "durationSec": duration_sec,
            }
            self._jobs[job_id] = job
            self._by_profile[profile_id] = job_id
            self._log_buffers.setdefault(job_id, []).append(
                {"at": _now_iso(), "line": f"[fake] start {protocol} mode={job['mode']} state={state_path.name}"}
            )
            self._emit(
                "job_status",
                {
                    "jobId": job_id,
                    "profileId": profile_id,
                    "status": "running",
                    "at": job["startedAt"],
                    "detail": job["detail"],
                },
            )
            return dict(job)

    def stop_job(self, profile_id: str) -> Dict[str, Any]:
        with self._lock:
            jid = self._by_profile.get(profile_id)
            if not jid or jid not in self._jobs:
                raise KeyError("NOT_FOUND")
            job = self._jobs[jid]
            if job.get("status") != "running":
                return dict(job)
            job["status"] = "stopped"
            job["stoppedAt"] = _now_iso()
            job["detail"] = "stopped by API"
            self._log_buffers.setdefault(jid, []).append(
                {"at": job["stoppedAt"], "line": "[fake] stop requested"}
            )
            self._emit(
                "job_status",
                {
                    "jobId": jid,
                    "profileId": profile_id,
                    "status": "stopped",
                    "at": job["stoppedAt"],
                },
            )
            return dict(job)

    def recent_logs(self, job_id: Optional[str] = None, profile_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, str]]:
        """Return job/card logs only when scoped.

        HARD_GATE#768-B / ASSIGN#785#4: unscoped /api/logs must not flatten
        job buffers into page-level global log. Card logs stay profile/job scoped.
        """
        with self._lock:
            if not job_id and profile_id:
                job_id = self._by_profile.get(profile_id)
            if not job_id:
                return []
            return list(self._log_buffers.get(job_id, []))[-limit:]


def _load_orchestrator() -> Any:
    try:
        from cmcc_cloud_alive.webui.orchestrator import Orchestrator  # type: ignore

        return Orchestrator()
    except Exception:
        return FakeOrchestrator()


ORCH: Any = _load_orchestrator()


# ---------------------------------------------------------------------------
# Profile store (filesystem under DATA/profiles)
# ---------------------------------------------------------------------------

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_profile_id(name: str) -> str:
    name = (name or "").strip().replace(" ", "-")
    name = _SAFE_NAME.sub("-", name).strip("-._")
    if not name:
        name = "profile"
    return name[:60]


def _profile_path(profile_id: str) -> Path:
    return profiles_dir() / f"{profile_id}.json"


# HARD_GATE#868: same account shares one token/session state (like interactive).
# Card profile JSON keeps UI meta + selected userServiceId; live child uses acct_*.json.
# userId/isSubAccount/loginMode MUST sync: checkToken needs X-SOHO-UserId; re-login
# path uses isSubAccount/loginMode to pick sub vs main password login (4305/90020176).
_SHARED_ACCOUNT_KEYS = (
    "username",
    "password",
    "passwordSavedAt",
    "sohoToken",
    "token",
    "userId",
    "phone",
    "isSubAccount",
    "loginMode",
    "isLogined",
    "deviceId",
    "device_id",
    "clientProfile",
    "clientId",
    "lastLoginStatus",
    "lastLoginAttemptAt",
    "lastLoginError",
)


def _account_key(username: str) -> str:
    return _safe_profile_id(username or "unknown")


def _shared_account_path(username: str) -> Path:
    return profiles_dir() / f"acct_{_account_key(username)}.json"


def _is_shared_account_file(path: Path) -> bool:
    return path.name.startswith("acct_") and path.suffix == ".json"


def _sync_shared_account(state: Dict[str, Any]) -> Optional[Path]:
    """Merge session fields into acct_<user>.json; return shared path or None.

    HARD_GATE#868: same account shares one token. Stale per-card tokens must
    NOT clobber a good shared token on start/hydrate. Token overwrite is only
    allowed when the card just established a session (login path), or shared
    has no token yet.
    """
    username = str(state.get("username") or state.get("phone") or "").strip()
    if not username:
        return None
    shared = _shared_account_path(username)
    existing = _read_state(shared) if shared.is_file() else {}
    merged = dict(existing) if isinstance(existing, dict) else {}

    token_keys = ("sohoToken", "token")
    device_keys = ("deviceId", "device_id")

    # Non-token shared keys: non-empty card value wins (except deviceId below).
    for k in _SHARED_ACCOUNT_KEYS:
        if k in token_keys or k in device_keys:
            continue
        if k in state and state.get(k) not in (None, ""):
            merged[k] = state[k]

    # deviceId: prefer stable shared value so dual cards don't mint two devices.
    for dk in device_keys:
        card_dev = state.get(dk)
        shared_dev = merged.get(dk)
        if shared_dev in (None, "") and card_dev not in (None, ""):
            merged[dk] = card_dev
        # else keep shared / existing

    # Token policy: protect shared sohoToken from stale card overwrite.
    status = str(state.get("lastLoginStatus") or "")
    fresh_login = status in (
        "session-established",
        "session-present",
        "live-ok-no-token",
    )
    for tk in token_keys:
        card_tok = state.get(tk)
        if card_tok in (None, ""):
            continue
        shared_tok = merged.get(tk)
        if shared_tok in (None, "") or card_tok == shared_tok or fresh_login:
            merged[tk] = card_tok
        # else keep shared_tok (card is stale / partial)

    # Prefer non-empty token from either side (fill holes only).
    for tk in token_keys:
        if not merged.get(tk) and state.get(tk):
            merged[tk] = state[tk]

    merged["username"] = username
    merged["updatedAt"] = _now_iso()
    merged["sharedAccount"] = True
    _write_state(shared, merged)
    return shared



def _normalize_client_profile(value: Any, default: str = "linux") -> str:
    """Accept linux|windows|mac (case-insensitive); invalid → default."""
    v = str(value or "").strip().lower()
    if v in ("linux", "windows", "mac"):
        return v
    return default


def _apply_client_profile_from_body(state: Dict[str, Any], body: Optional[Dict[str, Any]]) -> bool:
    """If body carries clientProfile, write normalized value onto card state.

    Returns True when state was changed.
    """
    if not isinstance(body, dict) or "clientProfile" not in body:
        return False
    raw = body.get("clientProfile")
    if raw is None or str(raw).strip() == "":
        return False
    new_v = _normalize_client_profile(raw, default="")
    if not new_v:
        return False
    old = _normalize_client_profile(state.get("clientProfile"), default="")
    if old == new_v:
        # still ensure canonical form
        if state.get("clientProfile") != new_v:
            state["clientProfile"] = new_v
            return True
        return False
    state["clientProfile"] = new_v
    return True


def _hydrate_profile_from_shared(state: Dict[str, Any]) -> Dict[str, Any]:
    """Fill missing token/password from shared account file (card keeps own usid)."""
    username = str(state.get("username") or state.get("phone") or "").strip()
    if not username:
        return state
    shared_path = _shared_account_path(username)
    if not shared_path.is_file():
        return state
    shared = _read_state(shared_path)
    if not shared:
        return state
    out = dict(state)
    for k in _SHARED_ACCOUNT_KEYS:
        if k in ("username",):
            continue
        if (not out.get(k)) and shared.get(k):
            out[k] = shared[k]
    return out


def _resolve_live_state_path(profile_path: Path, state: Dict[str, Any]) -> Path:
    """Path passed to child --state: shared acct file when username known."""
    username = str(state.get("username") or state.get("phone") or "").strip()
    if not username:
        return profile_path
    shared = _sync_shared_account(state)
    return shared if shared is not None else profile_path


def _card_user_service_id(state: Dict[str, Any]) -> str:
    usid = (
        state.get("userServiceId")
        or state.get("selectedUserServiceId")
        or state.get("user_service_id")
        or ""
    )
    return str(usid) if usid else ""


def _read_state(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _public_profile(profile_id: str, state: Dict[str, Any], path: Path) -> Dict[str, Any]:
    st = ORCH.get_status(profile_id) if hasattr(ORCH, "get_status") else {"status": "idle"}
    job_status = st.get("status") or "idle"
    # Official protocol slot (from spu / last list) ≠ user-selected keepalive protocol.
    spu = state.get("spuCode") or state.get("lastSpuCode") or ""
    spu = str(spu) if spu is not None else ""
    official = state.get("lastOfficialProtocol") or state.get("protocolHint") or ""
    official = str(official).upper() if official else ""
    if not official and spu:
        official = _spu_protocol_hint(spu)
    return {
        "id": profile_id,
        "displayName": state.get("displayName") or profile_id,
        "usernameMasked": _mask_username(state.get("username")),
        "desktopLabel": state.get("desktopLabel") or state.get("desktopName") or "",
        "userServiceId": state.get("userServiceId") or "",
        "spuCode": spu,
        "protocolHint": official,
        "lastOfficialProtocol": official,
        "hasPassword": bool(state.get("password")),
        "tokenPresent": bool(state.get("sohoToken") or state.get("token")),
        "isSubAccount": bool(state.get("isSubAccount")),
        "loginMode": state.get("loginMode") or ("sub" if state.get("isSubAccount") else "main"),
        "clientProfile": _normalize_client_profile(state.get("clientProfile"), default="linux"),
        "draft": bool(state.get("draft")),
        "jobStatus": job_status,
        "jobId": st.get("jobId"),
        "statePath": str(path),
        "updatedAt": state.get("updatedAt") or (
            datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).astimezone().isoformat(timespec="seconds")
            if path.is_file()
            else _now_iso()
        ),
    }


def list_profiles(include_draft: bool = False) -> List[Dict[str, Any]]:
    """List profiles. Login-only draft profiles are hidden until save-and-keepalive.

    HARD_GATE#868: skip shared acct_*.json (token store only, not UI cards).
    """
    out: List[Dict[str, Any]] = []
    for p in sorted(profiles_dir().glob("*.json")):
        if _is_shared_account_file(p):
            continue
        pid = p.stem
        st = _read_state(p)
        if not include_draft and bool(st.get("draft")):
            continue
        # surface tokenPresent from shared account when card file lacks token
        st = _hydrate_profile_from_shared(st)
        out.append(_public_profile(pid, st, p))
    return out



def _commit_profile_draft(path: Path, state: Dict[str, Any]) -> Dict[str, Any]:
    """Clear draft flag so profile appears in timeline (save-and-keepalive)."""
    if state.get("draft"):
        state = dict(state)
        state.pop("draft", None)
        state["updatedAt"] = _now_iso()
        _write_state(path, state)
    return state


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def health(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "status": "up",
            "service": "cmcc-cloud-alive-webui",
            "at": _now_iso(),
            "orchestrator": type(ORCH).__name__,
        }
    )


async def system_info(request: Request) -> JSONResponse:
    expected = _read_access_token()
    has_file = False
    try:
        has_file = _access_token_path().is_file() and bool(
            _access_token_path().read_text(encoding="utf-8", errors="replace").strip()
        )
    except OSError:
        has_file = False
    has_env = bool((os.environ.get("CMCC_WEBUI_TOKEN") or "").strip())
    return JSONResponse(
        {
            "ok": True,
            "service": "cmcc-cloud-alive",
            "dataDir": str(_data_dir()),
            "profilesDir": str(profiles_dir()),
            "cliCallable": True,  # package present; not probing LIVE
            # Footer: "服务 cmcc-cloud-alive · v{version}" — align with WebUI baseline id.
            "version": "0.1.0-webui-871d-access-gate15",
            "tokenRequired": bool(expected),
            # gate6: empty token = open access; setup is optional (not forced)
            "setupRequired": False,
            "authEnabled": bool(expected),
            "tokenSource": ("file" if has_file else ("env" if has_env else "none")),
            "orchestrator": type(ORCH).__name__,
        }
    )


async def auth_status(request: Request) -> JSONResponse:
    """Public: whether setup/login is needed (no secret leaked)."""
    expected = _read_access_token()
    provided = _extract_request_token(request)
    authed = (not expected) or _token_ok(provided, expected)
    return JSONResponse(
        {
            "ok": True,
            # gate6: no forced first-run; empty token = auth off
            "setupRequired": False,
            "tokenRequired": bool(expected),
            "authEnabled": bool(expected),
            "authenticated": authed,
            "version": "0.1.0-webui-871d-access-gate15",
        }
    )


async def auth_setup(request: Request) -> JSONResponse:
    """First-run: create durable access token when none configured yet."""
    if _read_access_token():
        return api_error(
            "ALREADY_CONFIGURED",
            "访问密钥已存在，请使用登录或「设置令牌」修改",
            409,
            next_step="在登录页输入现有密钥；修改请点顶栏「设置令牌」",
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    generate = bool(body.get("generate"))
    token = str(body.get("token") or body.get("accessToken") or "").strip()
    if generate or not token:
        token = secrets.token_urlsafe(18)
    try:
        path = _write_access_token(token)
    except ValueError as e:
        return api_error("VALIDATION", str(e), 400, next_step="请提供 4–256 位无空格密钥，或使用 generate")
    except OSError as e:
        return api_error("IO_ERROR", f"写入密钥失败: {e}", 500, next_step="检查数据目录写权限")
    return JSONResponse(
        {
            "ok": True,
            "setup": True,
            "token": token,
            "path": str(path),
            "message": "访问密钥已写入数据目录，请妥善保存；后续登录需此密钥",
        }
    )


async def auth_login(request: Request) -> JSONResponse:
    """Validate access token (does not create sessions server-side; FE stores Bearer)."""
    expected = _read_access_token()
    if not expected:
        # gate6: auth disabled — treat as success so FE can enter console
        return JSONResponse(
            {
                "ok": True,
                "authenticated": True,
                "authEnabled": False,
                "message": "未启用访问密钥，已直接进入控制台",
            }
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    token = str(body.get("token") or body.get("accessToken") or "").strip()
    if not token:
        token = _extract_request_token(request)
    if not _token_ok(token, expected):
        return api_error(
            "TOKEN_INVALID",
            "访问密钥错误",
            401,
            next_step="请检查密钥是否与服务器一致（数据目录 webui_access_token 或 CMCC_WEBUI_TOKEN）",
        )
    return JSONResponse({"ok": True, "authenticated": True, "token": token})


async def auth_change(request: Request) -> JSONResponse:
    """Change access token (requires current valid Bearer; writes file).

    gate6: when no token configured yet, allow first enable without currentToken.
    """
    expected = _read_access_token()
    current = _extract_request_token(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    # allow body.currentToken as alternative to Authorization
    body_current = str(body.get("currentToken") or body.get("oldToken") or "").strip()
    if body_current:
        current = body_current
    if expected and not _token_ok(current, expected):
        return api_error(
            "TOKEN_INVALID",
            "当前访问密钥错误，无法修改",
            401,
            next_step="请输入正确的当前密钥后再改密",
        )
    generate = bool(body.get("generate"))
    new_token = str(body.get("token") or body.get("newToken") or body.get("accessToken") or "").strip()
    if generate or not new_token:
        new_token = secrets.token_urlsafe(18)
    try:
        path = _write_access_token(new_token)
    except ValueError as e:
        return api_error("VALIDATION", str(e), 400, next_step="新密钥需 4–256 位且无空格")
    except OSError as e:
        return api_error("IO_ERROR", f"写入密钥失败: {e}", 500, next_step="检查数据目录写权限")
    return JSONResponse(
        {
            "ok": True,
            "changed": True,
            "authEnabled": True,
            "token": new_token,
            "path": str(path),
            "message": "访问密钥已更新（写入数据目录，优先于环境变量）",
        }
    )


async def auth_disable(request: Request) -> JSONResponse:
    """Disable access-token gate by deleting file token (env CMCC_WEBUI_TOKEN still wins)."""
    expected = _read_access_token()
    has_env = bool((os.environ.get("CMCC_WEBUI_TOKEN") or "").strip())
    if has_env and not _access_token_path().is_file():
        return api_error(
            "ENV_TOKEN",
            "当前密钥来自环境变量 CMCC_WEBUI_TOKEN，无法通过本接口关闭",
            400,
            next_step="请取消环境变量或改用文件密钥后再关闭鉴权",
        )
    if expected:
        current = _extract_request_token(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        body_current = str(body.get("currentToken") or body.get("oldToken") or body.get("token") or "").strip()
        if body_current:
            current = body_current
        if not _token_ok(current, expected):
            return api_error(
                "TOKEN_INVALID",
                "当前访问密钥错误，无法关闭鉴权",
                401,
                next_step="请输入正确的当前密钥后再关闭",
            )
    try:
        path = _clear_access_token()
    except OSError as e:
        return api_error("IO_ERROR", str(e), 500, next_step="检查数据目录写权限")
    # If env still set, report residual auth
    still = _read_access_token()
    return JSONResponse(
        {
            "ok": True,
            "disabled": not bool(still),
            "authEnabled": bool(still),
            "path": str(path),
            "message": (
                "已关闭访问鉴权（删除文件密钥）"
                if not still
                else "已删除文件密钥，但仍受环境变量 CMCC_WEBUI_TOKEN 约束"
            ),
        }
    )


async def profiles_list(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "profiles": list_profiles()})


async def profiles_create(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return api_error("VALIDATION", "JSON body required")
    if not isinstance(body, dict):
        return api_error("VALIDATION", "JSON object required")
    display = (body.get("displayName") or body.get("name") or "").strip()
    username = (body.get("username") or "").strip()
    password = body.get("password")  # write-only
    client_profile = _normalize_client_profile(body.get("clientProfile"), default="linux")
    if str(body.get("clientProfile") or "").strip() and client_profile not in ("linux", "windows", "mac"):
        return api_error("VALIDATION", "clientProfile must be linux|windows|mac")
    if client_profile not in ("linux", "windows", "mac"):
        client_profile = "linux"
    base = _safe_profile_id(display or username or f"p-{uuid.uuid4().hex[:8]}")
    pid = base
    n = 2
    while _profile_path(pid).exists():
        pid = f"{base}-{n}"
        n += 1
    state: Dict[str, Any] = {
        "displayName": display or pid,
        "username": username,
        "clientProfile": client_profile,
        "createdAt": _now_iso(),
        "updatedAt": _now_iso(),
    }
    # HARD_GATE#850: login-only create stays draft; hidden from timeline until save.
    if body.get("draft") is True or str(body.get("draft") or "").lower() in ("1", "true", "yes"):
        state["draft"] = True
    if password:
        state["password"] = str(password)
        state["passwordSavedAt"] = _now_iso()
    path = _profile_path(pid)
    _write_state(path, state)
    public = _public_profile(pid, state, path)
    return JSONResponse({"ok": True, "profile": public}, status_code=201)


async def profiles_get(request: Request) -> JSONResponse:
    pid = request.path_params["profile_id"]
    path = _profile_path(pid)
    if not path.is_file():
        return api_error("NOT_FOUND", f"profile {pid} not found", 404)
    state = _read_state(path)
    return JSONResponse({"ok": True, "profile": _public_profile(pid, state, path)})


async def profiles_delete(request: Request) -> JSONResponse:
    """Delete a cloud-desktop account profile JSON.

    OPS#185 / OPEN#188: if keepalive is running, stop it first, then unlink
    the profile file. Idempotent-ish: missing profile → 404 (not 405).
    """
    pid = request.path_params["profile_id"]
    # Block path traversal; do not re-normalize id (Master probe __no_such__ → 404).
    if not pid or any(x in pid for x in ("/", "\\", "..")):
        return api_error("VALIDATION", "invalid profile id", 400)
    path = _profile_path(pid)
    try:
        path.resolve().relative_to(profiles_dir().resolve())
    except Exception:
        return api_error("VALIDATION", "invalid profile id", 400)
    if not path.is_file():
        return api_error("NOT_FOUND", f"profile {pid} not found", 404)

    stopped = False
    stop_detail = None
    try:
        st = ORCH.get_status(pid) if hasattr(ORCH, "get_status") else {}
        status = (st or {}).get("status") or "idle"
        if status == "running":
            try:
                job = ORCH.stop_job(pid)
                stopped = True
                stop_detail = (job or {}).get("status") or "stopped"
            except KeyError:
                # No active job mapping; continue to delete file.
                stop_detail = "no_job"
            except Exception as e:
                return api_error("STOP_FAILED", f"stop before delete failed: {e}", 500)
    except Exception as e:
        return api_error("STOP_FAILED", f"status before delete failed: {e}", 500)

    try:
        path.unlink()
    except FileNotFoundError:
        return api_error("NOT_FOUND", f"profile {pid} not found", 404)
    except OSError as e:
        return api_error("IO_ERROR", f"delete failed: {e}", 500)

    # Best-effort: remove leftover tmp if any
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        if tmp.is_file():
            tmp.unlink()
    except OSError:
        pass

    return JSONResponse(
        {
            "ok": True,
            "deleted": True,
            "profileId": pid,
            "stoppedJob": stopped,
            "stopDetail": stop_detail,
        }
    )


def _password_login_for_profile(
    path: Path, username: str, password: str, mode: str = "main"
) -> Dict[str, Any]:
    """Thin wrapper: main/sub password login writes sohoToken into profile state JSON."""
    from cmcc_cloud_alive.auth import password_login, sub_password_login

    login_fn = (
        sub_password_login
        if str(mode).lower() in ("sub", "subaccount", "1", "true")
        else password_login
    )
    return login_fn(
        username,
        password,
        state_path=str(path),
        save_password=True,
    )



async def profiles_patch(request: Request) -> JSONResponse:
    """Partial update for card UI meta (clientProfile / displayName / protocol draft).

    Does not touch live session tokens except via explicit body keys already
    handled by login. Used by FE when user toggles 客户端 segment so the choice
    survives refresh without requiring a full re-login.
    """
    pid = request.path_params["profile_id"]
    path = _profile_path(pid)
    if not path.is_file():
        return api_error("NOT_FOUND", f"profile {pid} not found", 404)
    try:
        body = await request.json()
    except Exception:
        return api_error("VALIDATION", "JSON body required")
    if not isinstance(body, dict):
        return api_error("VALIDATION", "JSON object required")
    state = _read_state(path)
    changed = False
    if "clientProfile" in body:
        raw = body.get("clientProfile")
        if raw is None or str(raw).strip() == "":
            return api_error("VALIDATION", "clientProfile must be linux|windows|mac")
        new_v = _normalize_client_profile(raw, default="")
        if new_v not in ("linux", "windows", "mac"):
            return api_error("VALIDATION", "clientProfile must be linux|windows|mac")
        if state.get("clientProfile") != new_v:
            state["clientProfile"] = new_v
            changed = True
        else:
            state["clientProfile"] = new_v  # canonicalize
            changed = True
    if "displayName" in body and body.get("displayName") is not None:
        dn = str(body.get("displayName") or "").strip()
        if dn and state.get("displayName") != dn:
            state["displayName"] = dn
            changed = True
    if "protocol" in body and body.get("protocol") is not None:
        # store user choice only; resolve_user_protocol remains source at start
        proto = str(body.get("protocol") or "").strip().upper()
        if proto:
            if proto in ("ZX", "ZHONGXING"):
                proto = "ZTE"
            if proto == "SANGFOR":
                proto = "SCG"
            if proto in ("ZTE", "SCG") and state.get("protocol") != proto:
                state["protocol"] = proto
                changed = True
    if not changed and "clientProfile" not in body:
        return api_error("VALIDATION", "no supported fields to patch", 400)
    state["updatedAt"] = _now_iso()
    _write_state(path, state)
    try:
        _sync_shared_account(state)
    except Exception:
        pass
    # re-read + hydrate for response consistency with GET
    state2 = _read_state(path)
    try:
        state2 = _hydrate_profile_from_shared(state2)
    except Exception:
        pass
    # card-level clientProfile must win over shared hydrate defaults
    if state.get("clientProfile"):
        state2["clientProfile"] = state["clientProfile"]
    pub = _public_profile(pid, state2, path)
    return JSONResponse({"ok": True, "profile": pub})


async def profiles_login(request: Request) -> JSONResponse:
    """Save credentials and attempt LIVE cloud login (sohoToken).

    Default path calls ``auth.password_login`` → ``core.password_login`` on a
    worker thread and persists ``sohoToken`` into the profile state file.
    Offline smoke may set ``CMCC_WEBUI_LOGIN_STUB=1`` to store credentials only
    (never invents a session token). Callers must treat
    ``sessionEstablished=false`` as not logged in for desktops.
    """
    pid = request.path_params["profile_id"]
    path = _profile_path(pid)
    if not path.is_file():
        return api_error("NOT_FOUND", f"profile {pid} not found", 404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    state = _read_state(path)
    username = (body.get("username") or state.get("username") or "").strip()
    password = body.get("password")
    if password is not None and str(password) == "":
        return api_error(
            "VALIDATION",
            "password empty",
            400,
            next_step="请填写密码后再保存",
        )
    if body.get("password"):
        state["password"] = str(body["password"])
        state["passwordSavedAt"] = _now_iso()
        password = str(body["password"])
    else:
        password = state.get("password")
    if body.get("username"):
        state["username"] = str(body["username"]).strip()
        username = state["username"]
    # main/sub account login mode (composer dual buttons)
    raw_mode = body.get("mode")
    if raw_mode is None and "isSubAccount" in body:
        raw_mode = "sub" if body.get("isSubAccount") else "main"
    login_mode = (
        "sub"
        if str(raw_mode or "main").lower() in ("sub", "subaccount", "1", "true")
        else "main"
    )
    state["loginMode"] = login_mode
    state["isSubAccount"] = login_mode == "sub"
    # HARD_GATE#871d-client-token: persist clientProfile from UI (card/composer)
    if _apply_client_profile_from_body(state, body):
        state["updatedAt"] = _now_iso()
    if not username and not (state.get("sohoToken") or state.get("token")):
        return api_error(
            "VALIDATION",
            "username required when no session token",
            400,
            next_step="请填写账号，或先写入有效 sohoToken",
        )

    state["lastLoginAttemptAt"] = _now_iso()
    state["updatedAt"] = _now_iso()

    stub_on = os.environ.get("CMCC_WEBUI_LOGIN_STUB", "").strip() in (
        "1",
        "true",
        "TRUE",
        "yes",
        "YES",
    )
    if stub_on:
        token_present = bool(state.get("sohoToken") or state.get("token"))
        state["lastLoginStatus"] = (
            "session-present" if token_present else "credentials-saved-no-session"
        )
        _write_state(path, state)
        try:
            _sync_shared_account(state)
        except Exception:
            pass
        pub = _public_profile(pid, state, path)
        return JSONResponse(
            {
                "ok": True,
                "profile": pub,
                "sessionEstablished": token_present,
                "source": "stub",
                "note": (
                    "session already present; desktops may list_clouds"
                    if token_present
                    else "CMCC_WEBUI_LOGIN_STUB=1: credentials stored only; no sohoToken minted"
                ),
                "nextStep": (
                    "拉取桌面列表（GET /desktops）"
                    if token_present
                    else "离线 stub：未建立 sohoToken；关 stub 后重试 LIVE 登录"
                ),
            }
        )

    if not username or not password:
        token_present = bool(state.get("sohoToken") or state.get("token"))
        if token_present:
            state["lastLoginStatus"] = "session-present"
            _write_state(path, state)
            try:
                _sync_shared_account(state)
            except Exception:
                pass
            pub = _public_profile(pid, state, path)
            return JSONResponse(
                {
                    "ok": True,
                    "profile": pub,
                    "sessionEstablished": True,
                    "source": "existing-session",
                    "note": "session already present; no password supplied for re-login",
                    "nextStep": "拉取桌面列表（GET /desktops）",
                }
            )
        state["lastLoginStatus"] = "credentials-incomplete"
        _write_state(path, state)
        return api_error(
            "VALIDATION",
            "username and password required for LIVE login",
            400,
            next_step="请填写账号和密码后重新登录",
        )

    # Persist credentials before LIVE call so retries / re-login can reuse them.
    state["username"] = username
    state["password"] = str(password)
    state["passwordSavedAt"] = state.get("passwordSavedAt") or _now_iso()
    state["lastLoginStatus"] = "live-attempt"
    _write_state(path, state)

    try:
        await asyncio.to_thread(_password_login_for_profile, path, username, str(password), login_mode)
    except Exception as e:
        msg = str(e) or e.__class__.__name__
        code_name = "UPSTREAM"
        status = 502
        resp = getattr(e, "response", None)
        # Prefer upstream response codes. Do NOT match bare "login"/"password":
        # core.assert_ok labels look like "passwordLogin failed: code=... msg=..."
        # and would falsely map every upstream failure to AUTH_FAILED/401.
        auth_codes = {4001, 4003, 4010, 4011, 4100, 401, 403}
        rc_int = None
        upstream_msg = ""
        if isinstance(resp, dict):
            rc = resp.get("code")
            try:
                rc_int = int(rc) if rc is not None else None
            except (TypeError, ValueError):
                rc_int = None
            upstream_msg = str(resp.get("msg") or "")
        if rc_int in auth_codes:
            code_name = "AUTH_FAILED"
            status = 401
        else:
            # Message-based auth only for explicit credential-wrong phrases.
            # Never match bare "login" or the assert_ok label "passwordLogin".
            hay = f"{upstream_msg} {msg}".lower()
            auth_needles = (
                "wrong password",
                "invalid password",
                "password error",
                "password incorrect",
                "bad credentials",
                "invalid credentials",
                "credential",
                "authentication failed",
                "auth failed",
                "unauthorized",
                "账号或密码",
                "用户名或密码",
                "密码错误",
                "密码不正确",
            )
            if any(n in hay for n in auth_needles):
                code_name = "AUTH_FAILED"
                status = 401
        # Re-read; core may have partially written. Never invent sohoToken.
        state = _read_state(path)
        state["lastLoginAttemptAt"] = _now_iso()
        state["lastLoginStatus"] = f"failed:{code_name}"
        state["lastLoginError"] = msg[:500]
        state["updatedAt"] = _now_iso()
        _write_state(path, state)
        zh_next = (
            "账号或密码错误：请核对后重试 POST /login"
            if code_name == "AUTH_FAILED"
            else "上游登录失败：检查网络/账号后重试 POST /login"
        )
        return api_error(
            code_name,
            f"password_login failed: {msg}",
            status,
            next_step=zh_next,
        )

    state = _read_state(path)
    token_present = bool(state.get("sohoToken") or state.get("token"))
    state["lastLoginAttemptAt"] = _now_iso()
    state["lastLoginStatus"] = "session-established" if token_present else "live-ok-no-token"
    state["lastLoginError"] = ""
    state["updatedAt"] = _now_iso()
    _write_state(path, state)
    # HARD_GATE#868: same account shares one token store (acct_<user>.json)
    try:
        _sync_shared_account(state)
    except Exception:
        pass
    pub = _public_profile(pid, state, path)
    return JSONResponse(
        {
            "ok": True,
            "profile": pub,
            "sessionEstablished": token_present,
            "source": "password_login",
            "note": (
                "LIVE login ok; sohoToken written — GET /desktops may list_clouds"
                if token_present
                else "LIVE login returned without sohoToken; desktops still gated"
            ),
            "nextStep": (
                "拉取桌面列表（GET /desktops）"
                if token_present
                else "登录响应无 sohoToken：检查上游账号状态后重试"
            ),
        }
    )


def _spu_protocol_hint(spu_code: str) -> str:
    """Map spuCode → likely client protocol (UI hint only; user may override)."""
    s = (spu_code or "").strip().lower()
    if not s:
        return ""
    if s == "sc-cloud-pc" or s.startswith("sc-"):
        return "SCG"
    if s == "zte-cloud-pc" or s.startswith("zte-"):
        return "ZTE"
    return ""


def _desktop_from_cloud(item: Any) -> Optional[Dict[str, Any]]:
    """Normalize one /cc/cloudPc/list item → WebUI desktop DTO (J8 spuCode)."""
    if not isinstance(item, dict):
        return None
    usid = item.get("userServiceId") or item.get("user_service_id") or ""
    usid = str(usid).strip() if usid is not None else ""
    if not usid:
        return None
    spu_raw = item.get("spuCode") if item.get("spuCode") is not None else item.get("spu_code")
    spu = str(spu_raw or "")
    vm_name = item.get("vmName") or item.get("desktopName") or item.get("name") or ""
    sku = item.get("skuName") or ""
    vm_status_show = item.get("vmStatusShow") or item.get("statusShow") or ""
    # HARD_GATE#850: name = skuName (python CLI: 家庭云电脑高级版), fallback vmName
    sku_s = str(sku) if sku is not None else ""
    vm_s = str(vm_name) if vm_name is not None else ""
    desk_label = sku_s or vm_s or usid
    dto: Dict[str, Any] = {
        "userServiceId": usid,
        "vmName": vm_s,
        "spuCode": spu,
        "skuName": sku_s,
        "desktopLabel": desk_label,
        "name": desk_label,
        "label": desk_label,
        "vmStatus": item.get("vmStatus"),
        "vmStatusShow": str(vm_status_show) if vm_status_show is not None else "",
        "statusName": str(vm_status_show) if vm_status_show is not None else "",
    }
    hint = _spu_protocol_hint(spu)
    if hint:
        dto["protocolHint"] = hint
    return dto


def _normalize_desktops(cloud_list: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(cloud_list, list):
        return out
    for raw in cloud_list:
        dto = _desktop_from_cloud(raw)
        if dto is not None:
            out.append(dto)
    return out


def _desktops_shape_fixture() -> List[Dict[str, Any]]:
    """Offline shape-only rows (env CMCC_WEBUI_DESKTOPS_FIXTURE=1). Not LIVE."""
    return [
        {
            "userServiceId": "fixture-sc-001",
            "vmName": "fixture-sc",
            "spuCode": "sc-cloud-pc",
            "skuName": "fixture",
            "desktopLabel": "fixture",
            "name": "fixture",
            "vmStatus": 1,
            "vmStatusShow": "运行中",
            "statusName": "运行中",
            "protocolHint": "SCG",
        },
        {
            "userServiceId": "fixture-zte-001",
            "vmName": "fixture-zte",
            "spuCode": "zte-cloud-pc",
            "skuName": "fixture",
            "vmStatus": 1,
            "vmStatusShow": "运行中",
            "protocolHint": "ZTE",
        },
    ]


def _list_clouds_for_profile(path: Path) -> List[Any]:
    """Thin wrapper: core.list_clouds with profile JSON as state file (single short call)."""
    from types import SimpleNamespace

    from cmcc_cloud_alive.core import list_clouds

    return list_clouds(SimpleNamespace(state=str(path)))


async def profiles_desktops(request: Request) -> JSONResponse:
    """List cloud desktops for a profile (J8_BE_DESKTOPS_SPU).

    Prefer cached ``cloudList`` in the profile state JSON. Otherwise call
    ``core.list_clouds`` (``/cc/cloudPc/list/v6``) once when ``sohoToken`` is
    present. Unauthenticated profiles get a structured error — never a silent
    stub empty success. Optional ``?refresh=1`` forces re-list. Fixture shape
    rows only when env ``CMCC_WEBUI_DESKTOPS_FIXTURE=1`` (offline smoke).
    """
    pid = request.path_params["profile_id"]
    path = _profile_path(pid)
    if not path.is_file():
        return api_error("NOT_FOUND", f"profile {pid} not found", 404)

    refresh = (request.query_params.get("refresh") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    state = _read_state(path)
    token = (state.get("sohoToken") or state.get("token") or "").strip()
    cached = state.get("cloudList")
    has_cache = isinstance(cached, list) and bool(state.get("lastCloudListAt") or cached)

    source = "cache"
    raw_items: List[Any] = []

    if has_cache and not refresh:
        raw_items = list(cached or [])
        source = "cache"
    elif token:
        try:
            raw_items = await asyncio.to_thread(_list_clouds_for_profile, path)
            source = "list_clouds"
            # re-read after merge_state wrote cloudList into the same profile file
            state = _read_state(path)
        except Exception as e:
            # Prefer CmccError details without requiring core at import time
            msg = str(e) or e.__class__.__name__
            code_name = "UPSTREAM"
            status = 502
            resp = getattr(e, "response", None)
            if isinstance(resp, dict):
                rc = resp.get("code")
                # common auth-ish codes from CMCC gateways
                if rc in (4001, 4003, 4010, 4011, 4100, 401, 403) or "token" in msg.lower():
                    code_name = "AUTH_EXPIRED"
                    status = 401
            zh_next = (
                "会话可能已失效：请重新登录写入 sohoToken，再 GET /desktops?refresh=1"
                if code_name == "AUTH_EXPIRED"
                else "上游列桌面失败：检查网络/账号后重试 GET /desktops?refresh=1"
            )
            return api_error(
                code_name,
                f"list_clouds failed: {msg}",
                status,
                next_step=zh_next,
            )
    else:
        fixture_on = os.environ.get("CMCC_WEBUI_DESKTOPS_FIXTURE", "").strip() in (
            "1",
            "true",
            "TRUE",
            "yes",
            "YES",
        )
        if fixture_on:
            desktops = _desktops_shape_fixture()
            return JSONResponse(
                {
                    "ok": True,
                    "profileId": pid,
                    "desktops": desktops,
                    "source": "fixture",
                    "count": len(desktops),
                    "note": "shape fixture only (CMCC_WEBUI_DESKTOPS_FIXTURE); wire path is core.list_clouds",
                }
            )
        return api_error(
            "AUTH_REQUIRED",
            "未登录：当前账号没有有效会话（sohoToken），无法拉取桌面列表",
            401,
            next_step="请先登录建立会话（写入 sohoToken），再重试拉取桌面",
        )

    desktops = _normalize_desktops(raw_items)
    return JSONResponse(
        {
            "ok": True,
            "profileId": pid,
            "desktops": desktops,
            "source": source,
            "count": len(desktops),
            "lastCloudListAt": state.get("lastCloudListAt") or "",
        }
    )


async def profiles_select_desktop(request: Request) -> JSONResponse:
    """Bind selected desktop + official protocol slot (spu / protocolHint).

    ``lastOfficialProtocol`` is the **official** protocol derived from spuCode
    (SCG/ZTE hint). It is independent of the user-chosen keepalive protocol on
    start-job.
    """
    pid = request.path_params["profile_id"]
    path = _profile_path(pid)
    if not path.is_file():
        return api_error("NOT_FOUND", f"profile {pid} not found", 404)
    try:
        body = await request.json()
    except Exception:
        return api_error("VALIDATION", "JSON body required")
    if not isinstance(body, dict):
        body = {}
    usid = body.get("userServiceId") or ""
    label = body.get("desktopLabel") or body.get("desktopName") or body.get("vmName") or ""
    spu = body.get("spuCode") or body.get("spu") or ""
    # Allow explicit official protocol, else derive from spu / protocolHint body.
    official_in = body.get("lastOfficialProtocol") or body.get("protocolHint") or ""
    state = _read_state(path)
    if usid:
        state["userServiceId"] = str(usid)
    if label:
        state["desktopLabel"] = str(label)
    if spu:
        spu_s = str(spu).strip()
        state["spuCode"] = spu_s
        state["lastSpuCode"] = spu_s
    official = str(official_in).strip().upper() if official_in else ""
    if not official and state.get("spuCode"):
        official = _spu_protocol_hint(str(state.get("spuCode") or ""))
    if official:
        state["lastOfficialProtocol"] = official
        state["protocolHint"] = official
    # HARD_GATE#851: keep draft; only save-and-start commits to timeline
    state["updatedAt"] = _now_iso()
    _write_state(path, state)
    return JSONResponse({"ok": True, "profile": _public_profile(pid, state, path)})



def resolve_user_protocol(body_protocol=None, state=None, fallback="ZTE"):
    """HARD_GATE#871c: body → profile fields → historical empty fallback. Never force SCG."""
    candidates = []
    if body_protocol:
        candidates.append(body_protocol)
    st = state or {}
    for k in ("protocol", "lastOfficialProtocol", "protocolHint", "last_protocol"):
        if st.get(k):
            candidates.append(st.get(k))
    for v in candidates:
        u = str(v or "").strip().upper()
        if u in ("ZX", "ZHONGXING"):
            u = "ZTE"
        if u == "SANGFOR":
            u = "SCG"
        if u in ("ZTE", "SCG"):
            return u
    return str(fallback or "ZTE").upper()


async def profiles_start_job(request: Request) -> JSONResponse:
    pid = request.path_params["profile_id"]
    path = _profile_path(pid)
    if not path.is_file():
        return api_error("NOT_FOUND", f"profile {pid} not found", 404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    mode = body.get("mode") or "live"
    try:
        timing = parse_job_timing_fields(body)
    except ValueError as e:
        return api_error("VALIDATION", str(e))
    # HARD_GATE#850: save-and-keepalive commits draft into timeline
    state = _read_state(path)
    # HARD_GATE#871c: user protocol choice — body → profile → ZTE empty-only
    protocol = resolve_user_protocol(body.get("protocol"), state)
    # HARD_GATE#871d-client-token: persist clientProfile before spawn (card + shared)
    changed = False
    if _apply_client_profile_from_body(state, body):
        changed = True
    if state.get("draft"):
        state.pop("draft", None)
        changed = True
    if not state.get("clientProfile"):
        state["clientProfile"] = _normalize_client_profile(
            body.get("clientProfile") if isinstance(body, dict) else None,
            default="linux",
        )
        changed = True
    if changed:
        state["updatedAt"] = _now_iso()
        _write_state(path, state)
        try:
            _sync_shared_account(state)
        except Exception:
            pass
    # HARD_GATE#868: card keeps UI meta; live child uses shared acct_*.json token
    # and --user-service-id from THIS card (not from shared, avoids dual-card race).
    usid = (
        state.get("userServiceId")
        or state.get("selectedUserServiceId")
        or state.get("user_service_id")
        or ""
    )
    live_path = _resolve_live_state_path(path, state)
    # ensure shared has latest credentials/token before spawn
    try:
        _sync_shared_account(state)
        live_path = _resolve_live_state_path(path, state)
    except Exception:
        pass
    try:
        job = await asyncio.to_thread(ORCH.start_job,
            pid,
            live_path,
            protocol=protocol,
            mode=mode,
            extra_args=timing["extraArgs"],
            interval_sec=timing["intervalSec"],
            traffic_sec=timing["trafficSec"],
            duration_sec=timing["durationSec"],
            user_service_id=str(usid) if usid else None,
        )
    except TypeError:
        # older orchestrator signature: pass extra_args only, merge fields on response
        try:
            job = await asyncio.to_thread(ORCH.start_job,
                pid, live_path, protocol=protocol, mode=mode, extra_args=timing["extraArgs"],
                user_service_id=str(usid) if usid else None,
            )
            job = dict(job)
            job["intervalSec"] = timing["intervalSec"]
            job["trafficSec"] = timing["trafficSec"]
            job["durationSec"] = timing["durationSec"]
            job["extraArgs"] = list(timing["extraArgs"])
        except RuntimeError as e:
            if str(e) == "PROFILE_IN_USE":
                return api_error("PROFILE_IN_USE", "profile already has a running job", 409)
            if str(e) == "USID_IN_USE":
                return api_error(
                    "USID_IN_USE",
                    "desktop userServiceId already has a running job on another card",
                    409,
                )
            return api_error("VALIDATION", str(e))
        except ValueError as e:
            return api_error("VALIDATION", str(e))
        return JSONResponse({"ok": True, "job": job}, status_code=202)
    except RuntimeError as e:
        if str(e) == "PROFILE_IN_USE":
            return api_error("PROFILE_IN_USE", "profile already has a running job", 409)
        if str(e) == "USID_IN_USE":
            return api_error(
                "USID_IN_USE",
                "desktop userServiceId already has a running job on another card",
                409,
            )
        return api_error("VALIDATION", str(e))
    except ValueError as e:
        return api_error("VALIDATION", str(e))
    return JSONResponse({"ok": True, "job": job}, status_code=202)


async def profiles_stop_job(request: Request) -> JSONResponse:
    pid = request.path_params["profile_id"]
    path = _profile_path(pid)
    if not path.is_file():
        return api_error("NOT_FOUND", f"profile {pid} not found", 404)
    try:
        job = ORCH.stop_job(pid)
    except KeyError:
        return api_error("NOT_FOUND", "no job for profile", 404)
    return JSONResponse({"ok": True, "job": job})


def _desktop_logout_for_profile(live_path: Path, user_service_id: str) -> Dict[str, Any]:
    """Call CLI desktop_logout → /cc/cloudPc/logout/v2 on worker thread."""
    from cmcc_cloud_alive import logout as logout_mod

    return logout_mod.desktop_logout(
        user_service_id=user_service_id or None,
        state_path=str(live_path),
    )


async def profiles_desktop_logout(request: Request) -> JSONResponse:
    """Desktop session logout via /cc/cloudPc/logout/v2 (same as CLI logout --desktop).

    Uses this card's userServiceId and shared acct_*.json token path so multi-card
    same-account keeps one live session file (HARD_GATE#868).
    """
    pid = request.path_params["profile_id"]
    path = _profile_path(pid)
    if not path.is_file():
        return api_error("NOT_FOUND", f"profile {pid} not found", 404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    state = _hydrate_profile_from_shared(_read_state(path))
    usid = (
        body.get("userServiceId")
        or body.get("user_service_id")
        or state.get("userServiceId")
        or state.get("selectedUserServiceId")
        or state.get("user_service_id")
        or ""
    )
    usid = str(usid).strip() if usid is not None else ""
    if not usid:
        return api_error(
            "VALIDATION",
            "userServiceId required for desktop logout",
            400,
            next_step="请先选择云桌面，或在配置中填写 userServiceId",
        )
    token = state.get("sohoToken") or state.get("token") or ""
    if not token:
        return api_error(
            "AUTH_REQUIRED",
            "未登录：当前账号没有有效会话（sohoToken），无法桌面登出",
            401,
            next_step="请先登录建立会话，再执行桌面登出",
        )

    # Read-only live path: do NOT _sync_shared_account here.
    # Sync would write this card's userServiceId into the shared acct_*.json and
    # clobber a sibling card of the same account (multi-card same-username).
    live_path = _resolve_live_state_path(path, state)

    try:
        response = await asyncio.to_thread(_desktop_logout_for_profile, live_path, usid)
    except Exception as e:
        msg = str(e) or e.__class__.__name__
        code_name = "UPSTREAM_ERROR"
        status = 502
        resp = getattr(e, "response", None)
        rc = None
        if isinstance(resp, dict):
            rc = resp.get("code")
        # Token / session expired codes from CMCC SOHO (incl. 4015)
        auth_codes = (4001, 4003, 4010, 4011, 4015, 4100, 401, 403)
        low = msg.lower()
        if (
            (rc in auth_codes)
            or "token" in low
            or "4015" in msg
            or "未登录" in msg
            or "登录" in msg and ("失效" in msg or "过期" in msg)
        ):
            code_name = "AUTH_EXPIRED"
            status = 401
        elif "not found" in low or "userServiceId not found" in msg:
            code_name = "NOT_FOUND"
            status = 404
        return api_error(
            code_name,
            f"desktop_logout failed: {msg}",
            status,
            next_step=(
                "会话可能已失效：请重新登录后再试桌面登出"
                if code_name == "AUTH_EXPIRED"
                else (
                    "云桌面 usid 无效或不属于当前账号：请重新「获取云桌面」后再登出"
                    if code_name == "NOT_FOUND"
                    else "上游桌面登出失败：检查网络/账号后重试"
                )
            ),
        )

    # api_request returns body even when SOHO code != 2000; map those to API errors
    # so the UI does not toast "成功" for 4015/stale usid (was the 502 / fake-ok path).
    if isinstance(response, dict):
        up_code = response.get("code")
        up_msg = (
            response.get("errMsg")
            or response.get("msg")
            or response.get("message")
            or ""
        )
        up_msg = str(up_msg)
        try:
            up_code_i = int(up_code) if up_code is not None else None
        except (TypeError, ValueError):
            up_code_i = None
        ok_upstream = (up_code_i == 2000) or (str(up_msg).upper() == "SUCCESS")
        if not ok_upstream:
            auth_codes = (4001, 4003, 4010, 4011, 4015, 4100, 401, 403)
            low = up_msg.lower()
            if (
                (up_code_i in auth_codes)
                or "token" in low
                or "4015" in str(up_code)
                or "未登录" in up_msg
                or ("登录" in up_msg and ("失效" in up_msg or "过期" in up_msg))
            ):
                return api_error(
                    "AUTH_EXPIRED",
                    f"desktop_logout failed: {up_msg or up_code}",
                    401,
                    next_step="会话可能已失效：请重新登录后再试桌面登出",
                )
            if (
                "not found" in low
                or "userServiceId not found" in up_msg
                or up_code_i in (404, 4004, 5000)
            ):
                return api_error(
                    "NOT_FOUND" if up_code_i != 5000 else "UPSTREAM_ERROR",
                    f"desktop_logout failed: {up_msg or up_code}",
                    404 if up_code_i != 5000 else 502,
                    next_step=(
                        "云桌面 usid 无效或不属于当前账号：请重新「获取云桌面」后再登出"
                        if up_code_i != 5000
                        else "上游桌面登出失败：检查 usid/网络后重试"
                    ),
                )
            return api_error(
                "UPSTREAM_ERROR",
                f"desktop_logout failed: {up_msg or up_code}",
                502,
                next_step="上游桌面登出失败：检查网络/账号后重试",
            )

    # Mirror lastDesktopLogout* onto card profile for UI visibility (shared already updated).
    try:
        card = _read_state(path)
        card["lastDesktopLogoutAt"] = _now_iso()
        card["lastDesktopLogoutUserServiceId"] = usid
        card["updatedAt"] = _now_iso()
        _write_state(path, card)
    except Exception:
        pass

    return JSONResponse(
        {
            "ok": True,
            "profileId": pid,
            "userServiceId": usid,
            "statePath": str(live_path),
            "response": response,
        }
    )


async def profiles_logs(request: Request) -> JSONResponse:
    pid = request.path_params["profile_id"]
    path = _profile_path(pid)
    if not path.is_file():
        return api_error("NOT_FOUND", f"profile {pid} not found", 404)
    lines = ORCH.recent_logs(profile_id=pid, limit=200)
    # ensure redaction of any accidental secrets in lines
    safe = [{"at": x.get("at"), "line": str(x.get("line", ""))[:2000]} for x in lines]
    return JSONResponse({"ok": True, "profileId": pid, "lines": safe})


async def profiles_logs_clear(request: Request) -> JSONResponse:
    """HARD_GATE#853: clear backend log buffer for a profile/card."""
    pid = request.path_params["profile_id"]
    path = _profile_path(pid)
    if not path.is_file():
        return api_error("NOT_FOUND", f"profile {pid} not found", 404)
    result = ORCH.clear_logs(profile_id=pid)
    return JSONResponse(
        {
            "ok": True,
            "profileId": pid,
            "cleared": int((result or {}).get("cleared") or 0),
            "jobId": (result or {}).get("jobId"),
            "lines": [],
        }
    )


async def profiles_events(request: Request) -> StreamingResponse:
    """SSE stream for a profile (and global job_status/job_log)."""
    pid = request.path_params["profile_id"]
    path = _profile_path(pid)
    if not path.is_file():
        return api_error("NOT_FOUND", f"profile {pid} not found", 404)

    queue = ORCH.subscribe()

    async def gen() -> AsyncIterator[bytes]:
        try:
            # initial snapshot
            st = ORCH.get_status(pid)
            data = json.dumps(
                {
                    "jobId": st.get("jobId"),
                    "profileId": pid,
                    "status": st.get("status"),
                    "at": _now_iso(),
                    "detail": "snapshot",
                },
                ensure_ascii=False,
            )
            yield f"event: job_status\ndata: {data}\n\n".encode("utf-8")
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                ev = item.get("event") or "message"
                payload = item.get("data") or {}
                if payload.get("profileId") and payload.get("profileId") != pid:
                    continue
                line = json.dumps(redact_obj(payload), ensure_ascii=False)
                yield f"event: {ev}\ndata: {line}\n\n".encode("utf-8")
        finally:
            ORCH.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def events_global(request: Request) -> StreamingResponse:
    """Global SSE stream — all job_status/job_log events (FE EventSource /api/events)."""
    queue = ORCH.subscribe()

    async def gen() -> AsyncIterator[bytes]:
        try:
            # initial hello so FE knows the stream is alive
            hello = json.dumps(
                {"status": "connected", "at": _now_iso(), "detail": "global-sse"},
                ensure_ascii=False,
            )
            yield f"event: job_status\ndata: {hello}\n\n".encode("utf-8")
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                ev = item.get("event") or "message"
                payload = item.get("data") or {}
                line = json.dumps(redact_obj(payload), ensure_ascii=False)
                yield f"event: {ev}\ndata: {line}\n\n".encode("utf-8")
        finally:
            ORCH.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def jobs_list(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "jobs": ORCH.list_jobs()})


async def jobs_create(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return api_error("VALIDATION", "JSON body required")
    pid = (body or {}).get("profileId") or (body or {}).get("profile_id")
    if not pid:
        return api_error("VALIDATION", "profileId required")
    path = _profile_path(str(pid))
    if not path.is_file():
        return api_error("NOT_FOUND", f"profile {pid} not found", 404)
    state = _read_state(path)
    protocol = resolve_user_protocol((body or {}).get("protocol"), state)
    mode = body.get("mode") or "live"
    try:
        timing = parse_job_timing_fields(body if isinstance(body, dict) else {})
    except ValueError as e:
        return api_error("VALIDATION", str(e))
    try:
        job = await asyncio.to_thread(ORCH.start_job,
            str(pid),
            path,
            protocol=protocol,
            mode=mode,
            extra_args=timing["extraArgs"],
            interval_sec=timing["intervalSec"],
            traffic_sec=timing["trafficSec"],
            duration_sec=timing["durationSec"],
        )
    except TypeError:
        try:
            job = await asyncio.to_thread(ORCH.start_job,
                str(pid),
                path,
                protocol=protocol,
                mode=mode,
                extra_args=timing["extraArgs"],
            )
            job = dict(job)
            job["intervalSec"] = timing["intervalSec"]
            job["trafficSec"] = timing["trafficSec"]
            job["durationSec"] = timing["durationSec"]
            job["extraArgs"] = list(timing["extraArgs"])
        except RuntimeError as e:
            if str(e) == "PROFILE_IN_USE":
                return api_error("PROFILE_IN_USE", "profile already has a running job", 409)
            if str(e) == "USID_IN_USE":
                return api_error(
                    "USID_IN_USE",
                    "desktop userServiceId already has a running job on another card",
                    409,
                )
            return api_error("VALIDATION", str(e))
        except ValueError as e:
            return api_error("VALIDATION", str(e))
        return JSONResponse({"ok": True, "job": job}, status_code=202)
    except RuntimeError as e:
        if str(e) == "PROFILE_IN_USE":
            return api_error("PROFILE_IN_USE", "profile already has a running job", 409)
        if str(e) == "USID_IN_USE":
            return api_error(
                "USID_IN_USE",
                "desktop userServiceId already has a running job on another card",
                409,
            )
        return api_error("VALIDATION", str(e))
    except ValueError as e:
        return api_error("VALIDATION", str(e))
    return JSONResponse({"ok": True, "job": job}, status_code=202)


async def jobs_get(request: Request) -> JSONResponse:
    jid = request.path_params["job_id"]
    job = ORCH.get_job(jid)
    if not job:
        return api_error("NOT_FOUND", f"job {jid} not found", 404)
    return JSONResponse({"ok": True, "job": job})


async def jobs_stop(request: Request) -> JSONResponse:
    jid = request.path_params["job_id"]
    job = ORCH.get_job(jid)
    if not job:
        return api_error("NOT_FOUND", f"job {jid} not found", 404)
    try:
        stopped = ORCH.stop_job(job["profileId"])
    except KeyError:
        return api_error("NOT_FOUND", "job already gone", 404)
    return JSONResponse({"ok": True, "job": stopped})


async def jobs_events(request: Request) -> StreamingResponse:
    jid = request.path_params["job_id"]
    job = ORCH.get_job(jid)
    if not job:
        return api_error("NOT_FOUND", f"job {jid} not found", 404)
    queue = ORCH.subscribe()

    async def gen() -> AsyncIterator[bytes]:
        try:
            data = json.dumps(
                {
                    "jobId": jid,
                    "profileId": job.get("profileId"),
                    "status": job.get("status"),
                    "at": _now_iso(),
                },
                ensure_ascii=False,
            )
            yield f"event: job_status\ndata: {data}\n\n".encode("utf-8")
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                payload = item.get("data") or {}
                if payload.get("jobId") and payload.get("jobId") != jid:
                    continue
                ev = item.get("event") or "message"
                line = json.dumps(redact_obj(payload), ensure_ascii=False)
                yield f"event: {ev}\ndata: {line}\n\n".encode("utf-8")
        finally:
            ORCH.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


async def logs_global(request: Request) -> JSONResponse:
    pid = request.query_params.get("profileId")
    jid = request.query_params.get("jobId")
    lines = ORCH.recent_logs(job_id=jid, profile_id=pid, limit=200)
    safe = [{"at": x.get("at"), "line": str(x.get("line", ""))[:2000]} for x in lines]
    return JSONResponse({"ok": True, "lines": safe})


async def index(request: Request) -> Response:
    index_path = _STATIC_DIR / "index.html"
    if index_path.is_file():
        # HARD_GATE#844: bust stale CSS/JS after layout hotfixes
        return FileResponse(
            index_path,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
            },
        )
    return JSONResponse({"ok": True, "message": "static shell missing", "api": "/api/system/health"})


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

routes = [
    Route("/", endpoint=index),
    Route("/index.html", endpoint=index),
    # health aliases: X2 `/api/health`, T3 `/health`, PM `/api/system/health`
    Route("/health", endpoint=health),
    Route("/api/health", endpoint=health),
    Route("/api/system/health", endpoint=health),
    Route("/api/system/info", endpoint=system_info),
    # X8 alias: OPEN gates mention /api/info
    Route("/api/info", endpoint=system_info),
    # Access gate (8317-style): public status/setup/login; change requires current token
    Route("/api/auth/status", endpoint=auth_status, methods=["GET"]),
    Route("/api/auth/setup", endpoint=auth_setup, methods=["POST"]),
    Route("/api/auth/login", endpoint=auth_login, methods=["POST"]),
    Route("/api/auth/change", endpoint=auth_change, methods=["POST"]),
    Route("/api/auth/disable", endpoint=auth_disable, methods=["POST"]),
    # X2 §3 profiles
    Route("/api/profiles", endpoint=profiles_list, methods=["GET"]),
    Route("/api/profiles", endpoint=profiles_create, methods=["POST"]),
    Route("/api/profiles/{profile_id}", endpoint=profiles_get, methods=["GET"]),
    Route("/api/profiles/{profile_id}", endpoint=profiles_patch, methods=["PATCH"]),
    Route("/api/profiles/{profile_id}", endpoint=profiles_delete, methods=["DELETE"]),
    Route("/api/profiles/{profile_id}/login", endpoint=profiles_login, methods=["POST"]),
    Route("/api/profiles/{profile_id}/desktops", endpoint=profiles_desktops, methods=["GET"]),
    Route("/api/profiles/{profile_id}/select-desktop", endpoint=profiles_select_desktop, methods=["POST"]),
    Route("/api/profiles/{profile_id}/jobs", endpoint=profiles_start_job, methods=["POST"]),
    Route("/api/profiles/{profile_id}/jobs/current", endpoint=profiles_stop_job, methods=["DELETE"]),
    Route("/api/profiles/{profile_id}/desktop-logout", endpoint=profiles_desktop_logout, methods=["POST"]),
    Route("/api/profiles/{profile_id}/logs", endpoint=profiles_logs, methods=["GET"]),
    Route("/api/profiles/{profile_id}/logs", endpoint=profiles_logs_clear, methods=["DELETE"]),
    Route("/api/profiles/{profile_id}/events", endpoint=profiles_events, methods=["GET"]),
    # Global SSE for FE EventSource("/api/events") — X7
    Route("/api/events", endpoint=events_global, methods=["GET"]),
    # T_PM-compatible jobs collection (poll fallback)
    Route("/api/jobs", endpoint=jobs_list, methods=["GET"]),
    Route("/api/jobs", endpoint=jobs_create, methods=["POST"]),
    Route("/api/jobs/{job_id}", endpoint=jobs_get, methods=["GET"]),
    Route("/api/jobs/{job_id}/stop", endpoint=jobs_stop, methods=["POST"]),
    Route("/api/jobs/{job_id}/events", endpoint=jobs_events, methods=["GET"]),
    Route("/api/logs", endpoint=logs_global, methods=["GET"]),
]

if _STATIC_DIR.is_dir():
    routes.append(Mount("/static", app=StaticFiles(directory=str(_STATIC_DIR)), name="static"))

app = Starlette(debug=os.environ.get("CMCC_WEBUI_DEBUG") == "1", routes=routes)
app.add_middleware(OptionalTokenMiddleware)


def main() -> None:
    import uvicorn

    host = os.environ.get("CMCC_WEBUI_HOST", "127.0.0.1")
    port = int(os.environ.get("CMCC_WEBUI_PORT", "8080"))
    uvicorn.run("cmcc_cloud_alive.webui.app:app", host=host, port=port, factory=False)


if __name__ == "__main__":
    main()

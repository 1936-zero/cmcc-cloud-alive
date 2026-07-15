"""Multi-profile keepalive job orchestrator (J2).

Parent-process only. Does NOT run keepalive loops on the ASGI event-loop
thread. Default mode is live (real child). dry-run only when mode explicitly
set to dry-run. #862 removed CMCC_WEBUI_ALLOW_LIVE gate.

Public method names match app.FakeOrchestrator so J3 `_load_orchestrator`
swaps in automatically.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["Orchestrator", "FakeBackend", "live_allowed"]


def _now_iso() -> str:
    # HARD_GATE#861: Shanghai wall clock so orch stamps match child short_time().
    # Previous UTC "...Z" made card logs jump 8h (e.g. 21:xx child vs 13:xx orch).
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def live_allowed() -> bool:
    """#862: LIVE always on — no CMCC_WEBUI_ALLOW_LIVE gate (debug = real)."""
    return True


def _data_dir() -> Path:
    """Unified durable root shared with CLI + WebUI app (X8).

    Matches ``cmcc_cloud_alive.webui.app._data_dir`` and core
    ``DEFAULT_DATA_DIR`` (``$HOME/.cmcc-cloud-alive``; Docker HOME=/data
    → ``/data/.cmcc-cloud-alive``).
    """
    explicit = os.environ.get("CMCC_DATA_DIR")
    if explicit:
        p = Path(explicit)
        if p.name == ".cmcc-cloud-alive":
            return p
        return p / ".cmcc-cloud-alive"
    raw = os.environ.get("CMCC_ALIVE_HOME") or os.environ.get("HOME") or str(Path.home())
    home = Path(raw)
    if home.name == ".cmcc-cloud-alive":
        return home
    return home / ".cmcc-cloud-alive"


def _jobs_dir() -> Path:
    d = _data_dir() / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _redact_line(line: str) -> str:
    """Strip obvious secret-ish tokens from log lines (never echo passwords)."""
    low = line.lower()
    for key in ("password", "passwd", "token", "secret", "authorization", "cookie"):
        if key in low:
            return f"[redacted:{key}]"
    return line[:2000]


def _fake_short_time() -> str:
    """Shanghai-style stamp matching core.short_time without importing CLI core."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _usid_from_state(state_path: Path) -> str:
    """Best-effort userServiceId from profile state JSON (dry-run markers only)."""
    try:
        raw = Path(state_path).read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        if isinstance(data, dict):
            usid = (
                data.get("userServiceId")
                or data.get("selectedUserServiceId")
                or data.get("user_service_id")
                or ""
            )
            if usid:
                return str(usid)
    except Exception:
        pass
    return "dry-run-svc"


def _live_creds_from_state(state_path: Path) -> Dict[str, str]:
    """Read username/password/usid from profile state for non-interactive live spawn.

    simple-keepalive --non-interactive needs argv credentials
    (main.py raises without --password). Values stay out of orch log lines; only the
    child argv carries them (Popen cmd is not logged).
    """
    out: Dict[str, str] = {}
    try:
        raw = Path(state_path).read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return out
    if not isinstance(data, dict):
        return out
    username = data.get("username") or data.get("phone") or ""
    password = data.get("password") or ""
    usid = (
        data.get("userServiceId")
        or data.get("selectedUserServiceId")
        or data.get("user_service_id")
        or ""
    )
    if username:
        out["username"] = str(username)
    if password:
        out["password"] = str(password)
    if usid:
        out["user_service_id"] = str(usid)
    return out



def _account_key_from_state(state_path: Path) -> str:
    """Normalize phone/username for same-account SCG first-connect serial gate."""
    try:
        data = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    raw = (
        data.get("username")
        or data.get("phone")
        or data.get("mobile")
        or data.get("account")
        or ""
    )
    s = str(raw).strip()
    if not s:
        return ""
    # digits-only phone form when possible
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 11:
        return digits[-11:]
    return s.lower()


class FakeBackend:
    """In-process dry-run backend: mirrors simple-keepalive interactive lines."""

    name = "fake"

    def __init__(
        self,
        orch: "Orchestrator",
        job_id: str,
        stop_evt: threading.Event,
        protocol: str = "ZTE",
        traffic_sec: Optional[int] = None,
        user_service_id: str = "dry-run-svc",
    ) -> None:
        self.orch = orch
        self.job_id = job_id
        self.stop_evt = stop_evt
        self.protocol = (protocol or "ZTE").upper()
        if self.protocol not in ("ZTE", "SCG"):
            self.protocol = "ZTE"
        self.traffic_sec = int(traffic_sec) if traffic_sec and int(traffic_sec) > 0 else 60
        self.user_service_id = user_service_id or "dry-run-svc"
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name=f"fake-job-{self.job_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self.stop_evt.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=timeout)

    def pid(self) -> Optional[int]:
        return None

    def _stamp(self) -> str:
        return _fake_short_time()

    def _emit_round(self, round_no: int) -> None:
        """Emit one simple-keepalive interactive round (CLI-shaped, no LIVE)."""
        proto = self.protocol
        kind = proto.lower()
        duration_cfg = self.traffic_sec
        duration_done = f"{float(duration_cfg) + 0.42:.2f}"
        stage_done = f"{kind}-keepalive-done"
        usid = self.user_service_id
        if proto == "SCG":
            hand = (
                f"[{self._stamp()}] 第{round_no}轮SCG保活：手选SCG，调用纯Python SCG协议 "
                f"duration={duration_cfg}s userServiceId={usid}"
            )
            done = (
                f"[{self._stamp()}] 第{round_no}轮SCG保活完成 "
                f"kind={kind} ok=True stage={stage_done} duration={duration_done}s"
            )
        else:
            hand = (
                f"[{self._stamp()}] 第{round_no}轮ZTE保活：手选ZTE，调用长测同款CAG/mux/raw-SPICE "
                f"duration={duration_cfg}s userServiceId={usid}"
            )
            done = (
                f"[{self._stamp()}] 第{round_no}轮ZTE保活完成 "
                f"kind={kind} ok=True stage={stage_done} duration={duration_done}s"
            )
        status = f"[{self._stamp()}] 云桌面状态：开机运行中"
        for line in (hand, done, status):
            if self.stop_evt.is_set():
                return
            self.orch._append_log(self.job_id, line)

    def _run(self) -> None:
        # Mirror simple-keepalive interactive boot/loop banners only.
        self.orch._append_log(
            self.job_id,
            "[orch] dry-run backend=fake (no LIVE child; simulated simple-keepalive lines)",
        )
        script = [
            "爱家移动云电脑",
            f"  协议：{self.protocol}",
            "[首次开机检查] 云电脑已运行，跳过开机，马上进入第一轮保活。",
            (
                f"进入保活循环：模式=永久 协议={self.protocol} "
                f"间隔=5分钟 单轮流量={self.traffic_sec}s"
            ),
        ]
        for line in script:
            if self.stop_evt.is_set():
                break
            self.orch._append_log(self.job_id, line)
            if self.stop_evt.wait(0.15):
                break

        round_no = 0
        while not self.stop_evt.is_set():
            round_no += 1
            self._emit_round(round_no)
            wait_s = 0.35 if round_no < 3 else 2.0
            if self.stop_evt.wait(wait_s):
                break
            if not self.stop_evt.is_set():
                self.orch._append_log(
                    self.job_id,
                    f"[{self._stamp()}] 云桌面状态：开机运行中",
                )
        self.orch._append_log(self.job_id, "[orch] dry-run backend stopped")


class SubprocessBackend:
    """LIVE child: python -m cmcc_cloud_alive --state <path> simple-keepalive ...

    Constructed when mode=live (always allowed after #862).
    """

    name = "subprocess"

    def __init__(
        self,
        orch: "Orchestrator",
        job_id: str,
        state_path: Path,
        protocol: str,
        extra_args: Optional[List[str]],
        stop_evt: threading.Event,
        log_path: Path,
        lock_path: Path,
        user_service_id: Optional[str] = None,
    ) -> None:
        self.orch = orch
        self.job_id = job_id
        self.state_path = state_path
        self.protocol = protocol
        self.extra_args = list(extra_args or [])
        self.stop_evt = stop_evt
        self.log_path = log_path
        self.lock_path = lock_path
        # HARD_GATE#868: per-card usid override (shared state holds token only)
        self.user_service_id = (user_service_id or "").strip() or None
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._lock_fd: Optional[int] = None

    def start(self) -> None:
        self._acquire_lock()
        cmd = self._build_cmd()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(self.log_path, "a", encoding="utf-8")  # noqa: SIM115 — kept open for child lifetime
        try:
            # HARD_GATE#870: never inherit a cwd that shadows site-packages with
            # stale /app/cmcc_cloud_alive (python -m prefers cwd first).
            self._proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                cwd="/data",
                env=self._child_env(),
            )
        except Exception as e:
            log_f.close()
            self._release_lock()
            raise RuntimeError(f"spawn failed: {e}") from e
        self.orch._append_log(
            self.job_id,
            f"[orch] live spawned pid={self._proc.pid} protocol={self.protocol} state={self.state_path.name}",
        )
        self._thread = threading.Thread(
            target=self._watch,
            args=(log_f,),
            name=f"live-job-{self.job_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self.stop_evt.set()
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.terminate()
                except Exception:
                    pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        proc.kill()
                    except Exception:
                        pass
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=timeout)
        self._release_lock()

    def pid(self) -> Optional[int]:
        if self._proc is None:
            return None
        return self._proc.pid if self._proc.poll() is None else self._proc.pid

    def _build_cmd(self) -> List[str]:
        # LIVE child matches Python simple interactive path:
        # simple-keepalive -> _simple_run_keepalive -> _simple_forced_keepalive(ZTE|SCG).
        # HARD_GATE#868: card usid override wins; shared acct_*.json supplies token/creds.
        creds = _live_creds_from_state(self.state_path)
        usid = (self.user_service_id or "") or (creds.get("user_service_id") or "")
        if not usid:
            raise RuntimeError("state missing userServiceId/selectedUserServiceId")
        proto = (self.protocol or "ZTE").upper()
        if proto not in ("ZTE", "SCG"):
            proto = "ZTE"
        cmd = [
            sys.executable,
            "-m",
            "cmcc_cloud_alive",
            "--state",
            str(self.state_path),
            "simple-keepalive",
            "--user-service-id",
            str(usid),
            "--protocol",
            proto,
        ]
        # extra_args already carries --interval-minutes/--traffic-seconds/--mode
        cmd.extend(self.extra_args)
        return cmd

    def _child_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env["CMCC_ORCH_JOB_ID"] = self.job_id
        env["CMCC_ORCH_PROTOCOL"] = self.protocol
        # HARD_GATE#870: drop PYTHONPATH so cwd=/data cannot re-introduce /app
        # package shadowing of site-packages (invalid choice: simple-keepalive).
        env.pop("PYTHONPATH", None)
        # Container must not send SPICE/SCG via HTTP(S) proxy (user hard rule).
        for k in (
            "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
            "all_proxy", "ALL_PROXY", "ftp_proxy", "FTP_PROXY",
        ):
            env.pop(k, None)
        # Prefer direct for all destinations when a proxy-capable stack is present.
        env["NO_PROXY"] = "*"
        env["no_proxy"] = "*"
        # Ensure child does not inherit a webui token requirement that confuses CLI
        return env

    def _acquire_lock(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception as e:
            os.close(fd)
            raise RuntimeError(f"PROFILE_LOCK: {e}") from e
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        self._lock_fd = fd

    def _release_lock(self) -> None:
        fd = self._lock_fd
        self._lock_fd = None
        if fd is None:
            return
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            if self.lock_path.is_file():
                self.lock_path.unlink()
        except Exception:
            pass

    def _watch(self, log_f: Any) -> None:
        """Incremental tail of child stdout file → ring buffer as Python-raw lines.

        Card UI dumps `x.line` only. Do NOT prefix child lines with `[live]`;
        multi-line bursts must all pass through (not last-line-only).
        """
        proc = self._proc
        offset = 0
        pending = ""
        try:
            # Start at EOF so we only stream post-spawn output (spawn meta is [orch]).
            try:
                if self.log_path.is_file():
                    offset = self.log_path.stat().st_size
            except Exception:
                offset = 0
            while proc and proc.poll() is None and not self.stop_evt.is_set():
                try:
                    offset, pending = self._drain_log(offset, pending)
                except Exception:
                    pass
                if self.stop_evt.wait(0.5):
                    break
            # Final drain after child exit / stop
            try:
                offset, pending = self._drain_log(offset, pending, final=True)
            except Exception:
                pass
            rc = proc.returncode if proc else None
            if self.stop_evt.is_set():
                self.orch._mark_stopped(self.job_id, detail="stopped by API", exit_code=rc)
            else:
                status = "stopped" if rc == 0 else "error"
                self.orch._mark_stopped(
                    self.job_id,
                    detail=f"child exited rc={rc}",
                    exit_code=rc,
                    status=status,
                )
        finally:
            try:
                log_f.close()
            except Exception:
                pass
            self._release_lock()

    def _drain_log(self, offset: int, pending: str, *, final: bool = False) -> tuple:
        """Read new bytes from log_path; append complete lines as raw (redacted) text."""
        if not self.log_path.is_file():
            return offset, pending
        with open(self.log_path, "r", encoding="utf-8", errors="replace") as rf:
            rf.seek(offset)
            chunk = rf.read()
            offset = rf.tell()
        if not chunk:
            if final and pending.strip():
                self.orch._append_log(self.job_id, pending.rstrip("\r\n"))
                pending = ""
            return offset, pending
        data = pending + chunk
        lines = data.splitlines(keepends=True)
        if lines and not lines[-1].endswith(("\n", "\r")):
            pending = lines.pop()
        else:
            pending = ""
        for raw in lines:
            line = raw.rstrip("\r\n")
            if line == "":
                continue
            # Raw child stdout only — _append_log already redacts secrets.
            self.orch._append_log(self.job_id, line)
        if final and pending.strip():
            self.orch._append_log(self.job_id, pending.rstrip("\r\n"))
            pending = ""
        return offset, pending


class Orchestrator:
    """In-memory job table + per-profile mutex + dry-run/LIVE backends."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._by_profile: Dict[str, str] = {}
        # userServiceId -> job_id (same desktop must not stack across cards)
        self._by_usid: Dict[str, str] = {}
        # same-account+same-protocol first-connect serial: "acct|proto" -> {jobId, t0, protocol}
        # HARD_GATE#871d-proto-serial1: cross-protocol (SCG vs ZTE) does NOT share the gate.
        self._account_scg_gate: Dict[str, Dict[str, Any]] = {}
        self._log_buffers: Dict[str, List[Dict[str, str]]] = {}
        self._last_log_line: Dict[str, str] = {}
        self._subscribers: List[asyncio.Queue] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._backends: Dict[str, Any] = {}  # job_id -> backend
        self._stop_events: Dict[str, threading.Event] = {}

    # ------------------------------------------------------------------
    # SSE / loop
    # ------------------------------------------------------------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def _emit(self, event: str, data: Dict[str, Any]) -> None:
        payload = {"event": event, "data": data}
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

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

    @staticmethod
    def _sticky_key(profile_id: str) -> str:
        """HARD_GATE#854: profile-sticky log buffer key (survives job remaps)."""
        return "p:" + str(profile_id)

    def recent_logs(
        self,
        job_id: Optional[str] = None,
        profile_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, str]]:
        """Return job/card logs only when scoped.

        HARD_GATE#768-B / ASSIGN#785#4: unscoped /api/logs must not flatten
        job buffers into page-level global log. Card logs stay profile/job scoped
        via /api/profiles/{id}/logs or ?profileId=/jobId=.

        HARD_GATE#854: prefer active job buffer; fall back / merge sticky profile
        buffer so logs reappear after clear even if job mapping races.
        """
        with self._lock:
            pid = profile_id
            if not job_id and profile_id:
                job_id = self._by_profile.get(profile_id)
            if not job_id and profile_id:
                # fallback: newest job for this profile that still has a buffer
                candidates = []
                for jid, job in self._jobs.items():
                    if (job or {}).get("profileId") == profile_id:
                        candidates.append((job.get("startedAt") or job.get("createdAt") or "", jid))
                if candidates:
                    candidates.sort()
                    job_id = candidates[-1][1]
                else:
                    # last resort: any buffer whose job table maps to profile
                    for jid, buf in self._log_buffers.items():
                        if str(jid).startswith("p:"):
                            continue
                        job = self._jobs.get(jid) or {}
                        if job.get("profileId") == profile_id and buf:
                            job_id = jid
                            break
            if not pid and job_id:
                pid = (self._jobs.get(job_id) or {}).get("profileId")
            job_lines = list(self._log_buffers.get(job_id, [])) if job_id else []
            sticky_lines: List[Dict[str, str]] = []
            if pid:
                sticky_lines = list(self._log_buffers.get(self._sticky_key(pid), []))
            if job_lines and sticky_lines:
                # merge by (at,line) preserving order, prefer sticky chronology then job
                seen = set()
                merged: List[Dict[str, str]] = []
                for entry in sticky_lines + job_lines:
                    key = (entry.get("at") or "", entry.get("line") or "")
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(entry)
                return merged[-limit:]
            if job_lines:
                return job_lines[-limit:]
            if sticky_lines:
                return sticky_lines[-limit:]
            return []

    def clear_logs(
        self,
        job_id: Optional[str] = None,
        profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """HARD_GATE#853/#854: really clear job+sticky card log buffers (not FE-only)."""
        with self._lock:
            if not job_id and profile_id:
                job_id = self._by_profile.get(profile_id)
            # also clear any historical buffers that still map to this profile
            targets = set()
            if job_id:
                targets.add(job_id)
            if profile_id:
                for jid, job in self._jobs.items():
                    if (job or {}).get("profileId") == profile_id:
                        targets.add(jid)
                # buffers may outlive job table; keep by_profile mapping job
                mapped = self._by_profile.get(profile_id)
                if mapped:
                    targets.add(mapped)
                # HARD_GATE#854: always clear sticky profile buffer even if no job map
                targets.add(self._sticky_key(profile_id))
            cleared = 0
            for jid in list(targets):
                buf = self._log_buffers.get(jid)
                if buf:
                    cleared += len(buf)
                # keep key present so live backend keeps appending to same buffer
                self._log_buffers[jid] = []
                self._last_log_line.pop(jid, None)
            # HARD_GATE#854: never drop by_profile mapping on clear (new logs must reattach)
            pid = profile_id
            if not pid and job_id:
                pid = (self._jobs.get(job_id) or {}).get("profileId")
            if pid:
                # ensure sticky key exists empty so subsequent appends dual-write cleanly
                self._log_buffers[self._sticky_key(pid)] = []
                self._last_log_line.pop(self._sticky_key(pid), None)
            if pid and pid not in self._by_profile and job_id:
                self._by_profile[pid] = job_id
            elif pid and job_id and self._by_profile.get(pid) not in targets and job_id in targets:
                # keep mapped id if it was a target (already mapped)
                pass
        if pid or job_id:
            self._emit(
                "job_log_cleared",
                {
                    "jobId": job_id,
                    "profileId": pid,
                    "cleared": cleared,
                    "at": _now_iso(),
                },
            )
        return {
            "ok": True,
            "jobId": job_id,
            "profileId": pid,
            "cleared": cleared,
        }

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

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
        user_service_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        protocol = (protocol or "ZTE").upper()
        if protocol not in ("ZTE", "SCG"):
            raise ValueError("protocol must be ZTE or SCG")
        mode = (mode or "live").lower()
        # #848: 永久(live)/单轮(once|single) 都走真子进程；仅显式 dry-run 走 FakeBackend
        if mode in ("dry-run", "dryrun", "fake", "sim", "simulate"):
            mode = "dry-run"
        elif mode in ("live", "prod", "production", "once", "single", "forever", "permanent", "loop"):
            # #862: always allow live; gate removed
            mode = "live"
        else:
            # unknown -> live (real keepalive)
            mode = "live"

        state_path = Path(state_path)

        # HARD_GATE#871d-acct-serial1: same-account SCG live first-connect serial.
        # HARD_GATE#871d-acct-serial2: extend first-connect serial to ZTE live as well.
        # HARD_GATE#871d-proto-serial1: gate key = account|protocol — cross-protocol no wait.
        # HARD_GATE#871d-relogin-serial1: token/auth same-account re-login flock (see token.py).
        # Dual-card same login+same protocol thrash platform session if first-connect races; stagger ~75s.
        # dry-run skip. Blocks caller thread (app must asyncio.to_thread).
        account_key = ""
        gate_key = ""
        if protocol in ("SCG", "ZTE") and mode == "live":
            account_key = _account_key_from_state(state_path)
            if account_key:
                # same-account + same-protocol only; SCG vs ZTE run in parallel
                gate_key = f"{account_key}|{protocol}"
                serial_sec = float(
                    os.environ.get(
                        "CMCC_ACCOUNT_SERIAL_SEC",
                        os.environ.get("CMCC_SCG_ACCOUNT_SERIAL_SEC", "75"),
                    )
                    or "75"
                )
                if serial_sec < 0:
                    serial_sec = 0.0
                # wait until previous same-account+same-protocol live job's first-connect window ends
                while serial_sec > 0:
                    wait_more = 0.0
                    with self._lock:
                        gate = self._account_scg_gate.get(gate_key)
                        if not gate:
                            break
                        other_jid = str(gate.get("jobId") or "")
                        t0 = float(gate.get("t0") or 0.0)
                        other = self._jobs.get(other_jid) if other_jid else None
                        elapsed = time.time() - t0 if t0 else serial_sec
                        # free gate if other gone / not running / window elapsed
                        if (
                            not other
                            or other.get("status") not in ("running", "pending")
                            or elapsed >= serial_sec
                        ):
                            if gate is self._account_scg_gate.get(gate_key):
                                # only drop if still same gate entry
                                if not other or other.get("status") not in ("running", "pending"):
                                    self._account_scg_gate.pop(gate_key, None)
                            break
                        # same profile re-entry handled by PROFILE_IN_USE later
                        if other and other.get("profileId") == profile_id:
                            break
                        wait_more = min(serial_sec - elapsed, 1.0)
                        if wait_more <= 0:
                            break
                    if wait_more > 0:
                        time.sleep(wait_more)
                    else:
                        break

        with self._lock:
            existing = self._by_profile.get(profile_id)
            if existing and self._jobs.get(existing, {}).get("status") == "running":
                raise RuntimeError("PROFILE_IN_USE")

            usid = (user_service_id or "").strip()
            if usid:
                old_jid = self._by_usid.get(usid)
                old_job = self._jobs.get(old_jid) if old_jid else None
                if (
                    old_job
                    and old_job.get("status") == "running"
                    and old_job.get("profileId") != profile_id
                ):
                    # Strategy 1: refuse stack same desktop on another card
                    raise RuntimeError("USID_IN_USE")

            job_id = uuid.uuid4().hex[:12]
            job = {
                "id": job_id,
                "jobId": job_id,
                "profileId": profile_id,
                "statePath": str(state_path),
                "protocol": protocol,
                "mode": mode,
                "status": "running",
                "pid": None,
                "startedAt": _now_iso(),
                "stoppedAt": None,
                "detail": (
                    "dry-run FakeBackend (no LIVE child)"
                    if mode == "dry-run"
                    else "live subprocess pending"
                ),
                "extraArgs": list(extra_args or []),
                "intervalSec": interval_sec,
                "trafficSec": traffic_sec,
                "durationSec": duration_sec,
                "backend": "fake" if mode == "dry-run" else "subprocess",
                "exitCode": None,
                "userServiceId": usid or None,
            }
            self._jobs[job_id] = job
            self._by_profile[profile_id] = job_id
            if usid:
                self._by_usid[usid] = job_id
            if account_key:
                job["accountKey"] = account_key
                if protocol in ("SCG", "ZTE") and mode == "live":
                    # HARD_GATE#871d-proto-serial1: key includes protocol
                    gk = gate_key or f"{account_key}|{protocol}"
                    job["serialGateKey"] = gk
                    self._account_scg_gate[gk] = {
                        "jobId": job_id,
                        "t0": time.time(),
                        "profileId": profile_id,
                        "protocol": protocol,
                        "accountKey": account_key,
                    }
            self._log_buffers.setdefault(job_id, [])
            stop_evt = threading.Event()
            self._stop_events[job_id] = stop_evt

        # start backend outside lock (may block on flock for LIVE)
        try:
            if mode == "dry-run":
                backend = FakeBackend(
                    self,
                    job_id,
                    stop_evt,
                    protocol=protocol,
                    traffic_sec=traffic_sec,
                    user_service_id=(user_service_id or _usid_from_state(state_path)),
                )
            else:
                jdir = _jobs_dir() / job_id
                jdir.mkdir(parents=True, exist_ok=True)
                log_path = jdir / "worker.log"
                lock_path = _data_dir() / "locks" / f"{profile_id}.lock"
                backend = SubprocessBackend(
                    self,
                    job_id,
                    state_path=state_path,
                    protocol=protocol,
                    extra_args=extra_args,
                    stop_evt=stop_evt,
                    log_path=log_path,
                    lock_path=lock_path,
                    user_service_id=user_service_id,
                )
            backend.start()
            with self._lock:
                self._backends[job_id] = backend
                pid = backend.pid()
                if pid is not None:
                    self._jobs[job_id]["pid"] = pid
                    self._jobs[job_id]["detail"] = f"live subprocess pid={pid}"
                job_out = dict(self._jobs[job_id])
        except Exception as e:
            with self._lock:
                j = self._jobs.get(job_id)
                if j:
                    j["status"] = "error"
                    j["stoppedAt"] = _now_iso()
                    j["detail"] = f"start failed: {e}"
                    fail_usid = (j.get("userServiceId") or "").strip()
                    if fail_usid and self._by_usid.get(fail_usid) == job_id:
                        self._by_usid.pop(fail_usid, None)
                    fail_gk = (j.get("serialGateKey") or "").strip()
                    if not fail_gk:
                        # legacy fallback: pre-proto-serial used bare accountKey
                        fail_ak = (j.get("accountKey") or "").strip()
                        fail_proto = (j.get("protocol") or "").strip()
                        if fail_ak and fail_proto:
                            fail_gk = f"{fail_ak}|{fail_proto}"
                        else:
                            fail_gk = fail_ak
                    gate = self._account_scg_gate.get(fail_gk) if fail_gk else None
                    if gate and gate.get("jobId") == job_id:
                        self._account_scg_gate.pop(fail_gk, None)
                self._stop_events.pop(job_id, None)
            self._append_log(job_id, f"[orch] start failed: {e}")
            self._emit(
                "job_status",
                {
                    "jobId": job_id,
                    "profileId": profile_id,
                    "status": "error",
                    "at": _now_iso(),
                    "detail": str(e),
                },
            )
            raise

        ak_note = f" account={account_key}" if account_key else ""
        self._append_log(
            job_id,
            f"[orch] start protocol={protocol} mode={mode} state={state_path.name}{ak_note}",
        )
        self._emit(
            "job_status",
            {
                "jobId": job_id,
                "profileId": profile_id,
                "status": "running",
                "at": job_out["startedAt"],
                "detail": job_out["detail"],
            },
        )
        return job_out

    def stop_job(self, profile_id: str) -> Dict[str, Any]:
        with self._lock:
            jid = self._by_profile.get(profile_id)
            if not jid or jid not in self._jobs:
                raise KeyError("NOT_FOUND")
            job = self._jobs[jid]
            if job.get("status") != "running":
                return dict(job)
            backend = self._backends.get(jid)
            stop_evt = self._stop_events.get(jid)
            usid = (job.get("userServiceId") or "").strip()
            state_path = job.get("statePath")

        # Graceful remote release BEFORE local SIGTERM (stop/clear buttons).
        # Local kill alone can leave SOHO/SCG ghost sessions → MAIN_INIT missing.
        if usid and state_path:
            try:
                from cmcc_cloud_alive import logout as logout_mod

                self._append_log(
                    jid,
                    f"[orch] stop: desktop_logout before kill usid={usid}",
                )
                logout_mod.desktop_logout(
                    user_service_id=usid,
                    state_path=str(state_path),
                )
                self._append_log(jid, "[orch] stop: desktop_logout ok")
            except Exception as e:
                self._append_log(
                    jid,
                    f"[orch] stop: desktop_logout failed (continue kill): {e}",
                )

        if stop_evt is not None:
            stop_evt.set()
        if backend is not None:
            try:
                backend.stop()
            except Exception as e:
                self._append_log(jid, f"[orch] stop error: {e}")

        return self._mark_stopped(jid, detail="stopped by API")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_log(self, job_id: str, line: str, *, dedupe: bool = False) -> None:
        safe = _redact_line(line)
        at = _now_iso()
        with self._lock:
            if dedupe and self._last_log_line.get(job_id) == safe:
                return
            self._last_log_line[job_id] = safe
            buf = self._log_buffers.setdefault(job_id, [])
            entry = {"at": at, "line": safe}
            buf.append(entry)
            # cap buffer
            if len(buf) > 500:
                del buf[: len(buf) - 500]
            profile_id = (self._jobs.get(job_id) or {}).get("profileId")
            # HARD_GATE#854: dual-write sticky profile buffer so card logs
            # reappear after clear even if job mapping races.
            if profile_id:
                sk = self._sticky_key(profile_id)
                if dedupe and self._last_log_line.get(sk) == safe:
                    pass
                else:
                    self._last_log_line[sk] = safe
                    sbuf = self._log_buffers.setdefault(sk, [])
                    sbuf.append(entry)
                    if len(sbuf) > 500:
                        del sbuf[: len(sbuf) - 500]
        self._emit(
            "job_log",
            {"jobId": job_id, "profileId": profile_id, "at": at, "line": safe},
        )

    def _mark_stopped(
        self,
        job_id: str,
        *,
        detail: str,
        exit_code: Optional[int] = None,
        status: str = "stopped",
    ) -> Dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return {"jobId": job_id, "status": status, "detail": detail}
            if job.get("status") not in ("running", "pending"):
                return dict(job)
            job["status"] = status
            job["stoppedAt"] = _now_iso()
            job["detail"] = detail
            if exit_code is not None:
                job["exitCode"] = exit_code
            profile_id = job.get("profileId")
            usid = (job.get("userServiceId") or "").strip()
            if usid and self._by_usid.get(usid) == job_id:
                self._by_usid.pop(usid, None)
            gk = (job.get("serialGateKey") or "").strip()
            if not gk:
                # legacy fallback: pre-proto-serial used bare accountKey
                ak = (job.get("accountKey") or "").strip()
                proto = (job.get("protocol") or "").strip()
                if ak and proto:
                    gk = f"{ak}|{proto}"
                else:
                    gk = ak
            gate = self._account_scg_gate.get(gk) if gk else None
            if gate and gate.get("jobId") == job_id:
                self._account_scg_gate.pop(gk, None)
            out = dict(job)
            self._backends.pop(job_id, None)
            self._stop_events.pop(job_id, None)
        self._append_log(job_id, f"[orch] {detail}")
        self._emit(
            "job_status",
            {
                "jobId": job_id,
                "profileId": profile_id,
                "status": status,
                "at": out["stoppedAt"],
                "detail": detail,
            },
        )
        return out

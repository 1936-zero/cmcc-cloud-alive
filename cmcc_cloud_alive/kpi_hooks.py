#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SCG keepalive KPI / hold observability hooks (I-G4 + I-PHASE-I-KPI).

Observed-only counters — never synthesizes 174-byte WAN pads.
Wire points live in scg_route; this module is pure state + JSON export.

I-PHASE-I-KPI: 4-sample VM power state via power_monitor.snapshot at
start / 1/3 / 2/3 / end. wall-clock hold duration ≠ VM powered claim.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default under package .runtime (same tree as core.DEFAULT_STATE)
DEFAULT_KPI_DIR = Path(__file__).resolve().parents[1] / ".runtime"
DEFAULT_KPI_PATH = DEFAULT_KPI_DIR / "scg_kpi.json"

# Channel id names matching scg_route CHANNEL_* constants
CHANNEL_NAMES = {
    1: "main",
    2: "display",
    3: "inputs",
    4: "cursor",
    5: "playback",
    6: "record",
}

# Canonical 4-sample phase labels (I-PHASE-I-KPI)
VM_SAMPLE_PHASES = ("start", "one_third", "two_thirds", "end")


def _now() -> float:
    return time.time()


def _mono() -> float:
    return time.monotonic()


@dataclass
class ChannelOpenInfo:
    channel_id: int
    name: str = ""
    opened_at: float = 0.0
    redq_ok: bool = False
    ticket_ok: bool = False
    auth_ok: bool = False
    last_error: str = ""


@dataclass
class KpiSnapshot:
    """Serializable KPI snapshot (no secrets)."""

    ts: float = 0.0
    session_tag: str = ""
    wan_174b_ticks: int = 0
    wan_174b_last_age_s: Optional[float] = None
    last_hyscg_age_s: Optional[float] = None
    last_hyscg_mono: Optional[float] = None
    redq_count: int = 0
    ticket_count: int = 0
    ticket_fail: int = 0
    channel_open_map: Dict[str, Any] = field(default_factory=dict)
    hold_heartbeats: int = 0
    hold_replies: int = 0
    spice_ok: bool = False
    degraded: bool = False
    # I-PHASE-I-KPI: independent VM power samples (not wall-clock proxy)
    vm_samples: List[Dict[str, Any]] = field(default_factory=list)
    vm_sample_count: int = 0
    vm_powered_throughout: Optional[bool] = None
    wall_hold_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class KpiCollector:
    """Session-scoped observed-only KPI collector."""

    def __init__(self, session_tag: str = "", path: Optional[Path] = None) -> None:
        self.session_tag = session_tag or ""
        self.path = Path(path) if path else DEFAULT_KPI_PATH
        self.started_mono = _mono()
        self.started_wall = _now()
        self.wan_174b_ticks = 0
        self.wan_174b_last_mono: Optional[float] = None
        self.last_hyscg_mono: Optional[float] = None
        self.redq_count = 0
        self.ticket_count = 0
        self.ticket_fail = 0
        self.channel_open: Dict[int, ChannelOpenInfo] = {}
        self.hold_heartbeats = 0
        self.hold_replies = 0
        self.spice_ok = False
        self.degraded = False
        # I-PHASE-I-KPI
        self.vm_samples: List[Dict[str, Any]] = []
        self._vm_phases_done: set = set()
        self.wall_hold_seconds: Optional[float] = None
        self._lock = threading.Lock()

    # ---- observed counters (never synthesize WAN pads) ----

    def note_wan_174b(self, size: int = 174) -> None:
        if size != 174:
            return
        with self._lock:
            self.wan_174b_ticks += 1
            self.wan_174b_last_mono = _mono()

    def note_hyscg(self) -> None:
        with self._lock:
            self.last_hyscg_mono = _mono()

    def note_redq(self, channel_id: int = 0) -> None:
        with self._lock:
            self.redq_count += 1
            info = self.channel_open.get(int(channel_id))
            if info is not None:
                info.redq_ok = True

    def note_ticket(self, channel_id: int = 0, ok: bool = True) -> None:
        with self._lock:
            if ok:
                self.ticket_count += 1
            else:
                self.ticket_fail += 1
            info = self.channel_open.get(int(channel_id))
            if info is not None:
                info.ticket_ok = bool(ok)

    def note_channel_open(self, channel_id: int, auth_ok: bool = False, error: str = "") -> None:
        cid = int(channel_id)
        with self._lock:
            info = self.channel_open.get(cid)
            if info is None:
                info = ChannelOpenInfo(
                    channel_id=cid,
                    name=CHANNEL_NAMES.get(cid, "ch%d" % cid),
                    opened_at=_now(),
                )
                self.channel_open[cid] = info
            info.auth_ok = bool(auth_ok)
            if error:
                info.last_error = str(error)

    def note_hold_heartbeat(self) -> None:
        with self._lock:
            self.hold_heartbeats += 1

    def note_hold_reply(self) -> None:
        with self._lock:
            self.hold_replies += 1

    def set_spice_ok(self, ok: bool) -> None:
        with self._lock:
            self.spice_ok = bool(ok)

    def set_degraded(self, degraded: bool) -> None:
        with self._lock:
            self.degraded = bool(degraded)

    def set_wall_hold_seconds(self, seconds: float) -> None:
        """Record wall-clock hold duration (distinct from VM powered claim)."""
        with self._lock:
            self.wall_hold_seconds = float(seconds)

    def note_vm_sample(
        self,
        phase: str,
        snap: Optional[Dict[str, Any]] = None,
        *,
        error: str = "",
    ) -> None:
        """Record one of the 4 canonical VM power samples (observed-only).

        phase ∈ {start, one_third, two_thirds, end}.
        snap is power_monitor.snapshot() result (or None on failure).
        Does NOT claim VM powered from wall-clock alone.
        """
        phase = str(phase or "").strip().lower()
        if phase not in VM_SAMPLE_PHASES:
            # still record under raw phase for diagnostics, but mark non-canonical
            pass
        record: Dict[str, Any] = {
            "phase": phase,
            "at_wall": _now(),
            "at_mono": _mono(),
            "elapsed_hold_s": round(_mono() - self.started_mono, 3),
        }
        if error:
            record["error"] = str(error)
            record["running"] = None
            record["off"] = None
            record["vmStatus"] = None
        elif isinstance(snap, dict):
            record["running"] = snap.get("running")
            record["off"] = snap.get("off")
            record["vmStatus"] = snap.get("vmStatus")
            record["vmStatusShow"] = snap.get("vmStatusShow")
            record["at"] = snap.get("at")
            record["userServiceId"] = snap.get("userServiceId")
            record["index"] = snap.get("index")
            if "elapsedSeconds" in snap:
                record["elapsedSeconds"] = snap.get("elapsedSeconds")
        else:
            record["error"] = "empty_snapshot"
            record["running"] = None
            record["off"] = None
            record["vmStatus"] = None
        with self._lock:
            # de-dupe same phase (keep first)
            if phase in self._vm_phases_done and phase in VM_SAMPLE_PHASES:
                return
            if phase in VM_SAMPLE_PHASES:
                self._vm_phases_done.add(phase)
            self.vm_samples.append(record)

    def note_vm_sample_raw(self, record: Dict[str, Any]) -> None:
        """Append a pre-built sample dict (tests / advanced)."""
        with self._lock:
            self.vm_samples.append(dict(record or {}))

    def _vm_powered_throughout(self) -> Optional[bool]:
        """True only with multi-sample evidence that every conclusive sample is powered.

        I-D2-P06 discipline (G4 honesty):
        - None if no conclusive samples (do not invent PASS).
        - False if any conclusive sample is OFF/error, OR only a single conclusive
          sample exists (single-sample is not "throughout").
        - True only when >=2 conclusive samples are all running&!off.
          Prefer full 4 canonical phases (start/one_third/two_thirds/end);
          4-of-4 all powered is the strong PASS path. Partial multi-sample
          (>=2, all powered) is still True; single-sample never True.
        """
        verdicts = []
        for s in self.vm_samples:
            if s.get("error"):
                verdicts.append(False)
                continue
            if s.get("running") is None and s.get("off") is None:
                continue
            ok = bool(s.get("running")) and not bool(s.get("off"))
            verdicts.append(ok)
        if not verdicts:
            return None
        if not all(verdicts):
            return False
        # Multi-sample floor: one green snapshot ≠ powered throughout
        if len(verdicts) < 2:
            return False
        return True

    def snapshot(self) -> KpiSnapshot:
        now_m = _mono()
        with self._lock:
            wan_age = None
            if self.wan_174b_last_mono is not None:
                wan_age = round(now_m - self.wan_174b_last_mono, 3)
            hyscg_age = None
            if self.last_hyscg_mono is not None:
                hyscg_age = round(now_m - self.last_hyscg_mono, 3)
            ch_map: Dict[str, Any] = {}
            for cid, info in self.channel_open.items():
                ch_map[str(cid)] = {
                    "channel_id": info.channel_id,
                    "name": info.name,
                    "opened_at": info.opened_at,
                    "redq_ok": info.redq_ok,
                    "ticket_ok": info.ticket_ok,
                    "auth_ok": info.auth_ok,
                    "last_error": info.last_error,
                }
            vm_copy = [dict(x) for x in self.vm_samples]
            powered = self._vm_powered_throughout()
            return KpiSnapshot(
                ts=_now(),
                session_tag=self.session_tag,
                wan_174b_ticks=self.wan_174b_ticks,
                wan_174b_last_age_s=wan_age,
                last_hyscg_age_s=hyscg_age,
                last_hyscg_mono=self.last_hyscg_mono,
                redq_count=self.redq_count,
                ticket_count=self.ticket_count,
                ticket_fail=self.ticket_fail,
                channel_open_map=ch_map,
                hold_heartbeats=self.hold_heartbeats,
                hold_replies=self.hold_replies,
                spice_ok=self.spice_ok,
                degraded=self.degraded,
                vm_samples=vm_copy,
                vm_sample_count=len(vm_copy),
                vm_powered_throughout=powered,
                wall_hold_seconds=self.wall_hold_seconds,
            )

    def flush_json(self) -> Path:
        out = self.path
        out.parent.mkdir(parents=True, exist_ok=True)
        snap = self.snapshot()
        text = json.dumps(snap.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        out.write_text(text, encoding="utf-8")
        try:
            out.chmod(0o600)
        except OSError:
            pass
        return out

    def merge_into_stats(self, stats: Dict[str, Any]) -> Dict[str, Any]:
        """Merge KPI fields into scg_route result stats dict (in-place)."""
        snap = self.snapshot()
        stats["kpi"] = snap.to_dict()
        stats["wan_174b_ticks"] = snap.wan_174b_ticks
        stats["last_hyscg_age_s"] = snap.last_hyscg_age_s
        stats["channel_open_map"] = snap.channel_open_map
        stats["redq_count"] = snap.redq_count
        stats["ticket_count"] = snap.ticket_count
        # I-PHASE-I-KPI surface (P06/P07 field split)
        stats["vm_samples"] = snap.vm_samples
        stats["vm_sample_count"] = snap.vm_sample_count
        stats["vm_powered_throughout"] = snap.vm_powered_throughout
        # Alias for product/report schema (P07): same multi-sample truth
        stats["vm_running_throughout"] = snap.vm_powered_throughout
        stats["wall_hold_seconds"] = snap.wall_hold_seconds
        # Honesty: wall clock duration alone must NOT set product ok / spice_ok
        # Callers must not treat wall_hold_seconds as VM powered proof.
        return stats


# Process-wide optional collector (set by scg_route per session)
_ACTIVE: Optional[KpiCollector] = None
_ACTIVE_LOCK = threading.Lock()


def start_session(session_tag: str = "", path: Optional[Path] = None) -> KpiCollector:
    global _ACTIVE
    c = KpiCollector(session_tag=session_tag, path=path)
    with _ACTIVE_LOCK:
        _ACTIVE = c
    return c


def get_active() -> Optional[KpiCollector]:
    with _ACTIVE_LOCK:
        return _ACTIVE


def end_session(flush: bool = True) -> Optional[Path]:
    global _ACTIVE
    with _ACTIVE_LOCK:
        c = _ACTIVE
        _ACTIVE = None
    if c is None:
        return None
    if flush:
        return c.flush_json()
    return None


def maybe(method: str, *args: Any, **kwargs: Any) -> None:
    """No-op-safe call into active collector (safe if hooks disabled)."""
    c = get_active()
    if c is None:
        return
    fn = getattr(c, method, None)
    if callable(fn):
        try:
            fn(*args, **kwargs)
        except Exception:
            pass


def maybe_vm_sample_via_power_monitor(
    phase: str,
    user_service_id: str = "",
    state_path: Optional[str] = None,
    started_wall: Optional[float] = None,
    index: Optional[int] = None,
) -> None:
    """Best-effort VM sample for active session. No-op if no session / no usid.

    Uses power_monitor.snapshot only (no keepalive/boot/CAG). Failures recorded
    as error samples — never invents running=True.
    """
    c = get_active()
    if c is None:
        return
    if not user_service_id:
        c.note_vm_sample(phase, None, error="no_user_service_id")
        return
    try:
        from . import power_monitor  # local import: avoid cycle at module load

        snap = power_monitor.snapshot(
            user_service_id=user_service_id,
            state_path=state_path,
            started=started_wall,
            index=index,
        )
        c.note_vm_sample(phase, snap)
    except Exception as exc:  # pragma: no cover - network/env
        c.note_vm_sample(phase, None, error="snapshot_failed:%s" % type(exc).__name__)

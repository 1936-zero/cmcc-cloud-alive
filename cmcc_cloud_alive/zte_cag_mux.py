#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZTE CAG multiplexer — multiple inner links over one TLS conn.

Pure-Python port of B's ``internal/zte/cag_mux.go``.

A :class:`CAGMux` wraps a single raw (already-TLS) socket and demultiplexes
the CAG proxy framing protocol into many :class:`CAGMuxLink` virtual links.
Each link is opened with an *add-link* packet (built by
:mod:`zte_cag_proxy`) and carries independent SPICE channels (main,
inputs, cursor, …).

Architecture (mirrors B's Go goroutine model with a background thread):

    CAGMux
      ├── readLoop()  [daemon thread]  →  read_frame → dispatch by linkID
      ├── links: {linkID: CAGMuxLink}
      └── writeFrame(cmd, linkID, payload)  [locked]

    CAGMuxLink
      ├── rbuf + Condition  (readLoop produces, Read consumes)
      ├── Write → writeFrame(DATA, linkID, chunked)
      └── Close → writeFrame(CLOSE, linkID)
"""

from __future__ import annotations

import socket
import threading
from typing import Dict, Optional

from .zte_cag_proxy import (
    CAG_PROXY_CLOSE_LINK_CMD,
    CAG_PROXY_DATA_CMD,
    CAG_PROXY_PAYLOAD_MAX,
    build_cag_proxy_add_link_packet,
    new_zte_link_uuid,
    pack_frame,
    random_hex,
    read_frame,
)

__all__ = ["CAGMux", "CAGMuxLink", "open_cag_mux_link"]


class CAGMuxLink:
    """One virtual link inside a :class:`CAGMux` (port of B's ``CAGMuxLink``)."""

    def __init__(self, mux: "CAGMux", link_id: int, link_uuid: bytes,
                 trace_id: str, span_id: str):
        self.mux = mux
        self.link_id = link_id
        self.link_uuid = link_uuid
        self.trace_id = trace_id
        self.span_id = span_id
        self.redq_span_id = random_hex(8)
        self._rbuf = bytearray()
        self._closed = False
        self._close_err: Optional[BaseException] = None
        self._mu = threading.Lock()
        self._cond = threading.Condition(self._mu)
        self._read_timeout: Optional[float] = None

    # -- read (consumer side of readLoop) ---------------------------------
    def read(self, n: int = 65536) -> bytes:
        """Read up to *n* bytes; blocks until data or EOF/timeout (P8-009)."""
        with self._cond:
            while not self._rbuf:
                if self._closed:
                    if self._close_err is not None:
                        raise self._close_err
                    return b""  # EOF
                notified = self._cond.wait(self._read_timeout)
                if not notified and not self._rbuf and not self._closed:
                    raise TimeoutError("cag mux link %d read timeout" % self.link_id)
            out = bytes(self._rbuf[:n])
            del self._rbuf[:n]
            return out

    recv = read

    # -- write ------------------------------------------------------------
    def write(self, data: bytes) -> int:
        """Write *data*, chunked at ``CAG_PROXY_PAYLOAD_MAX`` (P8-007)."""
        data = bytes(data)
        written = 0
        p = memoryview(data)
        while len(p) > 0:
            chunk_len = min(len(p), CAG_PROXY_PAYLOAD_MAX)
            self.mux.write_frame(CAG_PROXY_DATA_CMD, self.link_id, bytes(p[:chunk_len]))
            written += chunk_len
            p = p[chunk_len:]
        return written

    sendall = write

    # -- close ------------------------------------------------------------
    def close(self) -> None:
        """Send a close-link frame and mark this link closed."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._cond.notify_all()
        try:
            self.mux.write_frame(CAG_PROXY_CLOSE_LINK_CMD, self.link_id, b"")
        except OSError:
            pass

    # -- deadlines --------------------------------------------------------
    def set_read_deadline(self, seconds: Optional[float]) -> None:
        """Set a read timeout (seconds, ``None`` = block forever) (P8-009)."""
        with self._cond:
            self._read_timeout = seconds
            self._cond.notify_all()

    def settimeout(self, seconds: Optional[float]) -> None:
        self.set_read_deadline(seconds)

    # -- internal: called by CAGMux.readLoop under mux lock ---------------
    def _append(self, payload: bytes) -> None:
        with self._cond:
            self._rbuf.extend(payload)
            self._cond.notify_all()

    def _mark_closed(self, err: Optional[BaseException] = None) -> None:
        with self._cond:
            self._closed = True
            self._close_err = err
            self._cond.notify_all()

    def fileno(self) -> int:
        return self.mux.conn.fileno()


class CAGMux:
    """CAG proxy multiplexer over one raw TLS socket (port of B's ``CAGMux``)."""

    def __init__(self, conn: socket.socket):
        self.conn = conn
        self._mu = threading.Lock()
        self._links: Dict[int, CAGMuxLink] = {}
        self._next_link_id = 1
        self._closed = False
        self._read_err: Optional[BaseException] = None
        self._reader: Optional[threading.Thread] = None

    # -- factory ----------------------------------------------------------
    @classmethod
    def open(cls, conn: socket.socket) -> "CAGMux":
        """Create a mux and start its background read loop."""
        mux = cls(conn)
        mux._start_read_loop()
        return mux

    def _start_read_loop(self) -> None:
        self._reader = threading.Thread(
            target=self._read_loop, name="cag-mux-readloop", daemon=True
        )
        self._reader.start()

    # -- read loop (producer) ---------------------------------------------
    def _read_loop(self) -> None:
        while True:
            try:
                cmd, link_id, payload = read_frame(self.conn)
            except (OSError, ConnectionError, TimeoutError) as exc:
                self._fail_all(exc)
                return
            except Exception as exc:  # defensive: malformed frame
                self._fail_all(exc)
                return

            if cmd == CAG_PROXY_CLOSE_LINK_CMD:
                with self._mu:
                    link = self._links.pop(link_id, None)
                if link is not None:
                    link._mark_closed()
            elif cmd == CAG_PROXY_DATA_CMD or (cmd & 0x0F) == CAG_PROXY_DATA_CMD:
                with self._mu:
                    link = self._links.get(link_id)
                if link is not None:
                    link._append(payload)
            # else: unknown command → discard (parity with B)

    def _fail_all(self, err: BaseException) -> None:
        with self._mu:
            self._read_err = err
            links = list(self._links.values())
            self._links.clear()
        for link in links:
            link._mark_closed(err)

    # -- open link --------------------------------------------------------
    def open_link(self, params, trace_id: str = "", span_id: str = "") -> CAGMuxLink:
        """Open a new inner link (sends the add-link packet) (P8-004/005)."""
        with self._mu:
            if self._closed:
                raise RuntimeError("cag mux closed")
            link_id = self._next_link_id
            self._next_link_id += 1
            link_uuid = new_zte_link_uuid()
            if not trace_id:
                trace_id = random_hex(16)
            if not span_id:
                span_id = random_hex(8)
            link = CAGMuxLink(self, link_id, link_uuid, trace_id, span_id)
            self._links[link_id] = link
        packet = build_cag_proxy_add_link_packet(
            params, link_id, link_uuid, trace_id, span_id
        )
        try:
            self.conn.sendall(packet)
        except OSError:
            with self._mu:
                self._links.pop(link_id, None)
            raise
        return link

    # -- write frame (locked) ---------------------------------------------
    def write_frame(self, cmd: int, link_id: int, payload: bytes = b"") -> None:
        with self._mu:
            if self._closed:
                raise RuntimeError("cag mux closed")
            frame = pack_frame(cmd, link_id, payload)
        self.conn.sendall(frame)

    # -- close ------------------------------------------------------------
    def close(self) -> None:
        """Close all links and the underlying socket."""
        with self._mu:
            if self._closed:
                return
            self._closed = True
            links = list(self._links.values())
            self._links.clear()
        for link in links:
            link._mark_closed()
            try:
                self.write_frame(CAG_PROXY_CLOSE_LINK_CMD, link.link_id, b"")
            except (OSError, RuntimeError):
                pass
        try:
            self.conn.close()
        except OSError:
            pass

    # -- introspection ----------------------------------------------------
    @property
    def closed(self) -> bool:
        with self._mu:
            return self._closed

    def link_count(self) -> int:
        with self._mu:
            return len(self._links)


def open_cag_mux_link(mux: CAGMux, params, trace_id: str = "",
                      span_id: str = "") -> CAGMuxLink:
    """Convenience: open one link on an existing :class:`CAGMux`."""
    return mux.open_link(params, trace_id=trace_id, span_id=span_id)

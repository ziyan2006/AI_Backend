from __future__ import annotations

import datetime
from threading import Lock
from typing import Any
from uuid import uuid4


class SessionTrace:
    def __init__(self, session_id: str, level_id: int, level_title: str) -> None:
        self.session_id = session_id
        self.level_id = level_id
        self.level_title = level_title
        self.created_at = datetime.datetime.now().strftime("%H:%M:%S")
        self.ended_at: str | None = None
        self.is_active = True
        self.logs: list[dict[str, Any]] = []
        self.turn_count = 0
        self.has_intercept = False
        self.has_fallback = False

    def add_log(self, trace_id: str, module: str, level: str, message: str) -> None:
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        if module == "PRE-CHECK" and "拦截" in message:
            self.has_intercept = True
        elif module == "FALLBACK" or "兜底" in message:
            self.has_fallback = True

        if module in ("ASR", "PRE-CHECK", "LLM-REQUEST") and not any(
            log.get("trace_id") == trace_id for log in self.logs
        ):
            self.turn_count += 1

        self.logs.append(
            {
                "timestamp": stamp,
                "trace_id": trace_id,
                "module": module,
                "level": level,
                "message": message,
            }
        )

    def close(self) -> None:
        self.is_active = False
        self.ended_at = datetime.datetime.now().strftime("%H:%M:%S")

    def to_summary(self) -> dict[str, Any]:
        status = "NORMAL"
        if self.has_fallback:
            status = "FALLBACK"
        elif self.has_intercept:
            status = "INTERCEPT"

        return {
            "session_id": self.session_id,
            "level_id": self.level_id,
            "level_title": self.level_title,
            "created_at": self.created_at,
            "ended_at": self.ended_at,
            "is_active": self.is_active,
            "turn_count": max(1, self.turn_count) if self.logs else 0,
            "status": status,
            "log_count": len(self.logs),
        }

    def to_detail(self) -> dict[str, Any]:
        summary = self.to_summary()
        summary["logs"] = self.logs
        return summary


class SessionLogStore:
    def __init__(self, max_history: int = 30) -> None:
        self._max_history = max_history
        self._sessions: dict[str, SessionTrace] = {}
        self._active_session_id: str | None = None
        self._lock = Lock()

    def create_session(
        self, level_id: int, level_title: str, session_id: str | None = None
    ) -> SessionTrace:
        with self._lock:
            if self._active_session_id and self._active_session_id in self._sessions:
                self._sessions[self._active_session_id].close()

            sid = session_id or f"sess_{int(datetime.datetime.now().timestamp())}_{uuid4().hex[:4]}"
            session = SessionTrace(session_id=sid, level_id=level_id, level_title=level_title)
            self._sessions[sid] = session
            self._active_session_id = sid

            if len(self._sessions) > self._max_history:
                oldest = next(iter(self._sessions))
                if oldest != self._active_session_id:
                    self._sessions.pop(oldest, None)

            return session

    def get_active_session(self) -> SessionTrace | None:
        with self._lock:
            if self._active_session_id:
                return self._sessions.get(self._active_session_id)
            return None

    def add_log(
        self,
        trace_id: str,
        module: str,
        level: str,
        message: str,
        session_id: str | None = None,
    ) -> None:
        with self._lock:
            target: SessionTrace | None = None
            if session_id and session_id in self._sessions:
                target = self._sessions[session_id]
            elif self._active_session_id and self._active_session_id in self._sessions:
                target = self._sessions[self._active_session_id]

            if target is not None:
                target.add_log(trace_id, module, level, message)

    def close_session(self, session_id: str | None = None) -> None:
        with self._lock:
            sid = session_id or self._active_session_id
            if sid and sid in self._sessions:
                self._sessions[sid].close()
                if self._active_session_id == sid:
                    self._active_session_id = None

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.to_summary() for s in reversed(list(self._sessions.values()))]

    def get_session_detail(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._sessions.get(session_id)
            return session.to_detail() if session else None


GLOBAL_SESSION_STORE = SessionLogStore()

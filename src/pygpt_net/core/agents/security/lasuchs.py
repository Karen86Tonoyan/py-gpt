#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# This file is a part of PYGPT package               #
# Website: https://pygpt.net                         #
# GitHub:  https://github.com/szczyglis-dev/py-gpt   #
# MIT License                                        #
# Created By  : Marcin Szczygliński                  #
# Updated Date: 2026.05.13 00:00:00                  #
# ================================================== #

import json
import os
import re
import threading
from collections import deque
from datetime import datetime
from typing import Deque, Dict, Any, List, Optional


# Event types emitted by Lasuchs
EVENT_INPUT = "agent_input"
EVENT_OUTPUT = "agent_output"
EVENT_TOOL_CALL = "tool_call"
EVENT_TOOL_RESULT = "tool_result"
EVENT_SECURITY_BLOCK = "security_block"
EVENT_AGENT_START = "agent_start"
EVENT_AGENT_STOP = "agent_stop"
EVENT_AGENT_ERROR = "agent_error"

# Anomaly detection thresholds
MAX_TOOL_CALLS_PER_STEP = 30
MAX_OUTPUT_LENGTH = 50_000
SUSPICIOUS_REPEAT_THRESHOLD = 5  # same tool called N+ times in one run


class Lasuchs:
    """
    Lasuchs — the listener/eavesdropper monitor.
    Records all agent pipeline events, detects anomalies (tool abuse,
    runaway loops, suspiciously large outputs), and maintains an audit log.
    """

    def __init__(self, window=None):
        self.window = window
        self._lock = threading.Lock()
        self._history: Deque[Dict[str, Any]] = deque(maxlen=500)
        self._tool_call_counts: Dict[str, int] = {}  # tool_name → count (reset per agent run)
        self._current_run_id: Optional[str] = None
        self._alerts: List[Dict[str, Any]] = []
        self._log_path: Optional[str] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, event_type: str, data: Dict[str, Any]):
        """
        Record an event from the agent pipeline.

        :param event_type: one of the EVENT_* constants
        :param data: event payload
        """
        entry = {
            "ts": datetime.utcnow().isoformat(),
            "event": event_type,
            **data,
        }
        with self._lock:
            self._history.append(entry)

        # Anomaly detection
        self._detect_anomalies(event_type, data)

        # Write to audit log
        self._write_log(entry)

    def start_run(self, agent_id: str, run_id: Optional[str] = None):
        """Signal the start of an agent run. Resets per-run counters."""
        import uuid
        self._current_run_id = run_id or str(uuid.uuid4())[:8]
        with self._lock:
            self._tool_call_counts.clear()
        self.record(EVENT_AGENT_START, {"agent_id": agent_id, "run_id": self._current_run_id})

    def end_run(self, agent_id: str, success: bool = True):
        """Signal the end of an agent run."""
        self.record(EVENT_AGENT_STOP, {
            "agent_id": agent_id,
            "run_id": self._current_run_id,
            "success": success,
        })

    def get_alerts(self) -> List[Dict[str, Any]]:
        """Return accumulated anomaly alerts."""
        with self._lock:
            return list(self._alerts)

    def clear_alerts(self):
        with self._lock:
            self._alerts.clear()

    def get_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._history)
        return items[-limit:]

    def set_log_path(self, path: str):
        """Set path for the audit JSONL log file."""
        self._log_path = path

    # ------------------------------------------------------------------
    # Anomaly detection
    # ------------------------------------------------------------------

    def _detect_anomalies(self, event_type: str, data: Dict[str, Any]):
        if event_type == EVENT_TOOL_CALL:
            tool_name = data.get("tool", "unknown")
            with self._lock:
                self._tool_call_counts[tool_name] = self._tool_call_counts.get(tool_name, 0) + 1
                count = self._tool_call_counts[tool_name]
            if count >= SUSPICIOUS_REPEAT_THRESHOLD:
                self._raise_alert("TOOL_LOOP", f"Tool '{tool_name}' called {count} times in one run")

        if event_type == EVENT_OUTPUT:
            output = data.get("content", "")
            if len(output) > MAX_OUTPUT_LENGTH:
                self._raise_alert("OVERSIZED_OUTPUT", f"Agent output exceeds {MAX_OUTPUT_LENGTH} chars ({len(output)})")

    def _raise_alert(self, alert_type: str, message: str):
        alert = {
            "ts": datetime.utcnow().isoformat(),
            "type": alert_type,
            "message": message,
            "run_id": self._current_run_id,
        }
        with self._lock:
            self._alerts.append(alert)
        msg = f"[Lasuchs] ALERT {alert_type}: {message}"
        if self.window:
            self.window.core.debug.log(Exception(msg))
        else:
            print(msg)

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    def _write_log(self, entry: Dict[str, Any]):
        """Append event to JSONL audit log file."""
        if not self._log_path:
            if self.window:
                try:
                    base = self.window.core.config.get_user_dir("logs")
                    self._log_path = os.path.join(base, "agent_audit.jsonl")
                except Exception:
                    return
            else:
                return
        try:
            os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # log write failures are non-fatal

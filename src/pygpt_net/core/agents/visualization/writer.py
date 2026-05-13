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
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional


# pixel-agents JSONL event types (matching Claude Code Hooks API format)
EVT_SESSION_START = "SessionStart"
EVT_SESSION_STOP = "SessionStop"
EVT_TOOL_START = "PreToolUse"
EVT_TOOL_RESULT = "PostToolUse"
EVT_ASSISTANT_MSG = "AssistantMessage"
EVT_PERMISSION = "PermissionRequest"
EVT_ERROR = "Error"


class PixelAgentsWriter:
    """
    Writes agent activity to a JSONL transcript file understood by pixel-agents.
    Each agent gets its own session file under ~/.pixel-agents/transcripts/.
    The pixel-agents VS Code extension polls these files and animates pixel-art
    characters that reflect what the agent is doing in real-time.
    """

    def __init__(self, window=None):
        self.window = window
        self._lock = threading.Lock()
        self._handles: Dict[str, Any] = {}  # session_id → open file handle
        self._base_dir: Optional[str] = None

    # ------------------------------------------------------------------
    # Public event API
    # ------------------------------------------------------------------

    def session_start(self, agent_id: str, session_id: str):
        """Write SessionStart event and open transcript file."""
        path = self._open_session(session_id)
        self._emit(session_id, EVT_SESSION_START, {
            "session_id": session_id,
            "agent_id": agent_id,
            "hook_event_name": EVT_SESSION_START,
        })

    def session_stop(self, agent_id: str, session_id: str):
        """Write SessionStop and close file."""
        self._emit(session_id, EVT_SESSION_STOP, {
            "session_id": session_id,
            "agent_id": agent_id,
            "hook_event_name": EVT_SESSION_STOP,
        })
        self._close_session(session_id)

    def tool_start(self, agent_id: str, tool_name: str, tool_input: dict):
        """Write PreToolUse event — pixel-agents shows the agent 'typing'."""
        session_id = self._session_for(agent_id)
        self._emit(session_id, EVT_TOOL_START, {
            "hook_event_name": EVT_TOOL_START,
            "tool_name": tool_name,
            "tool_input": tool_input,
        })

    def tool_result(self, agent_id: str, tool_name: str, output: str):
        """Write PostToolUse event — pixel-agents shows the agent 'reading'."""
        session_id = self._session_for(agent_id)
        self._emit(session_id, EVT_TOOL_RESULT, {
            "hook_event_name": EVT_TOOL_RESULT,
            "tool_name": tool_name,
            "tool_response": {"output": output[:2000]},  # cap size
        })

    def message(self, agent_id: str, role: str, content: str):
        """Write AssistantMessage event."""
        session_id = self._session_for(agent_id)
        self._emit(session_id, EVT_ASSISTANT_MSG, {
            "hook_event_name": EVT_ASSISTANT_MSG,
            "role": role,
            "message": content[:4000],
        })

    def permission_request(self, agent_id: str, tool_name: str, reason: str = ""):
        """Write PermissionRequest event — pixel-agents shows waiting bubble."""
        session_id = self._session_for(agent_id)
        self._emit(session_id, EVT_PERMISSION, {
            "hook_event_name": EVT_PERMISSION,
            "tool_name": tool_name,
            "reason": reason,
        })

    def error(self, agent_id: str, error_msg: str):
        """Write Error event."""
        session_id = self._session_for(agent_id)
        self._emit(session_id, EVT_ERROR, {
            "hook_event_name": EVT_ERROR,
            "error": error_msg[:500],
        })

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session_for(self, agent_id: str) -> str:
        """Map agent_id to a stable session_id (same run reuses same file)."""
        return f"pygpt_{agent_id}"

    def _get_base_dir(self) -> str:
        if self._base_dir:
            return self._base_dir
        # Prefer ~/.pixel-agents/transcripts (pixel-agents default)
        home = os.path.expanduser("~")
        base = os.path.join(home, ".pixel-agents", "transcripts")
        # Fallback: place inside pygpt logs dir
        if self.window:
            try:
                logs = self.window.core.config.get_user_dir("logs")
                base = os.path.join(logs, "pixel_agents")
            except Exception:
                pass
        self._base_dir = base
        return base

    def _transcript_path(self, session_id: str) -> str:
        base = self._get_base_dir()
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, f"{session_id}.jsonl")

    def _open_session(self, session_id: str) -> str:
        path = self._transcript_path(session_id)
        with self._lock:
            if session_id not in self._handles:
                try:
                    self._handles[session_id] = open(path, "a", encoding="utf-8", buffering=1)
                except Exception:
                    pass
        return path

    def _close_session(self, session_id: str):
        with self._lock:
            handle = self._handles.pop(session_id, None)
        if handle:
            try:
                handle.close()
            except Exception:
                pass

    def _emit(self, session_id: str, event_type: str, payload: dict):
        """Append a JSONL line to the session transcript."""
        # Ensure file is open
        if session_id not in self._handles:
            self._open_session(session_id)

        record = {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            handle = self._handles.get(session_id)
            if handle:
                try:
                    handle.write(line + "\n")
                    handle.flush()
                except Exception:
                    pass
            else:
                # Fallback: write directly
                try:
                    path = self._transcript_path(session_id)
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except Exception:
                    pass

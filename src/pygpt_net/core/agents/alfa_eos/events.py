#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# ALFA-EOS — Event Log                               #
# RFC v0.1 §6 — Event Sourcing                       #
# ================================================== #

"""
Append-only event log for the epistemic runtime.
All state mutations MUST produce a corresponding event before the mutation
is applied. Replaying the event log must reproduce the final state exactly
(INVARIANT_07).

15 canonical event types are defined here.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    CLAIM_CREATED                  = "CLAIM_CREATED"
    STATE_TRANSITIONED             = "STATE_TRANSITIONED"
    EVIDENCE_ADDED                 = "EVIDENCE_ADDED"
    EVIDENCE_INVALIDATED           = "EVIDENCE_INVALIDATED"
    EXECUTION_GRANTED              = "EXECUTION_GRANTED"
    EXECUTION_DENIED               = "EXECUTION_DENIED"
    EXECUTION_PERMISSION_EXPIRED   = "EXECUTION_PERMISSION_EXPIRED"   # Fix: missing expiry event
    CONFLICT_DETECTED              = "CONFLICT_DETECTED"
    ARBITRATION_STARTED            = "ARBITRATION_STARTED"
    ARBITRATION_RESOLVED           = "ARBITRATION_RESOLVED"
    ARBITRATION_TIMEOUT            = "ARBITRATION_TIMEOUT"
    DRIFT_DETECTED                 = "DRIFT_DETECTED"
    SNAPSHOT_CREATED               = "SNAPSHOT_CREATED"
    INVARIANT_VIOLATED             = "INVARIANT_VIOLATED"
    DEPENDENCY_ADDED               = "DEPENDENCY_ADDED"
    POLICY_UPDATED                 = "POLICY_UPDATED"


# ---------------------------------------------------------------------------
# Event dataclass
# ---------------------------------------------------------------------------

@dataclass
class Event:
    event_id: str
    event_type: EventType
    claim_id: Optional[str]
    agent_id: str
    payload: Dict[str, Any]
    schema_version: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "claim_id": self.claim_id,
            "agent_id": self.agent_id,
            "payload": self.payload,
            "schema_version": self.schema_version,
            "timestamp": self.timestamp.isoformat(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @staticmethod
    def new(
        event_type: EventType,
        agent_id: str,
        payload: Dict[str, Any],
        schema_version: str = "1.0",
        claim_id: Optional[str] = None,
    ) -> "Event":
        return Event(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            claim_id=claim_id,
            agent_id=agent_id,
            payload=payload,
            schema_version=schema_version,
        )


# ---------------------------------------------------------------------------
# In-memory append-only log
# ---------------------------------------------------------------------------

class EventLog:
    """
    Thread-unsafe in-memory append-only event log.
    For production use, replace with a durable backend (PostgreSQL, EventStoreDB).
    The log is the single source of truth — no event may be modified or deleted.
    """

    def __init__(self, schema_version: str = "1.0") -> None:
        self._log: List[Event] = []
        self.schema_version = schema_version

    def append(self, event: Event) -> Event:
        """Append an event. Returns the event for chaining."""
        self._log.append(event)
        return event

    def emit(
        self,
        event_type: EventType,
        agent_id: str,
        payload: Dict[str, Any],
        claim_id: Optional[str] = None,
    ) -> Event:
        """Convenience: create and append in one call."""
        event = Event.new(
            event_type=event_type,
            agent_id=agent_id,
            payload=payload,
            schema_version=self.schema_version,
            claim_id=claim_id,
        )
        return self.append(event)

    def get_events_for_claim(self, claim_id: str) -> List[Event]:
        return [e for e in self._log if e.claim_id == claim_id]

    def get_events_by_type(self, event_type: EventType) -> List[Event]:
        return [e for e in self._log if e.event_type == event_type]

    def all(self) -> List[Event]:
        return list(self._log)

    def count(self) -> int:
        return len(self._log)

    def to_jsonl(self) -> str:
        return "\n".join(e.to_json() for e in self._log)

    @classmethod
    def from_jsonl(cls, jsonl: str, schema_version: str = "1.0") -> "EventLog":
        log = cls(schema_version=schema_version)
        for line in jsonl.strip().splitlines():
            if not line:
                continue
            d = json.loads(line)
            log.append(Event(
                event_id=d["event_id"],
                event_type=EventType(d["event_type"]),
                claim_id=d.get("claim_id"),
                agent_id=d["agent_id"],
                payload=d["payload"],
                schema_version=d["schema_version"],
                timestamp=datetime.fromisoformat(d["timestamp"]),
            ))
        return log

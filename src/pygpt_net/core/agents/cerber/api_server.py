"""
CERBER API Server (FastAPI)
REST API for the Cerber security enforcement system.

P0 security fixes applied:
  - CORS restricted to CERBER_ALLOWED_ORIGINS (no wildcard)
  - /admin/* and /redteam/* require X-Admin-Token header
  - /train uses asyncio.Semaphore to cap concurrent background jobs

Version: 1.0.1
"""

import asyncio
import os
import secrets
import sys
import io
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, BackgroundTasks, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Environment variable {name!r} is not set. "
            "Cerber API refuses to start without explicit configuration."
        )
    return value


def _allowed_origins() -> list[str]:
    raw = os.environ.get("CERBER_ALLOWED_ORIGINS", "").strip()
    if not raw:
        raise RuntimeError(
            "CERBER_ALLOWED_ORIGINS is not set. "
            "Set it to a comma-separated list of allowed origins "
            "(e.g. 'http://localhost:3000,https://app.example.com')."
        )
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if "*" in origins:
        raise RuntimeError(
            "CERBER_ALLOWED_ORIGINS must not contain '*'. "
            "Provide explicit origin URLs."
        )
    return origins


ADMIN_TOKEN: str = _required_env("CERBER_ADMIN_TOKEN")
ALLOWED_ORIGINS: list[str] = _allowed_origins()

# Maximum concurrent /train background jobs
_TRAIN_SEMAPHORE = asyncio.Semaphore(2)

# ---------------------------------------------------------------------------
# Import Cerber modules (relative within package)
# ---------------------------------------------------------------------------

from .auto_guardian import AutoGuardian  # noqa: E402 — after sys.path fix
from .runtime_monitor import RuntimeMonitor  # noqa: E402
from .attack_library_advanced import (  # noqa: E402
    ArtPromptGenerator,
    BijectionLearningGenerator,
    ManyShotGenerator,
    HomoglyphGenerator,
    EmojiSmugglingGenerator,
    HexBase64Generator,
)
from .dataset_generator import CerberDatasetGenerator  # noqa: E402


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

_admin_key_header = APIKeyHeader(name="X-Admin-Token", auto_error=False)


def _require_admin_token(token: Optional[str] = Security(_admin_key_header)) -> None:
    if not token or not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Admin-Token header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    user_id: str = Field(default="anonymous")
    session_id: str = Field(default="default")


class ScanResponse(BaseModel):
    action: str
    lockdown: bool
    session_risk_score: float = 0.0
    triggers_found: List[Dict] = []
    severity: str = "NONE"
    explanation: str
    timestamp: str


class StatusResponse(BaseModel):
    system_status: str
    kill_switch_active: bool
    total_sessions: int
    locked_sessions: int
    average_risk_score: float
    uptime_seconds: float
    version: str = "1.0.1"


class TrainingRequest(BaseModel):
    malicious_per_rule: int = Field(default=5, ge=1, le=20)
    benign_count: int = Field(default=100, ge=10, le=500)
    composite_count: int = Field(default=30, ge=5, le=100)
    format_type: str = Field(default="anthropic", pattern="^(anthropic|openai)$")


class AttackGenerationRequest(BaseModel):
    attack_type: str = Field(
        ..., pattern="^(artprompt|bijection|manyshot|homoglyph|emoji|encoding)$"
    )
    payload: str = Field(..., min_length=1)
    parameters: Optional[Dict] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CERBER Security API",
    description=(
        "LLM security enforcement system — 60 canonical rules, fail-closed (RULE-058). "
        "Admin and red-team endpoints require X-Admin-Token."
    ),
    version="1.0.1",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Admin-Token"],
)

guardian = AutoGuardian(enable_ollama_mixing=False, log_file="cerber_api_audit.jsonl")
runtime_monitor = RuntimeMonitor(audit_log_path="cerber_runtime_audit.jsonl")
_server_start = datetime.now()


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------

@app.get("/", tags=["General"])
async def root():
    return {
        "service": "CERBER Security API",
        "version": "1.0.1",
        "status": "lockdown" if runtime_monitor.kill_switch_active else "operational",
    }


@app.post("/scan", response_model=ScanResponse, tags=["Security"])
async def scan_prompt(request: ScanRequest):
    try:
        result = guardian.scan_and_decide(prompt=request.prompt, user_id=request.user_id)

        if result["scan_result"]["detected"]:
            for trigger in result["scan_result"]["triggers_found"]:
                monitor_result = runtime_monitor.track_event(
                    session_id=request.session_id,
                    event_type=trigger.get("category", "unknown_threat"),
                    severity=result["scan_result"]["max_severity"],
                    details={"trigger": trigger, "user_id": request.user_id},
                )
                if monitor_result.get("kill_switch_triggered"):
                    result["lockdown"] = True
                    result["action"] = "block"

        session_report = runtime_monitor.get_session_report(request.session_id)
        session_risk = session_report["total_risk_score"] if session_report else 0.0

        return ScanResponse(
            action=result["action"].upper(),
            lockdown=result["lockdown"],
            session_risk_score=session_risk,
            triggers_found=result["scan_result"].get("triggers_found", []),
            severity=result["scan_result"].get("max_severity", "NONE"),
            explanation=result.get("response") or "Prompt analysis complete",
            timestamp=datetime.now().isoformat(),
        )

    except Exception as exc:
        runtime_monitor.trigger_kill_switch("API_EXCEPTION")
        raise HTTPException(status_code=500, detail=f"CERBER LOCKDOWN: {exc}") from exc


@app.get("/status", response_model=StatusResponse, tags=["Monitoring"])
async def get_status():
    sys_status = runtime_monitor.get_system_status()
    uptime = (datetime.now() - _server_start).total_seconds()
    return StatusResponse(
        system_status="LOCKDOWN" if sys_status["kill_switch_active"] else "OPERATIONAL",
        kill_switch_active=sys_status["kill_switch_active"],
        total_sessions=sys_status["total_sessions"],
        locked_sessions=sys_status["locked_sessions"],
        average_risk_score=sys_status["average_risk_score"],
        uptime_seconds=uptime,
    )


@app.get("/stats", tags=["Monitoring"])
async def get_statistics():
    return {
        "guardian": guardian.get_statistics(),
        "runtime_monitor": runtime_monitor.get_system_status(),
        "uptime_seconds": (datetime.now() - _server_start).total_seconds(),
    }


@app.get("/session/{session_id}", tags=["Monitoring"])
async def get_session_report(session_id: str):
    report = runtime_monitor.get_session_report(session_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return report


# ---------------------------------------------------------------------------
# Admin endpoints — require X-Admin-Token
# ---------------------------------------------------------------------------

@app.post("/train", tags=["Admin"], dependencies=[Depends(_require_admin_token)])
async def generate_training_data(request: TrainingRequest, background_tasks: BackgroundTasks):
    """Generate training data (async background task, max 2 concurrent)."""

    async def _generate() -> None:
        async with _TRAIN_SEMAPHORE:
            generator = CerberDatasetGenerator()
            generator.generate_full_dataset(
                malicious_per_rule=request.malicious_per_rule,
                benign_count=request.benign_count,
                composite_count=request.composite_count,
            )
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            generator.export_jsonl(f"cerber_training_{ts}.jsonl", format_type=request.format_type)
            generator.export_statistics(f"cerber_stats_{ts}.json")

    background_tasks.add_task(_generate)
    return {"status": "training job queued", "parameters": request.model_dump()}


@app.post("/redteam/generate", tags=["Admin"], dependencies=[Depends(_require_admin_token)])
async def generate_attack(request: AttackGenerationRequest):
    """Generate attack payload for authorized red-team testing."""
    generators = {
        "artprompt": lambda: ArtPromptGenerator.generate_attack(
            request.payload, framing=request.parameters.get("framing", "educational")
        ),
        "bijection": lambda: BijectionLearningGenerator.generate_attack(
            request.payload, cipher_type=request.parameters.get("cipher_type", "symbol")
        ),
        "manyshot": lambda: ManyShotGenerator.generate_attack(
            request.payload, shots=request.parameters.get("shots", 50)
        ),
        "homoglyph": lambda: HomoglyphGenerator.generate_attack(
            request.payload, intensity=request.parameters.get("intensity", 0.7)
        ),
        "emoji": lambda: EmojiSmugglingGenerator.generate_attack(request.payload),
        "encoding": lambda: HexBase64Generator.generate_attack(
            request.payload, encoding=request.parameters.get("encoding", "layered")
        ),
    }
    try:
        attack = generators[request.attack_type]()
        return {"attack_type": request.attack_type, "generated_attack": attack, "length": len(attack)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/admin/reset-session/{session_id}", tags=["Admin"], dependencies=[Depends(_require_admin_token)])
async def reset_session(session_id: str):
    if session_id not in runtime_monitor.sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    del runtime_monitor.sessions[session_id]
    return {"status": "session reset", "session_id": session_id}


@app.post("/admin/kill-switch/activate", tags=["Admin"], dependencies=[Depends(_require_admin_token)])
async def activate_kill_switch(reason: str = "MANUAL_ADMIN_TRIGGER"):
    """Activate global kill-switch (EMERGENCY — irreversible without restart)."""
    runtime_monitor.trigger_kill_switch(reason)
    return {
        "status": "KILL-SWITCH ACTIVATED",
        "reason": reason,
        "timestamp": datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info", access_log=True)


if __name__ == "__main__":
    main()

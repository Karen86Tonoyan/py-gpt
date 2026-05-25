"""
Cerber / ALFA Decision Precedence — authority order.

This module documents (and will eventually enforce at runtime) the precedence
order between the two security layers in the ALFA system.

LAYER 1 — Cerber (deterministic enforcement)
  Authority: ABSOLUTE on the following threat categories:
    - jailbreak / prompt-injection (RULE-026, zero-exception)
    - malware / CBRN / illegal content generation
    - hard crisis (RULE-064, CrisisHardCut runs BEFORE all pipeline)
    - auth / risk escalation (kill-switch, session lockdown)

  Decision: BLOCK or LOCKDOWN — not overridable by any downstream component.
  LLM is NOT consulted. Policy engine decides.

LAYER 2 — ALFA Complete System (cognitive / support layer)
  Authority: ADVISORY on the following threat categories:
    - manipulation awareness (Cialdini, social engineering)
    - boundary reminders (user-declared limits)
    - contextual soft interventions (tone, framing)
    - ambiguous requests that do not match Layer 1 triggers

  Decision: inject structured SYSTEM ALERT into LLM context.
  LLM responds intelligently ("I notice authority framing, but I can't…").
  LLM MAY soften, explain, or decline — it cannot override a Layer 1 BLOCK.

PRECEDENCE RULE (invariant):
  IF Cerber returns action=BLOCK or lockdown=True:
    → Response is the Cerber lockdown message. Pipeline terminates.
    → ALFA layer is NOT invoked.

  IF Cerber returns action=WARN:
    → ALFA layer IS invoked with manipulation_alert context.
    → LLM decides how to respond within the non-blocked channel.

  IF Cerber returns action=ALLOW:
    → ALFA layer IS invoked normally (no forced context injection).

GuardianAdapter (setup.py / guardian_adapter.py) implements this at runtime:
  - SHADOW mode: Cerber evaluates, ALFA proceeds unmodified (calibration)
  - PARTIAL mode: Cerber blocks HIGH/CRITICAL; ALFA handles LOW/MEDIUM
  - FULL mode: full matrix, both layers active per rules above

SHADOW mode is the default rollout mode. Do not enable PARTIAL or FULL
until Cerber detection_rate >= 0.92 and false_positive_rate <= 0.03
(verified by EvalHarness).
"""

from enum import Enum


class DecisionAuthority(str, Enum):
    CERBER = "cerber"      # deterministic, final
    ALFA = "alfa"          # advisory, LLM-mediated
    BOTH = "both"          # cerber gates first, alfa advises if allowed


THREAT_AUTHORITY_MAP: dict[str, DecisionAuthority] = {
    # Layer 1 — Cerber absolute authority
    "jailbreak": DecisionAuthority.CERBER,
    "prompt_injection": DecisionAuthority.CERBER,
    "malware": DecisionAuthority.CERBER,
    "cbrn": DecisionAuthority.CERBER,
    "illegal_content": DecisionAuthority.CERBER,
    "crisis_hard": DecisionAuthority.CERBER,
    "auth_escalation": DecisionAuthority.CERBER,
    "kill_switch": DecisionAuthority.CERBER,
    # Layer 2 — ALFA advisory
    "manipulation": DecisionAuthority.ALFA,
    "boundary_reminder": DecisionAuthority.ALFA,
    "soft_intervention": DecisionAuthority.ALFA,
    # Both layers — cerber gates, alfa advises if not blocked
    "social_engineering": DecisionAuthority.BOTH,
    "authority_claim": DecisionAuthority.BOTH,
}


def get_authority(threat_category: str) -> DecisionAuthority:
    """Return the decision authority for a given threat category."""
    return THREAT_AUTHORITY_MAP.get(threat_category, DecisionAuthority.CERBER)

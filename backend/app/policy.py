"""Серверні запобіжники над рішеннями AI. Чисті функції без БД і LLM."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas import ModerationDecision

SEVERE = {"hate", "threat", "doxxing", "sexual"}
ACT_MIN_CONFIDENCE = 0.5
BAN_MIN_CONFIDENCE = 0.8


def guard_moderation(d: ModerationDecision) -> ModerationDecision:
    if d.decision == "acquit" or d.rule == "none" or d.confidence < ACT_MIN_CONFIDENCE:
        return d.model_copy(update={"decision": "acquit", "duration_min": 0})
    if d.decision == "ban" and d.confidence < BAN_MIN_CONFIDENCE:
        return d.model_copy(update={"decision": "timeout", "duration_min": 10})
    if d.decision == "timeout":
        return d.model_copy(update={"duration_min": min(max(d.duration_min or 5, 1), 60)})
    return d


def guard_triage(category: str, incident_rule: str) -> str:
    # тяжкі порушення ніколи не йдуть у шоу, лише в підтримку
    return "disputed" if category == "interesting" and incident_rule in SEVERE else category

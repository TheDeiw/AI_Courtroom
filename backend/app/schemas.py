"""Pydantic v2 schemas for agent responses (ARCHITECTURE.md, section 8.2).

These are the frozen contracts: the LLM is forced to answer in exactly these shapes.
"""

from typing import Literal

from pydantic import BaseModel, Field

Rule = Literal["insult", "hate", "threat", "harassment", "spam", "ads",
               "spoiler", "sexual", "doxxing", "manipulation", "other", "none"]


class PrefilterFlag(BaseModel):
    id: int
    reason: str                       # 3-6 слів


class PrefilterResult(BaseModel):
    flagged: list[PrefilterFlag]      # лише підозрілі


class ModerationDecision(BaseModel):
    decision: Literal["acquit", "timeout", "ban"]
    rule: Rule
    duration_min: int = 0             # лише для timeout
    confidence: float = Field(ge=0, le=1)
    reason: str                       # одне речення для юзера
    user_summary: str                 # 1-2 речення про поведінку юзера


class TriageResult(BaseModel):
    category: Literal["boring", "disputed", "interesting"]
    interest_score: int = Field(ge=0, le=10)   # цінність для шоу
    sincerity: int = Field(ge=0, le=10)
    reason: str
    summary: str                               # суть апеляції одним реченням


class Pleading(BaseModel):            # спільна схема для адвоката й прокурора
    arguments: list[str]              # 2-3 аргументи
    strongest_point: str


class Verdict(BaseModel):
    decision: Literal["uphold", "overturn"]
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    key_factors: list[str]
    precedent_ids: list[int]


class DialogueLine(BaseModel):
    speaker: Literal["clerk", "prosecutor", "defense", "judge"]
    emotion: Literal["neutral", "angry", "ironic", "solemn"]
    text: str


class Dialogue(BaseModel):
    lines: list[DialogueLine]

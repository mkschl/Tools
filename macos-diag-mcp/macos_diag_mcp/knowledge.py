"""The domain knowledge from CLAUDE.md, loaded from knowledge.toml.

Kept as data rather than prose in a docstring so a finished investigation can
correct a signal or add a case without a code change.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

KNOWLEDGE_PATH = Path(__file__).with_name("knowledge.toml")


@dataclass(frozen=True)
class Step:
    number: int
    title: str
    detail: str
    tools: tuple[str, ...]


@dataclass(frozen=True)
class Signal:
    pattern: str
    process: str
    meaning: str


@dataclass(frozen=True)
class Case:
    name: str
    date: str
    symptom: str
    cause: str
    recovery: str
    prevention: str


@dataclass(frozen=True)
class Knowledge:
    summary: str
    safety_rules: tuple[str, ...]
    steps: tuple[Step, ...]
    signals: tuple[Signal, ...]
    cases: tuple[Case, ...]
    lessons: tuple[str, ...]


def load_knowledge(path: Path = KNOWLEDGE_PATH) -> Knowledge:
    raw = tomllib.loads(path.read_text())
    playbook = raw["playbook"]
    return Knowledge(
        summary=playbook["summary"].strip(),
        safety_rules=tuple(playbook["safety_rules"]),
        steps=tuple(
            Step(
                number=int(s["number"]),
                title=s["title"],
                detail=s["detail"].strip(),
                tools=tuple(s["tools"]),
            )
            for s in playbook["steps"]
        ),
        signals=tuple(
            Signal(pattern=s["pattern"], process=s["process"], meaning=s["meaning"])
            for s in raw["signals"]
        ),
        cases=tuple(
            Case(
                name=c["name"],
                date=c["date"],
                symptom=c["symptom"],
                cause=c["cause"],
                recovery=c["recovery"],
                prevention=c["prevention"],
            )
            for c in raw["cases"]
        ),
        lessons=tuple(playbook["lessons"]),
    )


def _tokens(text: str) -> list[str]:
    """Words worth matching on: short ones match everything and mean nothing."""
    return [
        t
        for t in "".join(c if c.isalnum() else " " for c in text).split()
        if len(t) > 3
    ]


def match_signals(signals: tuple[Signal, ...], query: str) -> list[Signal]:
    """Signals relevant to a log line, best first.

    A log line is never quite the table's wording — it carries a pid, a code and
    a subsystem — so this scores shared words rather than requiring a substring.
    """
    needle = query.casefold()
    if not needle.strip():
        return list(signals)

    scored: list[tuple[int, int, Signal]] = []
    for index, signal in enumerate(signals):
        pattern = signal.pattern.casefold()
        if pattern in needle or needle in pattern:
            score = 1000
        else:
            words = set(_tokens(pattern))
            score = len(words & set(_tokens(needle)))
            if signal.process.casefold() in needle:
                score += 1
        if score:
            scored.append((score, -index, signal))

    scored.sort(reverse=True)
    return [signal for _, _, signal in scored]


def match_cases(cases: tuple[Case, ...], query: str) -> list[Case]:
    """Cases whose text mentions the query. Empty query returns all of them."""
    needle = query.casefold().strip()
    if not needle:
        return list(cases)
    return [
        case
        for case in cases
        if needle
        in " ".join(
            (case.name, case.symptom, case.cause, case.recovery, case.prevention)
        ).casefold()
    ]

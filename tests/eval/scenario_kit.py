"""Script builder shared by the extra replay scenarios (``scenarios_extra``).

``scenario.build_scenario`` (the original 30-turn session) keeps its own closures and is not touched; the
extra scenarios are written against this kit so each one reads as a plain script: turns, tool rounds, gold
facts and the error lines of the logs it prints. The data classes (``Turn``, ``Round``, ``ToolStep``,
``GoldFact``, ``Scenario``) are the original ones, so ``replay.run_session`` treats every scenario alike.

A scenario builder is ``build(seed, turns) -> Scenario`` (``turns``: user turns including the final question;
``turns`` below the natural length cuts the script like the original does: the first ``turns - 1`` scripted
turns, then the final question; gold facts and error lines that first appear after the cut are dropped).
Randomness comes only from ``Kit.rng(tag)``: ``random.Random`` seeded with a string, which does not depend on
``PYTHONHASHSEED``.
"""
from __future__ import annotations

import json
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:  # ``fixtures`` lives next to ``eval``
    sys.path.insert(0, str(_TESTS_DIR))

from scenario import CATEGORIES, GoldFact, Round, Scenario, ToolStep, Turn  # noqa: E402

SOURCES = ("user", "assistant", "tool")
MIN_TURNS = 4


@dataclass(frozen=True)
class ScenarioSpec:
    """A named scenario: how to build it and how long it naturally is."""

    name: str
    build: Callable[[int, int], Scenario]  # (seed, turns) -> Scenario
    turns: int  # natural length: scripted turns + the final question
    min_turns: int
    summary: str  # one line: what shape of session this is


def call_key(name: str, args: dict, nth: int | None = None) -> str:
    """Stable identity of a tool call (re-fetch accounting). ``nth`` tells apart repeats of one command whose
    output differs (a growing log tailed again): ``terminal:<command>#2``."""
    target = args.get("command") or args.get("pattern") or args.get("path") or json.dumps(args, sort_keys=True)
    return f"{name}:{target}" + (f"#{nth}" if nth else "")


class Kit:
    """Accumulates turns, gold facts and error lines for one scenario build."""

    def __init__(self, name: str, seed: int) -> None:
        self.name = name
        self.seed = seed
        self._turns: list[Turn] = []
        self._gold: list[GoldFact] = []
        self._errors: list[tuple[int, str]] = []  # (turn that printed the line, the line)

    # -- randomness ------------------------------------------------------------------------------
    def rng(self, tag: str) -> random.Random:
        """An independent, seed-stable stream: every payload gets its own, so editing one leaves the rest alone."""
        return random.Random(f"{self.seed}:{self.name}:{tag}")

    # -- script ----------------------------------------------------------------------------------
    @staticmethod
    def step(name: str, args: dict, result: str, *, nth: int | None = None, key: str | None = None) -> ToolStep:
        return ToolStep(name, args, result, key or call_key(name, args, nth))

    @staticmethod
    def rnd(say: str, *steps: ToolStep) -> Round:
        return Round(say, tuple(steps))

    def turn(self, user: str, rounds: list[Round], final: str) -> int:
        """Append a user turn; returns its 1-based number (what ``fact(turn=...)`` and ``errors`` take)."""
        self._turns.append(Turn(user, tuple(rounds), final))
        return len(self._turns)

    def fact(
        self, fid: str, category: str, needle: str, turn: int, source: str, needed: bool, call_key: str | None = None,
    ) -> None:
        if category not in CATEGORIES:
            raise ValueError(f"gold {fid}: unknown category {category!r}")
        if source not in SOURCES:
            raise ValueError(f"gold {fid}: unknown source {source!r}")
        if source == "tool" and not call_key:
            raise ValueError(f"gold {fid}: a tool fact names the call that printed it")
        if any(g.id == fid for g in self._gold):
            raise ValueError(f"duplicate gold id {fid}")
        self._gold.append(GoldFact(fid, category, needle, turn, source, needed, call_key))

    def errors(self, turn: int, lines: list[str]) -> None:
        """Error lines (level tagged, from ``payloads_extra``) that the output of ``turn`` shows the model."""
        self._errors.extend((turn, line) for line in lines)

    # -- result ----------------------------------------------------------------------------------
    def finish(self, final_question: str, turns: int, *, natural: int, log_turn: int) -> Scenario:
        """The ``Scenario`` for ``turns`` user turns (``natural`` is the full script length)."""
        if not MIN_TURNS <= turns <= natural:
            raise ValueError(f"turns must be between {MIN_TURNS} and {natural}")
        if len(self._turns) != natural - 1:
            raise AssertionError(f"{self.name}: script has {len(self._turns)} turns, expected {natural - 1}")
        keep = max(1, turns - 1)
        kept = self._turns[:keep]
        gold = [g for g in self._gold if g.turn <= keep]
        for g in gold:  # scenario drift guard: every needle really is where the fact says it is
            t = self._turns[g.turn - 1]
            if g.source == "user":
                hays = [t.user]
            elif g.source == "assistant":
                hays = [t.final, *(r.say for r in t.rounds)]
                hays += [json.dumps(s.args) for r in t.rounds for s in r.steps]
                hays += [json.dumps(s.args, ensure_ascii=False) for r in t.rounds for s in r.steps]
            else:
                hays = [s.result for r in t.rounds for s in r.steps if s.key == g.call_key]
            escaped = json.dumps(g.needle)[1:-1]
            assert any(g.needle in h or escaped in h for h in hays), (
                f"{self.name}: gold {g.id} needle {g.needle!r} is not in turn {g.turn} ({g.source})"
            )
        scenario = Scenario(
            turns=kept,
            gold=gold,
            error_lines=[line for turn, line in self._errors if turn <= keep],
            log_turn=min(log_turn, keep),
            final_question=final_question,
        )
        scenario.facts_by_id = {g.id: g for g in gold}
        return scenario

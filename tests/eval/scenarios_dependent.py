"""Dependent-use replay shape: the step right after a large output needs a detail from it.

The cost story of shortening a large output early (1.10 idea A, "frontier shortening") is that the model may still need the output a
moment later. Every other shape asks for facts only at the end. Here each of the 25 scripted turns prints a large output (16-30K
characters) that carries one planted detail (``dep_<n>``), and the next three requests of the same turn (small follow-up commands whose
arguments never repeat the detail) are the ones that would want it. ``protocol3.dependent_missing`` counts, per session, the requests
among those three in which the detail is no longer visible. A product that never shortens within three requests scores 0.

Six early details are also asked for at the end (ordinary ``needed`` facts), so the shape keeps a final question like the others.
Nothing registers at import time; ``register_dependent`` adds it to the scenario registry and ``unregister_dependent`` takes it out.
"""
from __future__ import annotations

from fixtures import hermes_payloads as hp
from scenario import Scenario
from scenario_kit import Kit, ScenarioSpec

DEPENDENT_TURNS = 26
_FOLLOW_UPS = ("echo continuing", "echo applying the change", "echo checking the result")
_SHARDS = ("alder", "birch", "cedar", "elm", "fir", "hazel", "larch", "maple", "oak", "pine")


def _line(r, i: int) -> str:
    return r.choice((
        f"2026-03-{1 + i % 27:02d}T{r.randrange(24):02d}:{r.randrange(60):02d}:{r.randrange(60):02d}Z INFO shard={r.choice(_SHARDS)}-{r.randrange(40)} "
        f"rebalance moved={r.randrange(10_000)} rows in {r.randrange(900)}ms queue={r.randrange(300)}",
        f"2026-03-{1 + i % 27:02d}T{r.randrange(24):02d}:{r.randrange(60):02d}:{r.randrange(60):02d}Z DEBUG worker-{r.randrange(64)} lease renewed "
        f"epoch={r.randrange(10 ** 6)} lag={r.randrange(500)}ms",
        f"2026-03-{1 + i % 27:02d}T{r.randrange(24):02d}:{r.randrange(60):02d}:{r.randrange(60):02d}Z INFO compaction segment={r.randrange(10 ** 5)} "
        f"bytes={r.randrange(10 ** 8)} ratio=0.{r.randrange(100):02d}",
    ))


def build_dependent(seed: int = 0, turns: int = DEPENDENT_TURNS, *, name: str = "dependent") -> Scenario:
    kit = Kit(name, seed)
    natural = DEPENDENT_TURNS
    needed_turns = (3, 7, 11, 15, 19, 23)
    details: dict[int, str] = {}
    cmds: dict[int, str] = {}
    for i in range(1, natural):
        r = kit.rng(f"t{i}")
        size = r.randint(16_000, 30_000)
        detail = f"cfg-{_SHARDS[i % len(_SHARDS)]}-{r.randrange(16 ** 4):04x}-{i:02d}"
        details[i] = detail
        cmds[i] = f"./collect --shard {_SHARDS[i % len(_SHARDS)]} --window {i}"
        lines: list[str] = []
        total = 0
        while total < size:
            line = _line(r, i)
            lines.append(line)
            total += len(line) + 1
        lines.insert(len(lines) // 2, f"NOTE active primary for window {i} is {detail}")
        text = "\n".join(lines)
        rounds = [kit.rnd(f"Running: {cmds[i]}", kit.step("terminal", {"command": cmds[i]}, hp.terminal_result(text)))]
        for k, follow in enumerate(_FOLLOW_UPS):
            rounds.append(kit.rnd(f"Next, using what the output reported. Step {k + 1}.",
                                  kit.step("terminal", {"command": f"{follow} {i}.{k}"}, hp.terminal_result(f"{follow} {i}.{k}"))))
        kit.turn(f"Step {i}: collect window {i} and then apply what it reports.", rounds, f"Window {i} handled.")
    for i in range(1, natural):
        key = f"terminal:{cmds[i]}"
        kit.fact(f"dep_{i}", "recall", details[i], i, "tool", False, call_key=key)
        if i in needed_turns:
            kit.fact(f"need_{i}", "recall", details[i], i, "tool", True, call_key=key)
    final = "List the primary reported for windows 3, 7, 11, 15, 19 and 23."
    return kit.finish(final, turns, natural=natural, log_turn=natural - 1)


DEPENDENT_SPECS = (
    ScenarioSpec("dependent", build_dependent, DEPENDENT_TURNS, 4,
                 "each large output is needed again by the next three requests (dependent use)"),
)


def register_dependent() -> None:
    import scenario_registry

    for spec in DEPENDENT_SPECS:
        if spec.name not in scenario_registry.SCENARIOS:
            scenario_registry.register_scenario(spec)


def unregister_dependent() -> None:
    import scenario_registry

    for spec in DEPENDENT_SPECS:
        scenario_registry.SCENARIOS.pop(spec.name, None)

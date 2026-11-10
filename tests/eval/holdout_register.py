"""A committed register of looks at the holdout scenarios: one per product hash, and a second look must say why.

Tuning against a set you can look at as often as you like turns it into a development set. The register keeps that honest in a cheap
way: ``record_look`` writes ``{product hash, date, phase, scenarios, note}`` to ``holdout_looks.json`` (committed, append-only) and refuses to
record a second look at the same product hash unless a reason is given, in which case the reason is stored. Editing the product changes
its hash, so a changed product is a new look by construction; the point is to make "I looked again at the same code" visible.

The product hash is a SHA-256 over the plugin's own source files (``hermes_tameru_plugin/**/*.py``, line endings normalised), so it does not
change with checkout line endings.

    python tests/eval/holdout_register.py hash
    python tests/eval/holdout_register.py record PHASE SCENARIO[,SCENARIO...] [--note TEXT] [--again REASON]
    python tests/eval/holdout_register.py list
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTER = Path(__file__).with_name("holdout_looks.json")
HOLDOUTS = ("longform", "mixedlang", "diagnostics", "rereads", "dense", "bursty")


def product_hash(root: Path = ROOT) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / "hermes_tameru_plugin").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def looks(register: Path = REGISTER) -> list[dict]:
    if not register.exists():
        return []
    data = json.loads(register.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("holdout_looks.json must be a list")
    return data


def record_look(phase: str, scenarios, *, note: str = "", again: str = "", register: Path = REGISTER, root: Path = ROOT) -> dict:
    """Append one look. Raises ``ValueError`` for an unknown scenario, an empty phase, or a repeat look without ``again``."""
    scenarios = list(scenarios)
    unknown = [s for s in scenarios if s not in HOLDOUTS]
    if not phase.strip() or not scenarios or unknown:
        raise ValueError(f"a look needs a phase and holdout scenarios from {HOLDOUTS}; unknown: {unknown}")
    current = product_hash(root)
    existing = looks(register)
    earlier = [entry for entry in existing if entry.get("product") == current]
    if earlier and not again.strip():
        raise ValueError(f"this product hash was already looked at ({earlier[0]['date']}, phase {earlier[0]['phase']}); "
                         "pass a reason to look again")
    entry = {"product": current, "date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "phase": phase,
             "scenarios": scenarios, "note": note, "again": again.strip(), "looks_at_this_hash": len(earlier) + 1}
    register.write_text(json.dumps([*existing, entry], indent=1) + "\n", encoding="utf-8")
    return entry


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("hash", "record", "list"):
        print(__doc__)
        return 2
    if argv[0] == "hash":
        print(product_hash())
    elif argv[0] == "list":
        for entry in looks():
            print(f"{entry['date']}  {entry['product'][:12]}  phase {entry['phase']}  {','.join(entry['scenarios'])}"
                  + (f"  AGAIN: {entry['again']}" if entry.get("again") else ""))
    else:
        rest = argv[1:]
        note = again = ""
        for flag in ("--note", "--again"):
            if flag in rest:
                i = rest.index(flag)
                value = rest[i + 1] if i + 1 < len(rest) else ""
                rest = rest[:i] + rest[i + 2:]
                note, again = (value, again) if flag == "--note" else (note, value)
        if len(rest) != 2:
            print(__doc__)
            return 2
        try:
            print(json.dumps(record_look(rest[0], rest[1].split(","), note=note, again=again), indent=1))
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

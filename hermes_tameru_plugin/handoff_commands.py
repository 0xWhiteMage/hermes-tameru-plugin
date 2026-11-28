"""``/tameru handoff``: export a read-only session handoff without putting the ledger in chat."""
from __future__ import annotations

from pathlib import Path

from .handoff import HandoffOptions, export_handoff
from .session_policy import PolicyError

USAGE = ("Use /tameru handoff [SESSION_ID] [--db PATH] [--out DIR] [--tail-rows N]. "
         "The export is read-only and writes handoff.md plus digest.txt in a new directory.")


def _parse(parts: list[str], current_session: str) -> tuple[str, Path | None, Path | None, int]:
    target = current_session
    db = None
    out = None
    tail_rows = HandoffOptions.tail_rows
    positional = []
    index = 0
    while index < len(parts):
        part = parts[index]
        if part in ("--db", "--out", "--tail-rows"):
            if index + 1 >= len(parts):
                raise PolicyError(USAGE)
            value = parts[index + 1]
            if part == "--db":
                db = Path(value).expanduser()
            elif part == "--out":
                out = Path(value).expanduser()
            else:
                try:
                    tail_rows = int(value)
                except (TypeError, ValueError):
                    raise PolicyError("--tail-rows must be a positive integer.") from None
                if tail_rows < 1 or tail_rows > 2_000:
                    raise PolicyError("--tail-rows must be between 1 and 2000.")
            index += 2
        elif part.startswith("-"):
            raise PolicyError(USAGE)
        else:
            positional.append(part)
            index += 1
    if len(positional) > 1:
        raise PolicyError(USAGE)
    if positional:
        target = positional[0]
    if not target:
        raise PolicyError("No session id is available. Use /tameru handoff SESSION_ID [--db PATH].")
    return target, db, out, tail_rows


def handoff_command(engine, parts, *, session_id, **_context) -> dict:
    target, explicit_db, output, tail_rows = _parse(parts, session_id)
    session_db = getattr(engine, "_session_db", None)
    db_path = explicit_db or getattr(session_db, "db_path", None)
    if db_path is None:
        raise PolicyError("This chat is not bound to a state database path. Use --db PATH.")
    try:
        result = export_handoff(Path(db_path), target, output, HandoffOptions(tail_rows=tail_rows))
    except Exception as exc:
        raise PolicyError(f"Handoff export failed: {exc}") from None
    text = "\n".join([
        "Tameru handoff exported (source database was opened read-only).",
        f"  Handoff  {result.handoff_path}",
        f"  Digest   {result.digest_path}",
        f"  Rows     {result.message_count} stored, {result.user_count} user, {result.summary_count} compaction summaries",
        "",
        "Read handoff.md first. Do not paste the whole digest into model context.",
    ])
    return {
        "ok": True, "kind": "handoff", "text": text,
        "handoff_path": str(result.handoff_path), "digest_path": str(result.digest_path),
        "manifest_path": str(result.manifest_path), "rows": result.message_count,
        "users": result.user_count, "summaries": result.summary_count,
        "tail_rows": result.tail_rows, "unanswered_tool_calls": result.unanswered_tool_calls,
        "orphan_tool_results": result.orphan_tool_results,
    }

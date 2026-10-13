"""The rendered-row format: the header grammar, ref minting and rung rewrites.

A row Tameru wrote into a tool result looks like::

    <header line>
    <meta line>        (optional: compact JSON of the Hermes envelope, see ``payload.meta_line``)
    <body>             (optional: the extract, with Hermes' elision markers)

The header is frozen, because committed rows persist in session history for good::

    HEADER := PARENT_LINE " [tameru:" RUNG " kept " K "/" N " lines (" KC " of " TC " chars)"
              [" omitted: " CATS] [" " NOTE] " ref=" REF "]"
    RUNG   := fold | extract | brief | header | superseded
    REF    := 16 lowercase hex (8 before the 1.4.1 fix-all round; both parse, forever)

``PARENT_LINE`` is Hermes' own one-line summary of the original result (``[terminal] ran ... -> exit
1 ...``), so the header on its own is what Hermes calls a summary stub (starts with ``[``, contains
`` chars)``, under 400 chars): later prune passes and the lean tail path leave it alone. The NOTE
only appears on ``superseded`` rows (``superseded by msg 41 (re-read)``).

Rows of the 1.3.0 plugin carry no header; they are recognised by their bare ``[…]`` gap lines
(``classify_render``) so the engine never re-extracts them.

Import-safe: stdlib and the vendored engine only, nothing happens at import time.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from .tameru.compress_context import classify_line

TAMERU_TAG = " [tameru:"
RUNGS = ("fold", "extract", "brief", "errors", "header", "superseded")

# Anchored at the start of the content, first line only: a row that merely quotes a header on a later
# line (or source code that contains this pattern) is not a rendered row.
HEADER_RE = re.compile(
    r"^\[[^\n]*? \[tameru:(fold|extract|brief|errors|header|superseded) [^\n]*? ref=([0-9a-f]{16}|[0-9a-f]{8})\]"
)
# The whole grammar of one header line, for parsing and rewriting. The parent line is greedy: the real
# tag is the last one on the line, so a parent line that quotes a header cannot hijack the parse.
_ROW_RE = re.compile(
    r"(?P<parent>\[[^\n]*) \[tameru:(?P<rung>fold|extract|brief|errors|header|superseded) "
    r"(?P<counts>kept (?P<kept>\d+)/(?P<total>\d+) lines \([\d,]+ of [\d,]+ chars\))"
    r"(?P<extra>[^\n]*?) ref=(?P<ref>[0-9a-f]{16}|[0-9a-f]{8})\](?=\n|\Z)"
)
# The 1.3.0 plugin joined kept blocks with a line that is exactly ``[…]``.
_LEGACY_GAP = "[…]"
_LEGACY_GAP_RE = re.compile(r"^\[…\]$", re.MULTILINE)

_UID_KEYS = ("_tool_call_uid", "message_uid", "tool_call_id")
HEADER_MAX_LEN = 380          # default cap of a header line, under Hermes' 400-char summary-stub limit
_STUB_MAX_LEN = 399           # the hard cap: Hermes' summary-stub test wants len < 400
_PARENT_FLOOR = 24            # a header never shrinks its parent line below this
_PIECE_FLOOR = 8              # nor its omitted/note pieces
_PARENT_TAIL_KEEP = 110       # the tail of a shortened parent line holds "(N chars) FAILED: <error>"


@dataclass(frozen=True)
class RenderStats:
    """What a rendered row kept of its original (``omitted`` is ``((category, lines), ...)``)."""

    kept_lines: int
    total_lines: int
    kept_chars: int
    total_chars: int
    omitted: tuple[tuple[str, int], ...]
    rung: str
    note: str = ""


# ---- detection -------------------------------------------------------------------------------
def classify_render(content: str) -> Literal["v1", "legacy", None]:
    """``"v1"`` for a row with a Tameru header, ``"legacy"`` for a 1.3.0 row, else None.

    The legacy signature is a heuristic: at least one line that is exactly ``[…]`` and content that
    does not start with ``{`` (a JSON tool result never renders a bare gap line). It can misjudge a
    foreign text that happens to hold such a line; the cost is that the row is left untouched.
    """
    if not isinstance(content, str):
        return None
    # The substring tests run at C speed; the regexes only see rows that can match.
    if TAMERU_TAG in content and HEADER_RE.match(content):
        return "v1"
    if _LEGACY_GAP in content and not content.startswith("{") and _LEGACY_GAP_RE.search(content):
        return "legacy"
    return None


def is_tameru_rendered(content: str) -> bool:
    """True for a row Tameru rendered, current header or legacy 1.3.0 signature."""
    return classify_render(content) is not None


def parse_header(content: str) -> dict | None:
    """``{"rung", "ref", "kept", "total"}`` (kept/total are line counts) of a current row, else None."""
    if not isinstance(content, str):
        return None
    m = _ROW_RE.match(content)
    if m is None:
        return None
    return {
        "rung": m["rung"], "ref": m["ref"], "kept": int(m["kept"]), "total": int(m["total"]),
    }


def for_summary(content: str) -> str:
    """``content`` as the summarizer and Hermes' anchor index should read it (anything but a current row: as is).

    A header-only row (its text is gone) is cut to Hermes' own one-line summary that opens it: the counts, the
    omitted categories and the ``ref`` are bookkeeping of the prompt cache and the recovery tool, not facts a
    summary can use, and they made every such row two to four times as long as Hermes' line. Any other rung keeps
    its header (it says the body is partial) without the ``ref``: Hermes' anchor index reads a 16-hex ref as a
    commit hash, so an unlabelled list of the refs of rows that are gone crowded real commit ids out of it.
    """
    if not isinstance(content, str) or TAMERU_TAG not in content[:HEADER_MAX_LEN + 200]:
        return content
    m = _ROW_RE.match(content)
    if m is None:
        return content
    if m["rung"] == "header" and m.end() == len(content):
        return m["parent"]
    return content[:m.start("ref") - len(" ref=")] + "]" + content[m.end():]


def header_parent(content: str) -> str | None:
    """Hermes' own one-line summary that opens a current row's header, else None."""
    m = _ROW_RE.match(content) if isinstance(content, str) else None
    return None if m is None else m["parent"]


# ---- refs and statistics ---------------------------------------------------------------------
REF_CHARS = 16          # 64 bits (fix-all R7); LEGACY_REF_CHARS-long refs of older rows are a prefix of these
LEGACY_REF_CHARS = 8


def ref_matches(ref: str, key: str) -> bool:
    """True when the header ref ``ref`` names the stored ``key`` (equal, or a legacy 8-hex prefix of it)."""
    return ref == key or (len(ref) == LEGACY_REF_CHARS and len(key) == REF_CHARS and key.startswith(ref))


def make_ref(msg: dict, original: str) -> str:
    """16 hex chars naming ``original`` in ``msg``: ``sha256(uid|sha256(original))[:16]``.

    Rows rendered before the 1.4.1 fix-all round carry the first 8 of these 16 chars; lookups accept both
    (``ref_matches``), so an old ref still finds its original.

    ``uid`` is the first non-empty of ``_tool_call_uid``, ``message_uid``, ``tool_call_id`` (Hermes
    keeps the first two across restarts and session-db round trips), else "".
    """
    uid = next((str(msg[key]) for key in _UID_KEYS if msg.get(key)), "")
    digest = hashlib.sha256(original.encode("utf-8", "surrogatepass")).hexdigest()
    return hashlib.sha256(f"{uid}|{digest}".encode()).hexdigest()[:REF_CHARS]


def _lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def omitted_categories(original: str, kept: str, limit: int = 3) -> tuple[tuple[str, int], ...]:
    """The ``limit`` biggest categories (``classify_line``) of the lines ``kept`` dropped.

    Lines are matched as a multiset (trailing whitespace ignored); blank lines are not counted.
    Ordered by count, then name.
    """
    remaining = Counter(line.rstrip() for line in _lines(kept))
    dropped: Counter[str] = Counter()
    for line in _lines(original):
        line = line.rstrip()
        if remaining[line] > 0:
            remaining[line] -= 1
        elif line:
            dropped[classify_line(line)] += 1
    ranked = sorted(dropped.items(), key=lambda item: (-item[1], item[0]))
    return tuple(ranked[:max(limit, 0)])


# ---- rendering -------------------------------------------------------------------------------
def _one_line(text: str) -> str:
    return " ".join(text.splitlines())


def _shorten(text: str, limit: int, keep_tail: int = 0) -> str:
    """``text`` cut to ``limit`` chars with one ``…`` in place of the middle (or the end)."""
    if len(text) <= limit:
        return text
    tail = min(keep_tail, (limit - 1) // 2)
    return text[:limit - 1 - tail] + "…" + (text[-tail:] if tail else "")


def _compose(
    parent: str, rung: str, counts: str, pieces: list[str], ref: str, max_len: int,
) -> str:
    """One header line within ``max_len``: shrink the parent line first, then each piece in order."""
    if not parent.startswith("["):
        parent = f"[{parent}]"
    fixed = f" [tameru:{rung} {counts}"
    tail = f" ref={ref}]"
    texts = [parent, *(p for p in pieces if p)]
    floors = [_PARENT_FLOOR] + [_PIECE_FLOOR] * (len(texts) - 1)
    excess = sum(map(len, texts)) + len(texts) - 1 + len(fixed) + len(tail) - min(max_len, _STUB_MAX_LEN)
    for i, floor in enumerate(floors):
        if excess <= 0:
            break
        cut = min(excess, max(len(texts[i]) - floor, 0))
        if cut:
            texts[i] = _shorten(texts[i], len(texts[i]) - cut, _PARENT_TAIL_KEEP if i == 0 else 0)
            excess -= cut
    return texts[0] + fixed + "".join(" " + piece for piece in texts[1:]) + tail


def build_header(
    parent_line: str, stats: RenderStats, ref: str, *, max_len: int = HEADER_MAX_LEN,
) -> str:
    """The one-line header of a row (see the module docstring), at most ``max_len`` chars.

    ``parent_line`` is wrapped in ``[...]`` when it does not start with ``[``. When the line is too
    long the parent line is shortened first (keeping the end, where Hermes puts ``FAILED: ...``),
    then the omitted categories, then the note. ``max_len`` is capped at 399 so the result stays a
    Hermes summary stub.
    """
    counts = (
        f"kept {stats.kept_lines}/{stats.total_lines} lines "
        f"({stats.kept_chars:,} of {stats.total_chars:,} chars)"
    )
    cats = ", ".join(f"{name} {count}" for name, count in stats.omitted)
    # The note can quote text of the result (a spill path): never let it carry a tag the parse could take for ours.
    pieces = [f"omitted: {cats}" if cats else "", _one_line(stats.note).replace(TAMERU_TAG, " [tameru ")]
    return _compose(_one_line(parent_line), stats.rung, counts, pieces, ref, max_len)


def render(
    parent_line: str, stats: RenderStats, ref: str, *, meta_line: str = "", body: str = "",
) -> str:
    """Header, then the optional meta line and body, each on its own line."""
    out = build_header(parent_line, stats, ref)
    if meta_line:
        out += "\n" + meta_line
    if body:
        out += "\n" + body
    return out


# Hermes' terminal one-liner (``agent/context_compressor.py::_sum_terminal``).
_TERMINAL_LINE_RE = re.compile(r"(?P<head>\[terminal\] ran `.*` -> exit (?:-?\d+|\?), )\d+(?P<tail> lines output)\Z", re.DOTALL)


def true_line_count(parent_line: str, lines: int) -> str:
    """Hermes' terminal one-liner with ``lines`` as its line count; any other parent line unchanged.

    Hermes counts the newlines of the raw JSON result, so every terminal row says "1 lines output"; the
    caller passes the line count of the unwrapped payload (the one the header's ``kept K/N lines`` uses).
    The header builder still caps the whole line at 399 chars.
    """
    found = _TERMINAL_LINE_RE.fullmatch(" ".join(parent_line.splitlines())) if isinstance(parent_line, str) else None
    if found is None:
        return parent_line
    return f"{found['head']}{lines}{found['tail']}"


def _parent_of(content_or_parent_line: str) -> str:
    """The parent line of a rendered row, else the first line of the text."""
    m = _ROW_RE.match(content_or_parent_line)
    if m is not None:
        return m["parent"]
    return _one_line(content_or_parent_line.split("\n", 1)[0])


def header_only(content_or_parent_line: str, stats: RenderStats, ref: str) -> str:
    """A body-less row for ``stats``: just the header.

    Takes a parent line, or a rendered row whose parent line is reused.
    """
    return build_header(_parent_of(content_or_parent_line), stats, ref)


def retarget_rung(content: str, rung: str) -> str | None:
    """A rendered row cut down to its header, relabelled ``rung`` (stats, notes and ref unchanged).

    None when ``content`` is not a current row or ``rung`` is unknown (legacy rows have no header
    to keep; the engine builds theirs from the parent line).
    """
    if rung not in RUNGS or not isinstance(content, str):
        return None
    m = _ROW_RE.match(content)
    if m is None:
        return None
    return _compose(m["parent"], rung, m["counts"], [m["extra"].strip()], m["ref"], HEADER_MAX_LEN)

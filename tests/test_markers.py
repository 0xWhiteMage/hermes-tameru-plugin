"""Gap markers inside rendered rows are Hermes' own, and their counts are exact."""
from __future__ import annotations

import re

import pytest
from engine_support import Chat, demote, make_engine, server_log

from hermes_tameru_plugin.hermes_compat import MARKER_PREFIX, elision_marker, marker_artifact_re
from hermes_tameru_plugin.payload import parse_payload
from hermes_tameru_plugin.render import parse_header

_COUNTS = re.compile(r"(\d[\d,]*) of (\d[\d,]*) chars omitted")
_KEPT = re.compile(r"kept (\d+)/(\d+) lines \(([\d,]+) of ([\d,]+) chars\)")


def _row(content: str, task: str = "why did the payment gateway time out?") -> tuple[str, str]:
    """``(inner, rendered row)`` of a one-result chat."""
    chat = Chat(task)
    idx = chat.tool("terminal", {"command": "tail -n 450 app.log"}, content)
    _, out = demote(make_engine(), chat.finish(), idx)
    return parse_payload(content).inner, out[idx]["content"]


def _body(row: str) -> str:
    return row.split("\n", 2)[2]


def _walk(inner: str, body: str) -> tuple[list[tuple[int, int]], int]:
    """Markers ``(omitted, total)`` of ``body`` and the kept-line count, checking each marker against the
    span of ``inner`` lines it stands for."""
    lines = inner.split("\n")
    marker, pos, pending, found, kept = marker_artifact_re(), 0, None, [], 0
    for text in body.split("\n"):
        if marker.search(text):
            pending = tuple(int(n.replace(",", "")) for n in _COUNTS.search(text).groups())
            found.append(pending)
            continue
        at = lines.index(text, pos)
        dropped = lines[pos:at]
        span = len("\n".join(dropped))
        assert (pending[0] if pending else 0) == span, f"marker says {pending}, {len(dropped)} lines span {span}"
        pos, pending, kept = at + 1, None, kept + 1
    trailing = len("\n".join(lines[pos:]))
    if pending is not None:   # a trailing marker follows the last kept line
        assert pending[0] == trailing
    return found, kept


def test_markers_are_hermes_elision_markers_with_exact_counts():
    inner, row = _row(server_log())
    body = _body(row)
    found, kept = _walk(inner, body)
    assert found and all(total == len(inner) for _, total in found)
    for omitted, total in found:
        assert elision_marker(omitted, total) in body


def test_header_counts_match_the_body():
    inner, row = _row(server_log())
    header = parse_header(row)
    found, kept = _walk(inner, _body(row))
    shown = _KEPT.search(row.split("\n")[0]).groups()
    assert (header["kept"], header["total"]) == (kept, len(inner.split("\n")))
    assert int(shown[2].replace(",", "")) == len(_body(row))
    assert int(shown[3].replace(",", "")) == len(inner)


@pytest.mark.real_hermes
def test_markers_match_hermes_artifact_regex_and_template():
    from agent import compression_marker as cm

    inner, row = _row(server_log())
    found, _ = _walk(inner, _body(row))
    marker_lines = [t for t in _body(row).split("\n") if t.startswith(MARKER_PREFIX)]
    assert found and len(marker_lines) == len(found)
    for text, (omitted, total) in zip(marker_lines, found):
        assert cm._COMPRESSION_MARKER_ARTIFACT_RE.search(text)
        assert text == cm._elision_marker(omitted, total)
    assert len(max(marker_lines, key=len)) <= cm.ELISION_MARKER_MAX_LEN

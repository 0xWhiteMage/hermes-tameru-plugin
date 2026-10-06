"""``telemetry``: content-free counters, thread safety, the JSONL log and copy semantics."""
from __future__ import annotations

import copy
import json
import threading

from hermes_tameru_plugin.telemetry import (
    MAX_LABEL_CHARS,
    MAX_LABEL_VALUES,
    OVERFLOW_LABEL,
    Telemetry,
)


def test_fresh_snapshot_is_empty_and_json_serializable():
    snap = Telemetry().snapshot()
    assert snap == {
        "events": {},
        "chars": {"before": 0, "after": 0, "saved": 0},
        "time": {"seconds": 0.0, "calls": 0},
    }
    assert json.loads(json.dumps(snap)) == snap


def test_count_keeps_a_total_and_a_breakdown_per_label():
    t = Telemetry()
    t.count("render", rung="extract", tool="terminal")
    t.count("render", rung="extract", tool="read_file")
    t.count("render", 3, rung="header", tool="terminal")
    t.count("escalation")
    events = t.snapshot()["events"]
    assert events["render"] == {
        "count": 5,
        "labels": {"rung": {"extract": 2, "header": 3}, "tool": {"read_file": 1, "terminal": 4}},
    }
    assert events["escalation"] == {"count": 1, "labels": {}}


def test_chars_and_time_accumulate():
    t = Telemetry()
    t.add_chars(10_000, 1_200)
    t.add_chars(500, 600)
    t.time(0.25)
    t.time(0.5)
    snap = t.snapshot()
    assert snap["chars"] == {"before": 10_500, "after": 1_800, "saved": 8_700}
    assert snap["time"] == {"seconds": 0.75, "calls": 2}


def test_snapshot_is_deterministic_and_sorted_whatever_the_insertion_order():
    a, b = Telemetry(), Telemetry()
    for event in ("zeta", "alpha", "mid"):
        a.count(event, tool="x", rung="y")
    for event in ("mid", "zeta", "alpha"):
        b.count(event, rung="y", tool="x")
    assert json.dumps(a.snapshot()) == json.dumps(b.snapshot())
    assert list(a.snapshot()["events"]) == ["alpha", "mid", "zeta"]


def test_snapshot_is_a_copy_not_a_live_view():
    t = Telemetry()
    t.count("render", rung="extract")
    snap = t.snapshot()
    t.count("render", rung="extract")
    assert snap["events"]["render"]["count"] == 1
    snap["events"]["render"]["labels"]["rung"]["extract"] = 99
    assert t.snapshot()["events"]["render"]["labels"]["rung"]["extract"] == 2


def test_reset_clears_everything():
    t = Telemetry()
    t.count("render", rung="extract")
    t.add_chars(5, 1)
    t.time(1.0)
    t.reset()
    assert t.snapshot() == Telemetry().snapshot()


def test_label_values_are_bounded():
    t = Telemetry()
    t.count("render", tool="x" * 500)
    (value,) = t.snapshot()["events"]["render"]["labels"]["tool"]
    assert len(value) == MAX_LABEL_CHARS
    for i in range(MAX_LABEL_VALUES + 20):
        t.count("seen", tool=f"tool_{i}")
    values = t.snapshot()["events"]["seen"]["labels"]["tool"]
    assert len(values) == MAX_LABEL_VALUES + 1, "the distinct values plus the overflow bucket"
    assert values[OVERFLOW_LABEL] == 20
    t.count("seen", tool="tool_0")
    assert t.snapshot()["events"]["seen"]["labels"]["tool"]["tool_0"] == 2, "known values keep counting"


def test_counters_are_exact_under_threads():
    t = Telemetry()

    def work():
        for _ in range(500):
            t.count("render", rung="extract")
            t.add_chars(10, 4)
            t.time(0.001)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    snap = t.snapshot()
    assert snap["events"]["render"]["count"] == 4000
    assert snap["events"]["render"]["labels"]["rung"]["extract"] == 4000
    assert snap["chars"] == {"before": 40_000, "after": 16_000, "saved": 24_000}
    assert snap["time"]["calls"] == 4000


# ---- the log -----------------------------------------------------------------------------------
def test_emit_appends_one_json_line_per_record(tmp_path):
    log = tmp_path / "logs" / "tameru.jsonl"
    t = Telemetry(str(log))
    t.emit({"pass": 1, "rung": "extract", "tool": "terminal", "before": 9000, "after": 800})
    t.emit({"pass": 2, "rung": "header"})
    lines = log.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [
        {"pass": 1, "rung": "extract", "tool": "terminal", "before": 9000, "after": 800},
        {"pass": 2, "rung": "header"},
    ]
    assert lines[0] == json.dumps(json.loads(lines[0]), sort_keys=True, ensure_ascii=False)


def test_emit_without_a_path_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Telemetry().emit({"pass": 1})
    assert list(tmp_path.iterdir()) == []


def test_emit_never_raises(tmp_path):
    Telemetry(str(tmp_path)).emit({"pass": 1})                   # the path is a directory
    Telemetry(str(tmp_path / "blocked" / "x.jsonl")).emit({})    # fine: parent created
    blocker = tmp_path / "file"
    blocker.write_text("x")
    Telemetry(str(blocker / "x.jsonl")).emit({"pass": 1})        # a parent that is a file
    loop: dict = {}
    loop["self"] = loop
    Telemetry(str(tmp_path / "loop.jsonl")).emit(loop)           # unserializable record
    Telemetry(str(tmp_path / "ok.jsonl")).emit({"set": {1, 2}})  # default=str covers odd values


def test_emit_is_exact_under_threads(tmp_path):
    log = tmp_path / "t.jsonl"
    t = Telemetry(str(log))
    threads = [
        threading.Thread(target=lambda n=n: [t.emit({"worker": n, "i": i}) for i in range(50)])
        for n in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 300
    assert {(r["worker"], r["i"]) for r in records} == {(n, i) for n in range(6) for i in range(50)}


# ---- copies ------------------------------------------------------------------------------------
def test_copy_and_deepcopy_give_a_fresh_instance_with_the_same_log(tmp_path):
    t = Telemetry(str(tmp_path / "t.jsonl"))
    t.count("render", rung="extract")
    t.add_chars(10, 1)
    for clone in (copy.copy(t), copy.deepcopy(t)):
        assert clone is not t
        assert clone.snapshot() == Telemetry().snapshot()
        clone.emit({"pass": 1})
    assert (tmp_path / "t.jsonl").read_text(encoding="utf-8").count("\n") == 2
    assert t.snapshot()["events"]["render"]["count"] == 1, "the original is untouched"


def test_deepcopy_of_a_holder_does_not_trip_over_the_lock():
    holder = {"telemetry": Telemetry("x.jsonl"), "other": [1, 2]}
    cloned = copy.deepcopy(holder)
    assert isinstance(cloned["telemetry"], Telemetry)
    assert cloned["telemetry"] is not holder["telemetry"]

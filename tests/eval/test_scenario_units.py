"""Unit tests of the extra replay scenarios (``oncall``, ``refactor``, ``smallrows``, ``i18n``). No Hermes needed.

They check what the scenarios are *for*: that each has the shape its name promises (a log tailed again and again;
read -> patch -> re-read cycles; hundreds of mid-size results; non-Latin output), that every gold fact sits where it
says and nowhere an engine could keep it for the wrong reason, and that a build depends on the seed only (not on
``PYTHONHASHSEED``, the clock or the order of anything).
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import compare
import pytest
import scenario as original
import scenario_registry as registry
from replay import error_fingerprints
from scenario import CATEGORIES

EXTRA = tuple(n for n in registry.scenario_names() if n != registry.DEFAULT_SCENARIO)
HERE = Path(__file__).resolve().parent


def build(name: str, seed: int = 0, turns: int | None = None):
    spec = registry.get_spec(name)
    return spec.build(seed, spec.turns if turns is None else turns)


def steps(sc):
    return [(i, s) for i, t in enumerate(sc.turns, 1) for r in t.rounds for s in r.steps]


def results(sc):
    return [s.result for _, s in steps(sc)]


# ---- registry -------------------------------------------------------------------------------------
def test_the_registry_has_the_default_and_the_four_shapes():
    assert registry.scenario_names() == ("default", "oncall", "refactor", "smallrows", "i18n")
    assert registry.get_spec("default").build is original.build_scenario
    assert registry.get_spec("default").turns == original.N_TURNS == 30


def test_an_unknown_scenario_lists_the_registered_names():
    with pytest.raises(KeyError, match="oncall"):
        registry.get_spec("nope")


def test_compare_parses_valued_options_in_both_spellings():
    opts, rest = compare.parse_options(["--scenario", "oncall", "stock", "--seed=3", "--json"], ("--scenario", "--seed"))
    assert opts == {"--scenario": "oncall", "--seed": "3"} and rest == ["stock", "--json"]
    with pytest.raises(SystemExit):
        compare.parse_options(["--scenario"], ("--scenario",))


# ---- every scenario ---------------------------------------------------------------------------------
@pytest.mark.parametrize("name", EXTRA)
def test_natural_length_and_the_final_question_turn(name):
    spec = registry.get_spec(name)
    sc = build(name)
    assert len(sc.turns) == spec.turns - 1  # the final question is added by run_session
    assert sc.final_question and sc.system_prompt == original.SYSTEM_PROMPT


def test_natural_lengths():
    assert [registry.get_spec(n).turns for n in EXTRA] == [40, 35, 40, 32]


@pytest.mark.parametrize("name", EXTRA)
def test_a_build_depends_on_the_seed_alone(name):
    a, b, c = build(name, 0), build(name, 0), build(name, 1)
    assert a == b
    assert results(a) != results(c)  # filler values change with the seed ...
    assert [g.id for g in a.gold] == [g.id for g in c.gold]  # ... the script and its facts do not
    assert [t.user for t in a.turns] == [t.user for t in c.turns]


@pytest.mark.parametrize("name", EXTRA)
def test_shorter_builds_drop_the_facts_and_errors_after_the_cut(name):
    sc = build(name, 0, 10)
    assert len(sc.turns) == 9
    assert sc.gold and all(g.turn <= 9 for g in sc.gold)
    assert len(sc.error_lines) <= len(build(name).error_lines)
    with pytest.raises(ValueError):
        build(name, 0, 3)
    with pytest.raises(ValueError):
        build(name, 0, registry.get_spec(name).turns + 1)


@pytest.mark.parametrize("name", EXTRA)
def test_every_probe_category_has_gold_and_needs(name):
    sc = build(name)
    by_cat = collections.Counter(g.category for g in sc.gold)
    assert set(by_cat) == set(CATEGORIES), by_cat
    assert all(by_cat[c] >= 2 for c in CATEGORIES), by_cat
    assert len({g.id for g in sc.gold}) == len(sc.gold) >= 13
    assert sum(g.needed for g in sc.gold) >= 8
    assert {g.source for g in sc.gold} == {"user", "assistant", "tool"}
    # tool facts name the call that printed them; the final question cannot give a fact away
    assert all(g.call_key for g in sc.gold if g.source == "tool")
    assert not any(g.needle in sc.final_question for g in sc.gold)
    assert len({g.turn for g in sc.gold}) >= 8, "facts are spread over the session"


@pytest.mark.parametrize("name", EXTRA)
def test_error_lines_make_several_fingerprints_and_each_is_shown_to_the_model(name):
    sc = build(name)
    fps = error_fingerprints("\n".join(sc.error_lines))
    assert len(fps) >= 6
    joined = "\n".join(results(sc))
    assert all(line in joined for line in sc.error_lines), "an error line the model was never shown"


@pytest.mark.parametrize("seed", (0, 3))
@pytest.mark.parametrize("name", EXTRA)
def test_gold_needles_sit_only_where_the_fact_is_planted(name, seed):
    """A tool fact must not be repeated in user or assistant text (it would survive Hermes' verbatim-user section or a
    summary for the wrong reason); it must be in the result of the call it names, and elsewhere only in results of
    the same call family (a re-read, a diff, a patch). User and assistant facts are in their own role's text."""
    sc = build(name, seed)
    final_text = sc.final_question
    for g in sc.gold:
        assert '"' not in g.needle and "\\" not in g.needle and "\n" not in g.needle, g.id
        user = [t.user for t in sc.turns] + [final_text]
        say = [t.final for t in sc.turns] + [r.say for t in sc.turns for r in t.rounds]
        args = [json.dumps(s.args, ensure_ascii=False) for _, s in steps(sc)]
        found_in = {
            "user": [g.needle in x for x in user],
            "assistant": [g.needle in x for x in say],
        }
        if g.source != "user":
            assert not any(found_in["user"]), f"{g.id} ({g.source}) is also in user text"
        if g.source != "assistant":
            assert not any(found_in["assistant"]), f"{g.id} ({g.source}) is also in assistant text"
        if g.source == "assistant":
            assert any(found_in["assistant"]) or any(g.needle in a for a in args)
        if g.source == "tool":
            base = g.call_key.split("#")[0]
            owners = [s for _, s in steps(sc) if g.needle in s.result]
            assert any(s.key == g.call_key for s in owners), f"{g.id} is not in the result of {g.call_key}"
            for s in owners:
                if g.category == "artifact":
                    break  # a file, test or path legitimately recurs in the output about the same file
                derived = s.name in ("patch", "write_file") or " diff" in s.args.get("command", "")
                assert derived or s.key.split("#")[0] == base, f"{g.id} also appears in the result of {s.key}"


@pytest.mark.parametrize("name", EXTRA)
def test_tool_results_are_hermes_shaped_json_and_calls_have_arguments(name):
    for _, s in steps(build(name)):
        doc = json.loads(s.result) if not s.result.startswith("<persisted-output>") else {}
        if s.name == "terminal":
            assert list(doc) == ["output", "exit_code", "error"], s.key
        elif s.name == "read_file":
            assert doc["content"].startswith("1|") and doc["total_lines"] >= 1
        elif s.name == "patch":
            assert doc["success"] is True and doc["files_modified"]
        elif s.name == "write_file":
            assert doc["verified"] is True
        assert isinstance(s.args, dict) and s.args


def _digest(name: str, seed: int) -> str:
    sc = build(name, seed)
    return hashlib.sha256(repr(sc).encode("utf-8")).hexdigest()


@pytest.mark.parametrize("hashseed", ("0", "5"))
def test_builds_do_not_depend_on_pythonhashseed(hashseed):
    """Set iteration order, ``hash()`` and dict-of-set tricks would make a replay differ between runs."""
    code = (
        "import sys, hashlib\n"
        f"sys.path[:0] = {[str(HERE), str(HERE.parent), str(HERE.parent.parent)]!r}\n"
        "import scenario_registry as r\n"
        "for n in r.scenario_names():\n"
        "    s = r.get_spec(n); print(n, hashlib.sha256(repr(s.build(0, s.turns)).encode('utf-8')).hexdigest())\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True, cwd=HERE.parent.parent / "docs").stdout
    got = dict(line.split() for line in out.splitlines())
    assert got == {n: _digest(n, 0) for n in registry.scenario_names()}


# ---- the shape each scenario promises -------------------------------------------------------------------
def test_oncall_is_log_heavy_with_a_log_tailed_again_while_it_grows():
    sc = build("oncall")
    cmds = [s.args.get("command", "") for _, s in steps(sc)]
    for tool in ("journalctl", "kubectl -n shop logs", "docker logs", "kubectl -n shop describe", "helm -n shop"):
        assert any(tool in c for c in cmds), tool
    tails = [s.result for _, s in steps(sc) if s.args.get("command") == "tail -n 450 /var/log/shop/app.log"]
    assert len(tails) >= 6 and len({*tails}) == len(tails), "the same command, a different (grown) output every time"
    lines = [set(json.loads(t)["output"].splitlines()) for t in tails]
    for a, b in zip(lines, lines[1:]):
        assert len(a & b) >= 0.4 * len(a), "consecutive tails overlap: the log grows by a few lines"
    log_chars = sum(len(r) for r in results(sc))
    assert log_chars > 400_000 and sum(len(t) for t in tails) > 0.25 * log_chars
    big_json = [r for _, s in steps(sc) if s.args.get("command", "").startswith("curl")
                for r in [s.result] if len(r) > 8_000]
    assert len(big_json) >= 2 and all(json.loads(json.loads(r)["output"]) for r in big_json)


def test_refactor_is_read_patch_reread_with_real_edits():
    sc = build("refactor")
    seq = steps(sc)
    reads = collections.defaultdict(list)
    for _, s in seq:
        if s.name == "read_file":
            reads[s.args["path"]].append(s.result)
    assert sum(1 for rs in reads.values() if len(rs) >= 2) >= 6
    assert sum(1 for rs in reads.values() if len(set(rs)) >= 2) >= 6, "a re-read shows the edit"
    assert max(len(rs) for rs in reads.values()) >= 4
    assert sum(1 for _, s in seq if s.name == "patch") >= 10
    assert sum(1 for _, s in seq if s.name == "read_file" and len(s.result) > 9_000) >= 12
    pytest_runs = [s for _, s in seq if "pytest" in s.args.get("command", "")]
    assert len(pytest_runs) >= 5 and len({s.result for s in pytest_runs}) == len(pytest_runs)
    searches = [s for _, s in seq if s.name == "search_files" or s.args.get("command", "").startswith("rg ")]
    assert len(searches) >= 6
    # supersession: the rename grep answers shrink as the rename advances
    renames = [len(s.result) for s in searches if "OrderStatus.PAID" in json.dumps(s.args)]
    assert renames[0] > renames[-1]
    # the new modules exist in the session
    assert any(s.name == "write_file" and s.args["path"].endswith("pricing/engine.py") for _, s in seq)


def test_smallrows_is_many_results_of_800_to_3000_characters():
    sizes = [len(r) for r in results(build("smallrows"))]
    assert len(sizes) >= 150
    assert sum(800 <= n <= 3000 for n in sizes) >= 0.95 * len(sizes)
    assert max(sizes) <= 3200 and sum(sizes) > 250_000
    assert sum(1 for n in sizes if n > 2000) >= 10 and sum(1 for n in sizes if n < 1500) >= 50, "a spread, not one size"
    rounds = [len(r.steps) for t in build("smallrows").turns for r in t.rounds]
    assert max(rounds) >= 5, "parallel tool calls"


def test_i18n_is_non_latin_output_and_non_latin_user_text():
    sc = build("i18n")
    text = "\n".join(results(sc))

    def share(chars, lo, hi):
        return sum(1 for ch in chars if lo <= ord(ch) <= hi)

    assert share(text, 0x4E00, 0x9FFF) > 3_000        # CJK ideographs
    assert share(text, 0x3040, 0x30FF) > 500          # kana
    assert share(text, 0xAC00, 0xD7AF) > 500          # hangul
    assert share(text, 0x0600, 0x06FF) > 2_000        # Arabic
    assert share(text, 0x0400, 0x04FF) > 2_000        # Cyrillic
    assert all("\\u" not in r for r in results(sc)), "ensure_ascii=False, as the real tools print"
    assert any(share(t.user, 0x3040, 0x30FF) for t in sc.turns), "a user message in Japanese"
    assert any(g.source == "user" and share(g.needle, 0x3040, 0x9FFF) for g in sc.gold)
    assert any(g.source == "tool" and share(g.needle, 0x0600, 0x06FF) for g in sc.gold)
    # a catalog is read, patched and read again
    names = [(s.name, s.args.get("path")) for _, s in steps(sc)]
    i = names.index(("patch", "locales/ko.json"))
    assert ("read_file", "locales/ko.json") in names[:i] and ("read_file", "locales/ko.json") in names[i:]

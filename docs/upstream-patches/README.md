# Upstream patches (tameru-compaction-system 1.4.0 and the 1.4.1 candidate)

The engine fixes behind plugin 1.4.0 were made in
[`0xWhiteMage/tameru-compaction-system`](https://github.com/0xWhiteMage/tameru-compaction-system) and vendored
here. They could not be pushed upstream from the environment that produced them (the Claude GitHub App has no
write access to that repository), so they are exported as patches. Nothing in this repo depends on them being
applied upstream: the vendored copy in `hermes_tameru_plugin/tameru/` already contains their effect.

| Patch | Summary |
|---|---|
| `0001-feat-engine-fixes-for-agent-tool-output-plugin-facin.patch` | Engine fixes for agent tool output and the plugin-facing API (critical-line regex flag, quadratic hot spots, per-fingerprint error exemplars, filler queries, `gap_marker` / `recursion_markers` / `content_hint`, `error_fingerprints`, `brief_context`, `extract_evidence_lines`, `contains_secret`, payload unwrap/rewrap, `ENGINE_VERSION`, `task_query`, `sync_to_harness.py --check`; the `integration/hermes` bundle is removed) |
| `0002-feat-1.4.0-agent-output-formats-lossless-folding-log.patch` | 1.4.0: agent-output format adapters, lossless folding, log templates, JSON brief; version bump, CHANGELOG, RESEARCH takeaways |
| `0003-fix-1.4.0-QA-round-2-test-runner-errors-template-ove.patch` | QA round 2 fixes: test-runner errors kept, log-template over-masking, zero-count negation, recursion markers in JSON payloads, byte-exact JSON fold, numbered-code hint, big-integer crash |
| `0004-fix-1.4.1-candidate-lost-answers-pytest-captured-log.patch` | 1.4.1 candidate: pytest captured-section collapse, saturated grep/rg mode, opt-in `inline_short_gaps`, wider secrets screen, Python 3.12+ cache-test fix |
| `0005-fix-1.4.1-candidate-follow-up-CLI-flags-in-queries-g.patch` | 1.4.1 candidate follow-up: CLI flags are not query selectors, one host marker per run of omitted grep files, captured section ends at the next header |
| `0006-fix-secrets-unquoted-assignment-pattern-no-longer-fl.patch` | 1.4.1 red-team round: the unquoted `KEY=value` secrets pattern no longer flags code expressions (calls, indexes, placeholders, attribute chains, snake_case names) |
| `0007-fix-brief-brief_context-inlines-dropped-spans-shorte.patch` | 1.4.1 fix-all round: `brief_context` honours `InliningGapMarker` (a dropped span shorter than the marker stays verbatim) |
| `0008-fix-secrets-flag-whole-line-env-YAML-credential-assi.patch` | 1.4.1 fix-all round: whole-line env / YAML credential assignments with snake_case or dotted values are secrets again |

All eight are now also on the engine's `main` (merged from its `tameru-1.4.1` branch; `b480ab3..91f00e0`; the
1.4.0 commits there are `24a6bab`, `94960c0`, `a541e92`, re-applied from `0001`-`0003`, so their ids differ from the
`11b9e5e` named below). `0004` and `0005` are `a541e92..e099d10`; `0006`-`0008` are `e099d10..91f00e0`. The vendored
copy is `91f00e0` (`VENDORED_FROM`). Generated with
`git format-patch a541e92..e099d10 --start-number 4`, `git format-patch e099d10..a1e8a7f --start-number 6` and
`git format-patch a1e8a7f..91f00e0 --start-number 7`; apply them in order after `0001`-`0003`.

Range exported: `b480ab3..v1.4.0` (upstream `v1.4.0` is commit `11b9e5ed336c51a35f43cf9ec438023b2e0ca277`, the
commit `VENDORED_FROM` recorded for 1.4.0). Generated with:

```bash
cd tameru-compaction-system
git format-patch b480ab3..v1.4.0 -o <this_repo>/docs/upstream-patches/
```

## Applying them

Start from upstream commit `b480ab3` (or any tree identical to it), on a fresh branch:

```bash
git clone https://github.com/0xWhiteMage/tameru-compaction-system
cd tameru-compaction-system
git checkout -b release/1.4.0 b480ab3
git am /path/to/hermes-tameru-plugin/docs/upstream-patches/*.patch
```

`git am` keeps the original author, message and the `Co-Authored-By` trailer. If it stops on a conflict
(upstream has moved on), resolve it, `git add` the files and `git am --continue`; or abort with `git am --abort`
and apply onto a branch from `b480ab3`, then merge or rebase that branch normally. Afterwards check that the result
equals what is vendored here (`python scripts/sync_to_harness.py <this_repo>/hermes_tameru_plugin/tameru --manifest ../plugin.yaml --check`
should report no drift) and tag `v1.4.0` if you want the tag back.

The patch files are plain text and are excluded from the wheel and sdist (`docs/` does not ship).

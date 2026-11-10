# 1.9.0 release readiness (2026-11-11)

Everything below is local. Nothing has been pushed, no catalogue PR exists, and nothing has been filed upstream.

## State

| Item | Value |
|---|---|
| Plugin repo | `main`, 65 commits ahead of `origin/main` (backup branch `backup/pre-squash-20261110` kept locally) |
| Engine repo | `main` at `9f1f1c45`; the vendored copy matches it byte for byte (`scripts/check_vendored.py`) |
| Versions | plugin 1.9.0, engine 1.9.0 (versioned separately from here on) |
| Hermes tested on | `3b421578` (v0.23.1); stock baselines re-recorded, only the pin and timing keys moved |
| Tests | stub suite 2095 passed; real Hermes 3289 passed, 55 expected failures, 7 skipped; eval 991 passed |
| Live checks (Hermes Desktop, Haiku 4.5) | `lazy_tool` hides then reveals the recovery tool (seen live on 0.23.0 and 0.23.1); 16-output task answered correctly with 9 results shortened, 55K tokens freed |
| Lint | `ruff` is not installed on this PC, so `ruff check .` has not run locally; CI runs it |

## What to read before pushing

1. `CHANGELOG.md` (Unreleased): defaults changed (host tuning and learned priors off), `enabled: false` is stock Hermes, new off-by-default
   settings (`lazy_tool`, `net_estimate`, `stats_ledger`), `/tameru help|stats|reset`, deprecations, the `mcp__` fix.
2. `docs/COST-TUNING-2026-11-10.md`: every measurement and every rejected idea, including the live A/B (one task 18.5% cheaper, one inconclusive)
   and the cache-cold ceiling (rejected; no upstream request needed).
3. `README.md`: "At a glance" says when Tameru costs more.
4. `docs/UPSTREAM-REQUESTS-DRAFT.md`: three requests remain (the turn-start prune hook was withdrawn); none is filed.

## Order, when you approve it

1. Plugin: `git push origin main` (65 commits). CI will run for the first time on a real runner: the `vendored` and `desktop-panel` jobs and the
   pinned-Hermes jobs have never run there, so expect to fix something small.
2. Engine: `git push origin main` (the engine's local commits; `benchmarks/squeez_eval.py` is untracked and left out).
3. Catalogue PR to `NousResearch/hermes-agent` (`plugin-catalog/tameru.yaml`: `sha` = the plugin merge commit, `version: "1.9.0"`), from the plugin
   owner, per `docs/RELEASING.md` section 3. That one needs a human at Nous.
4. Upstream requests, one at a time, from `docs/UPSTREAM-REQUESTS-DRAFT.md`.

## Known open items (none blocks a push)

- Small windows (64K): `refactor` +9% and small results +3.9% against stock; documented in the README.
- `net_estimate` and `stats_ledger` are built and tested but not turned on in any live chat.
- Feature deletions (the deprecated list in the changelog) wait for your decision.

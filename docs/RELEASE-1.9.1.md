# 1.9.1 release state (2026-11-12)

1.9.1 answers the Hermes plugin-catalogue review of 1.9.0. The earlier readiness note for 1.9.0 is [RELEASE-1.9.0-READINESS.md](RELEASE-1.9.0-READINESS.md) (historical).

| Item | Value |
|---|---|
| Plugin | version 1.9.1, `main` at `b4429d7` (CI green) |
| Engine | 1.9.0, unchanged; the vendored copy still matches it |
| Hermes tested on | `3b421578` (v0.23.1) |
| Gate report | [gate-d71b7e46310c.json](gate-reports/gate-d71b7e46310c.json): stub 2149 passed, real Hermes 3346 passed (55 expected failures), Desktop panel tests passed, clean tree |
| Catalogue | [NousResearch/hermes-agent#153033](https://github.com/NousResearch/hermes-agent/pull/153033) pins `b4429d7` / 1.9.1; merged 2026-11-12 (Hermes commit `302f89e`), so 1.9.1 is the listed version. Tag and GitHub release `v1.9.1` point at `b4429d7` |

## What changed

See [CHANGELOG.md](../CHANGELOG.md) section 1.9.1: per-chat records move to Hermes' public `ctx.state` (90-day, 400-chat retention; 1.9.0 records are not migrated),
the panel uses Hermes' `Tip`, the Hermes home is read through `hermes_constants`, and the licence is Apache-2.0.

## Known limits

- The tooltip change has been exercised in the panel tests but not yet looked at live in Hermes Desktop.
- `ctx.state` has no multi-step transaction: two Hermes processes saving the same chat at the same instant can lose one of two updates.
- Measurements are those recorded on 1.9.0 ([COST-TUNING-2026-11-10.md](COST-TUNING-2026-11-10.md)); 1.9.1 does not change what is pruned.

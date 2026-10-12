# Tameru plugin 1.4.1 source audit — 12 October 2026

## Scope and identity

The standalone plugin's package, manifest and vendored engine identify 1.4.1. The engine source is `e7e32866ded56071af13fd5fb91a2214c1b3c018`; Hermes compatibility is pinned to `a0a49926f44c231f3456faffeaa4b1cc8ee4274c`. GitHub tag/Release publication is still pending.

This plugin uses Hermes' per-result demotion seams, recovery tool and artifact ledger. Hermes PR #94102 has a separate, simpler in-tree adapter; its test counts and integration behaviour must not be conflated with this plugin.

## Verification

- Upstream engine: 1,168 passed / 3 skipped, Python 3.12; battery 15/15; all five budget ratios retain required gold with zero forbidden leaks.
- Standalone plugin, stub Hermes: 1,125 passed / 268 skipped, Python 3.12. Skips include real-Hermes-only coverage.
- Standalone plugin, real upstream Hermes at the pin: 1,386 passed / 5 skipped, Python 3.14. Includes loader, configuration, recovery, seam parity and deterministic session-replay checks.
- Existing replay metrics and baseline comparisons remain unchanged; baselines were not blindly regenerated.
- Ruff 0.15.10 and compilation checks pass. Wheel and sdist build; wheel metadata, manifest, engine version and packaged modules agree.
- Vendored-copy drift check passes. CI pins the same Hermes revision and installs the declared plugin test dependencies in both real-host jobs.

## Corrections

Version/provenance claims were stale; metadata and current documentation now identify the reviewed upstream source. The real-host parity helpers were refreshed to match current Hermes annotations/docstrings without relaxing the parity assertions. The import-safety test previously included constructor effects in its import-only measurement; its measurement now ends before construction.

To avoid an invalid comparison, real-plugin tests ran against upstream Hermes without the competing in-tree Tameru adapter from PR #94102. The PR adapter was tested separately through its discovery path.

Historical live-session and performance observations remain in REPORT-1.4.1.md with their original scope. Passing fixture suites does not guarantee model answer quality or compatibility with future private Hermes API changes.

# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semantic versioning.

## [Unreleased]

### Changed (cost tuning, gauntlet round 1)
- Tameru lists one tool, `tameru_read_original`. The old name `tameru_expand` is still answered by `handle_tool_call` but is no longer
  listed, so the same ~1,600 characters are not sent twice on every request. The tool description and its parameter text are shorter
  (1,627 to 1,232 characters); the trigger sentence and the credential note are unchanged.
- Adaptive full compaction prunes results from 1,000 characters (it used 8,000, the proactive pass's floor), so the summarizer reads stubs
  instead of raw rows. This was the largest adaptive loss: smallrows at 32K went from +30.9% to about +10% against Hermes as shipped,
  and refactor, i18n and the default scenario now cost less than stock at 32K. A floor of 200 lost a recall fact in the oncall gate; 2,500 and above brings the loss back.
- The balanced preference keeps less detail per pruned result (0.67 to 0.50).
  Cost of the two changes, measured: oncall retains fewer gold facts than before (12 of 20 at 64K, was 17; 9 at 32K, was 13) but still more than Hermes as shipped (8 and 4),
  and costs less; the default scenario at 32K retains more (11, was 8). Every retention gate against stock passes.

### Testing
- `tests/eval/test_cost_matrix.py` (set `TAMERU_COST_MATRIX=out.json`; `TAMERU_COST_SCENARIOS`, `TAMERU_COST_WINDOWS` and `TAMERU_COST_ARMS` narrow it) measures every scenario, window and arm.
- Baselines re-recorded; three strict known-loss entries (refactor and i18n at 32K, i18n at 48K) and the default-session cost xfail removed because they now pass.

## [1.9.0] - 2026-11-09

### Changed
- The name is `Tameru (ためる)` everywhere: the Hermes plugin list, the engine's display name, the Desktop header, the
  READMEs and the docs (the manifest, engine and README previously said `貯める`). The engine's identifier stays
  `tameru`, which is the value saved as `context.engine`. The manifest description now starts with the name, which is
  what Hermes' engine picker shows beside the identifier.
- One solid look for everything that floats or sticks. The hover cards, the table's sticky header and the lean-mode
  dropdown list now use one opaque near-black with light text, in a light or a dark app alike, instead of theme tokens
  that could be translucent and clash with what was behind them. (A first version guessed light or dark from the page
  and drew white cards on Hermes' dark Desktop, which a live check caught; nothing is guessed now.) The note beside a source also uses the hover card now, so no native tooltip is left in
  the panel except the line-number gutters.
- **Adaptive host settings.** `compression.threshold` and `compression.protect_last_n` are now the starting point and
  guard rails, not fixed values. Per chat, Tameru nudges the effective summary threshold (within 10 points under to 15
  over your setting, never past 85% of the window, never raised while the cache is cold or soon after a Hermes summary)
  and the protected tail (half to double, 6 to 60 messages, wider when old outputs are revisited, via re-reads,
  Restore and Keep). It reads numbers only, never message text, and never writes `config.yaml`; Hermes keeps its own
  floors and caps. Each visible change gives a plain-language reason, and the memory hint shows "Tuned for this chat".
  Off with `adaptive_host: off` / `TAMERU_ADAPTIVE_HOST=off`, or when Tameru is paused or not in adaptive mode.
  Two numbers are saved per chat (`tameru_host_v1`).
- **Applied now means the provider accepted the request.** A Keep or Restore reads Pending until the `post_api_request`
  hook reports success for the request that carried it (the older "request started" signal was too early). Before that,
  the request Hermes built is checked to contain the restored text; if it does not, the edit reads Failed ("Not in the
  request"). A request that failed for good reads Failed ("Not sent"); one that Hermes will retry stays Pending. Edits
  whose request never got an answer are counted in the Health line ("edits not confirmed"). The hint says what was sent,
  never that the model used it.
- **Last reply timing and new input.** The memory hint shows how many tokens were new in the last reply, how long the
  call took, and, only when Hermes streamed it, when the first part arrived (a first chunk, not a first token; server
  prefill is not exposed and is not estimated). Numbers only; additive fields, so the panel schema is unchanged.
- **Same-named files can be told apart.** The per-turn note shows a parent directory beside a file name only when two
  different files share it (`src/index.ts`, `lib/index.ts`); the directory is cleaned and capped like a name.
- **The artifact trail marks a file that changed under the agent.** If two reads of a file differ and the agent did not
  write it in between, its line ends `(changed since earlier read)` (carried through later compactions, cleared when the
  reads agree again). It is only what Tameru saw: a command such as `git checkout` never marks anything by itself.
  When the trail has to drop lines, failing files, changed files and commands that did not exit 0 are the last to go.
- **Pinned instructions.** `/tameru pin TEXT`, `/tameru unpin N` and `/tameru pins` keep up to five lines you wrote word
  for word in every Hermes summary (a fixed `## Pinned by you (Tameru, verbatim)` section added after the summarizer
  runs, replacing an earlier one, so it cannot be softened or dropped). Nothing is inferred; credential-looking text is
  refused; the verbs work on your own clients only; switch off with `pins: off` / `TAMERU_PINS=off`. Saved per chat
  (`tameru_pins_v1`). The Desktop panel has a pin button in its header that opens the list (add, remove, "n of 5").
- **Desktop's engine list can read "Tameru (ためる)".** Desktop labels each engine from the saved `context.engine`
  value and Hermes loads it from a plugin folder of the same name, so the label changes only if both change together.
  `scripts/use_display_name.ps1` does exactly that (plan first, `-Apply`, `-Revert`): `plugins/tameru` becomes
  `plugins/tameru_(ためる)` and `context.engine` follows, with `config.yaml` backed up and the old folder kept in `plugin-backups/`.
  The plugin's id stays `tameru` (it comes from `plugin.yaml`), so `plugins.enabled`, `plugins.entries.tameru` and the
  panel are untouched. `scripts/rollback.ps1` restores into whichever folder is installed, and now keeps its plugin
  backup in `plugin-backups/` and follows the renamed Desktop folder. A backup left inside `plugins/` or `desktop-plugins/` is loaded as a
  second copy (a leading dot does not hide it from Desktop), and Desktop then refuses the new panel as a duplicate id.
- Desktop panel build number is 4 (Desktop and the plugin warn "Out of date" if they differ).

### Fixed
- The emergency switches now work as documented: `TAMERU_EDITS=off`, `TAMERU_MANIFEST=off`, `TAMERU_AUTOTUNE=off` and
  `TAMERU_ADAPTIVE_HOST=off` were silently ignored because only `TAMERU_HERMES_<NAME>` was read. The short names are
  accepted for these four; the long name wins if both are set.
- The artifact trail no longer quotes text a stranger may have written as if Tameru had checked it. Errors are taken
  only from commands the agent ran (not from files it read or searched), and any line that reads like an order to the
  model ("ignore previous instructions", "you must run ...", "Tameru verified ...") is left out and only counted
  (`/tameru` Health: "order-like error lines left out").
- Credentials typed into a command line are redacted in the trail: `-pSECRET`, `-u user:pw`, `PGPASSWORD=...`,
  `*_TOKEN=...`, `Authorization:` headers, `--password`.
- Keep and Restore need a reference of at least 8 characters that matches exactly one output.
- A forced loop of `tameru_read_original` calls can no longer widen the protected tail: a re-read of the same output
  counts once per 5 turns and the revisits counted per step are capped.
- `scripts/rollback.ps1` and the runbooks point to `v1.8.0` (tagged locally); `v1.6.0` remains the last published release.

### Testing
- Test runs can no longer change a real Hermes install: Hermes' bootstrap used to "finish a source update" (install
  dependencies, relaunch) when a test imported it, which made 25 tests fail and took a minute; the suite now sets
  `HERMES_DISABLE_LAZY_INSTALLS=1`, so the same tests run in seconds.
- The evaluation harness runs each replay with an empty learned-prior folder. Before, the first run's cross-chat learning
  leaked into the second run of the same engine, so the "two runs are identical" check failed for the plugin.
- Three expected-loss entries that now pass (strict xfail) were removed, and a real-Hermes policy-migration test was
  updated to the current default (adaptive everywhere). The saved baselines no longer match this Hermes (even the
  stock-Hermes ones differ), so they need re-recording against the Hermes version they are meant for.
- New replay arms `tameru_1_9_host_off` / `tameru_1_9_host_on` and `tests/eval/test_host_tuning_replay.py`: tuning Hermes'
  threshold and protected tail must not lose gold facts, break pairing or raise cost. On a 64K window Hermes' own floor
  makes the threshold percent irrelevant, so this guards side effects only; the threshold itself needs a larger window.

### Audit fixes
- A repo audit ([docs/PONYTAIL-AUDIT-2026-11-09.md](docs/PONYTAIL-AUDIT-2026-11-09.md)) found three real problems in this release's own
  work, now fixed: the credential and order-like filters hid ordinary commands (`ssh -p2222`, `MAX_TOKENS=4096`,
  `--token-limit 5`) and dropped ordinary error lines ("next step failed", anything naming the plugin); the install scripts left
  backups inside `plugins/` and `desktop-plugins/`, where Desktop loads them as a second panel and refuses the new one
  ("duplicate id tameru"), so backups now go to `plugin-backups/`; and `rollback.ps1` looked for the Desktop panel in the old
  folder name. One dead method was removed. The Keep/Restore, pin, host-tuning and usage methods moved out of `engine.py` into
  `engine_edits.py`, `engine_host.py` and `engine_usage.py` (verbatim), errors those methods swallow are now counted and shown
  as "errors ignored" in the Health line, and a Hermes module that cannot be imported is no longer retried on every call
  (remembered for five seconds). The vendored engine now includes four steps of `compress_context` moved into their own functions (engine commit
  `497872d`; the output is byte-identical on a 5,616-case capture). The other findings (the remaining long engine functions,
  replay captures in git) are listed in the report.

### Documentation
- New [docs/REPOSITORIES.md](docs/REPOSITORIES.md), and a matching guide and README section in the engine repository,
  state the split: the engine is harness-agnostic and has no dependency on Hermes; this plugin vendors it unchanged
  and owns everything Hermes-specific (adaptive policy and self-tuning, Restore and Keep, the per-turn note, the
  Desktop panel, `/tameru`, chat-surface gates). Where a change belongs and how releases flow between the two are in
  those guides.
- Version 1.9.0, vendoring engine 1.9.0 (no engine behaviour change).

## [1.8.0] - 2026-11-09

Tameru 1.8.0 is clearer and calmer in Desktop, lets you put a shortened output back in front of the model (Restore and
Keep), tells the model what is shortened, tunes itself across chats, and closes the security gaps found in review.

**About 1.7.0:** it was never tagged or published. It stayed a release candidate in both repositories, so everything it
contained is first released here (its section below is kept as the record of what it was). 1.8.0 also carries the
engine fixes made after that candidate (source associations across structured compaction, YAML dependencies and Git
note boundaries). The vendored engine is 1.8.0, and `plugin.yaml`, `pyproject.toml` and the engine agree.

**Upgrading:** new settings, all on by default and each switchable off: `autotune`, `manifest`, `edits` (and
`policy_mode` now defaults to `adaptive`). New small state is kept per chat in its own session metadata
(`tameru_tuner_v1`, `tameru_edits_v1`) and one profile-wide file, `$HERMES_HOME/tameru/learned.json` (five numbers).
Going back to an older release ignores all of it. See `docs/RUNBOOKS.md` for incidents and rollback.

### Added
- Self-tuning cleanup (`autotune`, default on in adaptive mode): bounded, self-reversing adjustments from four live signals, persisted per chat. The user choices are one bias (Save space, Balanced, Preserve detail) and Pause.
- Compression-progress snapshot (`/tameru --json` schema 3: gauge, savings, plain-language status, tuner posture and recent adjustments, timeline) and a text view of it for the CLI, TUI and messaging.
- `/tameru changes`, `/tameru bias`, `/tameru pause` and `/tameru resume`; `/tameru policy` remains.
- `post_compaction` hook so the tuner learns when Hermes compacted ahead of Tameru.
- Compaction status line carries a short Tameru clause; the post-commit chat line reports tokens freed.
- Activity table rebuilt after a restart, or on the first `/tameru`, from rows an archived original proves for the same message id; archive reads now include superseded originals (`include_inactive`) so this works on current Hermes.

### Security
- Every original now passes one fail-closed `screen_original` gate before it is shown, restored or handed to the model. Originals read back from Hermes' session archive were not screened for credentials before; View original, `tameru_read_original` and the Desktop viewer now refuse them ("Held a secret"). A test forbids reading an original any other way.
- `/tameru view`, `show`, `policy`, `bias`, `pause` and `resume` only run from Desktop, CLI, TUI or ACP. In a messaging chat Hermes passes no sender, so any member could previously read raw tool output; they now get a one-line refusal. `/tameru` and `/tameru changes` still work everywhere.
- The Desktop **Add** action quotes text in a code fence one backtick longer than any run inside it, so quoted output cannot close the fence and put instructions into the message.
- The profile-wide learned prior moves at most 0.12 per offset per UTC day, across all chats.
- The telemetry log only writes under `$HERMES_HOME` or the system temp folder (no network or device paths) and rotates at 5 MB, keeping one `.1` file.

### Added: getting back what was left out
- Desktop **Left Out** viewer: removed stretches fold into expandable bars, lines are selectable, with **Add** and **Re-Read** (the main action follows size and trust: untrusted web/MCP text and large selections default to the re-read, and adding is blocked for them).
- `/tameru show REF` and `/tameru ask REF [START-END | term]` on every surface.
- A short recovery note in each new session prompt and a tool description that leads with when to call `tameru_read_original`.
- Each recorded change keeps fingerprints of the lines it kept (no text), so a viewed page can mark what was left out.

### Added (Keep and Restore)
- **Restore** and **Keep** on each row of the Desktop list, and `/tameru restore REF`, `keep REF`, `unkeep REF`, `cancel` on Desktop, CLI and TUI (never from a messaging platform). They put the full original of an output Tameru already shortened back in front of the model: Restore for the next five replies, Keep until you unpin it (and across a restart). A change waits for your next message, so a batch costs the prompt cache one rebuild.
- It works on the request that goes to the model (`select_context`), never on saved history. A row is only replaced when it is provably the one Tameru shortened (same message identity and bytes); web content can only be re-read; an original that held a credential is refused; the total it adds is held under 15% of Hermes' compaction point (at most 40k tokens), and a Restore or Keep is refused ("Memory nearly full") when it would push the chat past that compaction point, because a Hermes summary cannot be undone. The same check runs on every request, so an edit that no longer fits reads Failed instead of triggering a summary. Any error leaves the request exactly as Hermes built it, and three errors switch edits off for the chat.
- A state reads Pending until the request that carried it has started, then Applied (or Kept); a change that could not be applied says why (Failed), and a finished Restore reads Expired. `edits: off` / `TAMERU_EDITS=off` disables the feature.
- Not in this change: restoring text that a Hermes summary already folded away (the row is no longer in the request, so it reports "Not in this chat's history"), a Restore button inside the viewer, and a second-click confirm for web content (web content is simply refused).

### Added (what the model is told, and real usage)
- A short per-turn note to the model (`pre_llm_call`, `manifest: on` by default, `TAMERU_MANIFEST=off` to disable): how many tool results are shortened and the base names of files it wrote or patched, in a `<tameru_note>` frame that says it is data, not instructions. It carries structure only (never tool output, commands or full paths), is cleaned and screened for credentials, opens by replacing earlier notes, is sent only when it changed (and again after a Hermes summary), and is capped at about 3,000 characters per session.
- The provider's own numbers for the last replies (`update_from_response`): the snapshot's new `usage` block carries the prompt size, cache read and write, and the share of the prompt read from cache. The gauge hint says "Reused N% from the last reply" only when the provider reported cache use; a provider that never does shows nothing rather than 0%.

### Changed: the Desktop panel and defaults
- Desktop panel is slimmer and calmer: a 30px header (title 12px with a lighter `(ためる)`, borderless lean-mode dropdown, 4px gauge with "N% full", "Saved ~N%"), silent while everything is fine, over a fixed 196px body. Wide panels show the list and the viewer side by side; narrow ones swap in one pane. An idle chat is a single line.
- The viewer is one toolbar row: close ×, "Left Out", source and range, **Add**, **Re-Read**, page arrows and a plain "Not sent until you send". Back stays available while an original is loading or has failed to load. Keys inside the viewer: R, A, `[`, `]`, Enter, Esc.
- Every control explains itself in a small hover card (hover for 350ms, or keyboard focus; Esc closes it). It is drawn on the page body so it can never add panel height or be clipped by the panel's CSS container; the same text is always available to screen readers.
- Copy follows a budget (short buttons and status text, no "tokens" or "prompt cache" on the surface) and the table no longer prints a doubled sign ("~−2,984" is now "−2,984", with the estimate noted in a hint).
- Each recorded change carries who made it, a short reason, characters saved and whether it can be restored; snapshot schema 3 adds `fill_pct` and `saved_pct` and marks timeline events as Tameru or Hermes. `restored` was renamed `recovered_after_restart` so "restore" keeps one meaning.
- Desktop table is slim (time, source, lines, reduction, tokens reduced, status, actions) with an expandable detail line per row.
- Desktop header reads `Tameru (ためる)`, and the exact tokens reduced have their own column next to the reduction percentage.
- Adaptive is now the default policy mode in every configuration, including `context.engine: tameru` on its own (it used to resolve to legacy). Set `policy_mode: legacy` to keep the old policy.
- Self-tuning now learns across chats: adjustments and calm stretches update a small bounded profile-wide memory (`learned.json`), and new chats start from a damped share of it.
- Desktop panel redesigned as a single condensed card above the composer: a header (lean mode, status sentence, usage gauge with Tameru and Hermes ticks, tokens saved, average pass time, options, collapse) over one table of passes, Hermes summaries and restored outputs with token counts, percentage and exact token reduction, mode, status, duration and View original. It polls while collapsed and reacts to Hermes compaction events. The Desktop plugin is now on by default.
- `/tameru` leads with compression progress; the changes table moved to `/tameru changes`.

### Documentation
- The README replay table, the catalogue page's "Measured" table and its 451-line log example were re-measured on 1.6.0 (2026-11-05); the per-result limits line now quotes 68.6% against the engine's 82.3%.
- Compatibility notes that Hermes `main` at `b63eb8b` fails 33 tests (replay baselines, a seam parity check, one archive-backed `tameru_expand` test); the CI pin and `1298c8e` still pass.

## [1.7.0] - 2026-11-05 (release candidate; never published, first released in 1.8.0)

- Bundled `/tameru` activity table and compatible Desktop composer panel; read-only original viewer with session/profile/lifecycle checks; clearer `tameru_read_original` alias with `tameru_expand` compatibility; live retained/changed/removed status; brief automatic notices.
- Real discovery registry sharing across module namespaces and profile config isolation (implicit process-environment overrides apply to the launch profile only).
- Bounded provenance receipts: copied or modified header refs cannot borrow another row's cached original or metadata.
- Recovery metadata credential/size screening; vendored atomic structured-format fixes.
- Inspection does not restore model context; restoration and "Keep" remain deferred because the host exposes no supported persistent rewrite transaction. (1.8.0 delivers them as an overlay on each request, never as an edit of saved history.)

## [1.6.0] - 2026-11-04

Summary by area (details below):

- **Vendored engine:** Tameru 1.6.0, with reducers for many more tool outputs (Linux, macOS and Windows tools, containers, builds, package managers, service logs, test runners), anchors and summaries for generic and empty queries, multi-language query handling and sentence splitting, and a private regex cache.
- **Catalogue page and docs:** a user-facing catalogue README with a hero banner, and plainer `tameru_expand` and `plugin.yaml` descriptions.
- **Fixes:** vendored engine fixes for progress-bar false positives, numeric terms in clock times, output longer than input, Prometheus metrics and Indonesian ordinals.

### Added
- Vendors error-anchored compression for generic queries that match little or nearly everything and small erroring logs.
- Vendors structured log levels as error lines and weak failure anchors when there are no error lines.
- Vendors test-runner reducers for Gradle, RSpec, PHPUnit, dotnet test, Maven Surefire and quiet pytest.
- Vendors kubectl `describe` and `get events` reducers, with broader diagnostic recognition.
- Vendors text tool-output collapse, the status-table reducer and dashboard summary anchors.
- Vendors JSON array summaries, no-query anchors for quiet logs, the docker build reducer and short-line folding.
- Vendors CSV/TSV outlier rows, logfmt log routing, new-key anchors for structured logs and apt `E:` error detection.
- Vendors syslog/journal/dmesg routing and anchors, the git log reducer, JSON Lines summaries, CJK-safe prose repeats and Markdown table outliers.
- Vendors repeat-aware anchor regions, PHP error-log anchors, pytest banner shortening, kubectl Replicas and Conditions fixes, rare query-word anchors, multi-document YAML matches and slash-dated log routing.
- Vendors the lint and MySQL slow log reducers, multi-document YAML outlier summaries, nginx/Apache/MySQL/HAProxy error-log anchors and Node frame anchors.
- Vendors Redis log routing, the env/config dump reducer (secret-looking lines never kept alone), kubectl separator and describe-job fixes, MSBuild/tsc lint locations, collapsed-summary needle selection and weak-query diff file folding.
- Vendors the Terraform plan, bundler asset, Ansible and pip install reducers, short-syslog rare-line anchors and the restored collapse-run split.
- Vendors klog and web access-log routing with 4xx/5xx kept, cargo build and git status reducers, query-saturated template collapse, the gradle task label, and the df snap-loop fix.
- Vendors the npm/yarn/composer, ps aux and docker ps reducers, jest output without a summary, and go package folding.
- Vendors repeated-sentence folding for prose in any script, accented query words kept whole, and the pip freeze and npm audit reducers.
- Vendors the sentence-level prose brief for any script, multilingual query stop words and generic asks, negation-aware and fold-aware query matching, and the Playwright, top and CMake reducers.
- Vendors the npm ls, curl -v, rollout status, systemd-analyze blame, all-zero table row and ping reducers, failed-build asset folding, snake_case query matching and more filler-only summary words.
- Vendors the key/value line segmentation and the progress, traceroute, openssl s_client, PowerShell table and error, msiexec log, netstat, ipconfig, numbered-list and nmap reducers.
- Vendors the private regex cache with its guard test, and the pg_settings, macOS unified log, docker compose ps, vmstat, apt upgradable and labelled progress reducers.

### Changed
- **Catalogue page and README.** `hermes_tameru_plugin/README.md` (the README the Hermes plugin catalogue renders) is
  rewritten for users: what the plugin does, a real before/after example, the `tameru_expand` tool and what to know
  before installing. A hero banner (`docs/assets/hero.png`, 2:1) heads the repository README.
- **`tameru_expand` description.** Opens with "Tameru Expand (展開, tenkai: unfold)" and says in plain words that it
  brings back the exact text of a shortened tool output. Arguments and behaviour are unchanged.
- **`plugin.yaml` description** reworded for people browsing plugins.

### Fixed
- Vendors the fix that kept ratio lines such as `20/40 degraded` from being dropped as progress bars.
- Vendors the fix that keeps docker `Step N/M` lines.
- Vendors the fixes for numeric query terms matching clock times and for output that could be longer than the input.
- Vendors the fixes for Prometheus metrics read as Markdown and Indonesian ordinals in prose briefs.

## [1.5.0] - 2026-10-13

Summary and measurements: [docs/REPORT-1.5.0.md](docs/REPORT-1.5.0.md). Release steps: [docs/RELEASING.md](docs/RELEASING.md).
Vendors Tameru 1.5.0 (`933aa33`). Verified against Hermes `a0a4992` (CI pin), `63d0b94` and `6e7b83a2f1` (main, 2026-10-13).

### Fixed
- **The proactive prune ran on no window under ~64K.** The plugin's trigger is `max(48,000, 0.75 x threshold)`, and a
  32K window's compaction threshold is 27,200 tokens: the trigger was above the window, so the prune never fired
  (`prune_commits` 0 in every 32K replay cell). Where the result is not below the threshold the trigger is now
  `small_window_prune_ratio` (0.60) x threshold (16,320 at 32K); a 64K window and up are unchanged. The regrowth rule
  applies there too, and the re-prune target gate (`reprune_target_ratio`) does not: the fixed part of a 32K prompt is
  ~0.3 of the threshold, so the gate blocked every re-prune. Replay on a realistic harness (simulated clock, context
  overflow path) against Hermes as shipped, 32K, seeds 0 and 1: turns that fail with a context overflow
  default/oncall/refactor/i18n 6/6/4-5/17 -> 0 (Hermes as shipped: the same counts), compactions 3/6/4/2 -> 1/2/1/2, gold
  facts 6/12/7-10/12 -> 11/14/14/14, cost per answered turn -5..-6%/-37%/-13..-17%/-19% against Hermes. The 48K cells
  gain gold (default 6 -> 12, oncall 12 -> 17-18, against Hermes' 5 and 6-10) at -9..-10% cost on default, -38% on oncall,
  +3..+4% on refactor and -4% on i18n.
  Smallrows (nothing prunable) is unchanged: +8.9% at 32K.

### Changed
- **A prune is followed by the next prune after a regrowth of `prune_regrowth_ratio` (0.20) of the threshold, not Hermes' full
  trigger-sized runway (0.7), and only if it leaves the prompt at or below `reprune_target_ratio` (0.30) of the threshold.**
  Hermes' runway outlasted the stretch to the compaction a prune is meant to replace, so a 128K-256K session regrew raw
  rows for the rest of its life while stock Hermes compacted and reset (replay vs Hermes as shipped: oncall 200K +23.7%
  cost, default 128K +4.0%; now -9.0% and -5.6%, with more gold facts and every error template). Window-scaled trigger
  only; small windows, a user's `compression.proactive_prune_tokens` and an explicit argument are untouched.
- **`default_proactive_prune_ratio` 0.70 -> 0.75.** With the later prune following on regrowth (above), the first prune can
  wait: on the 200K replay (Hermes as shipped never compacts; peak 0.731 of the threshold) the plugin no longer prunes at
  all and keeps 17/17 gold facts instead of 14/17 (that cell was -9.9% cost, now identical to Hermes as shipped), for
  1.5-2.8 points of cost on four 128K/200K cells that remain 4-19% below Hermes as shipped. Set 0.70 to restore the old trigger.
- **The plugin's proactive-prune trigger follows the window.** Unless you set `compression.proactive_prune_tokens`,
  it is `max(default_proactive_prune_tokens, default_proactive_prune_ratio x Hermes' compaction threshold)` (48,000
  and 0.75), re-derived when the model changes: 48K on 32K/64K windows, 112.5K on a 200K window. The fixed 48K trigger
  pruned large windows long before they filled (200K replay: 13/17 gold facts against 17/17 for Hermes as shipped).
- **New `errors` rung.** A row past the retained-extract budget, or in the pressure pass, keeps one exemplar line per
  error template under its header instead of dropping to a bare header (`retained_error_budget_chars` 8,000,
  `errors_row_chars` 1,600; `0` turns it off). On 128K-200K windows the plugin had kept 0-1 of 7 error templates;
  now 7/7 at every window measured. The header grammar gains the rung; older rows still parse.
- **Small windows keep less.** The retained-extract and retained-error budgets shrink in proportion below a 64K
  window's compaction threshold (`small_window_threshold_tokens`, 54,400): 3,000 and 4,000 chars at 32K. At 32K the
  fixed sizes were twice the share of the context, the post-compaction context came out bigger and the plugin
  compacted sooner (replay, i18n at 32K, four seeds: 20 compactions against Hermes' 8; now 8, with 56 gold facts
  against 48). From a 64K window up nothing changes.
- **The summarizer and Hermes' anchor index read a Tameru row as Hermes would.** A header-only row is cut to Hermes'
  own one-line summary and every other rung loses its `ref=` (`render.for_summary`, applied in
  `_serialize_records_for_summary` and `_augment_summary_lean`; the transcript is not touched). Each ref used to land
  in the summary's "commits:" anchor list (16 hex chars read as a commit hash, ~18 chars each, unlabelled, ahead of the
  real commit ids), and each header-only row was 2-4x Hermes' line in the summarizer prompt (replay, smallrows at
  32K: summary prompts 199,109 chars, now 166,006; Hermes 112,113).

### Added
- Replay: a `stock_defaults` engine (Hermes as shipped, proactive prune off) with a baseline and acceptance test,
  `run_session(context_length=...)`, and large-window tests (128K, 200K, 256K).

### Corrected
- Cost claims against "stock Hermes" (about 20-22% cheaper) were measured against Hermes with the proactive prune
  opted in at 48K. Against Hermes as shipped the replay is 5.7% cheaper in chars and 7.1% in o200k tokens, with
  13/17 gold facts against 6/17 and 7/7 error templates against 0/7 ([REPORT-1.4.1.md](docs/REPORT-1.4.1.md) §9).

### Fixed (engine, Tameru 1.5.0)
- Quadratic time on blank-line runs and on long single-word or many-term queries (over 20 s each; now milliseconds);
  a crash on a lone surrogate in the query; the receipt no longer copies the whole input (credentials on dropped
  lines included); `contains_secret` knows 70 of 80 common credential shapes (29 before), which the recovery store and
  the artifact trail rely on; weak (plain-word) queries keep diffs in whole hunks and keep grep definition lines.
- About 4.6x faster on the per-result call sequence (outputs byte-identical; the real-Hermes suite runs in ~80 s
  instead of ~150 s with more tests). Same-text memoization is bounded (2 KB to 1M chars).

### Fixed (engine.py, serving red-team)
- No model-catalogue lookup until Hermes has sized the window; a disabled engine keeps the built-in compressor's
  Codex threshold autoraise (the kill switch is now exactly stock); the extract memo is bounded by characters (4M) as
  well as entries; an unreadable `plugin.yaml` no longer raises in `get_status`. On hosts that size the window
  eagerly (no `_resolved_context_length`), the trigger is scaled straight away.

### Added (replay harness)
- **Realistic replay.** A simulated clock (20 s per request) so Hermes' 300 s no-op and anti-thrash backoffs lapse
  as they would in a live session, and a context-overflow path mirroring `agent/turn_overflow.py` (a request over the
  window is rejected unbilled and forces a compression, three attempts per turn, then the turn fails). New metrics:
  `overflow_errors`, `failed_turns`, `max_request_window_ratio`, cost per answered turn. The previous harness billed
  32K requests up to 2.1x the window, which made 32K look 17-28% cheaper than it is; 64K and larger windows are
  identical on both harnesses. `--old-harness` reproduces the old baselines byte for byte.
- 32K and 48K acceptance tests (`tests/eval/test_small_window_replay.py`), large-window tests, a kill-switch
  byte-parity test against stock, serving tests (isolation across 12 concurrent agents, fault injection, untrusted
  wrappers, redaction, session rotation).
- `hermes_tameru_plugin/README.md` for the Hermes catalogue page; `docs/RELEASING.md`.

### Contribution audit
- Resynchronise the vendored engine with the BOM-tolerant CLI/cache/recovery reads; upstream now passes 1,172 tests with 3 skipped.

- Catalogue-admission validation passes all 14 checks, including safe security scan, capability matching and no runtime core override.
- Revise Hermes PR #94102 towards a catalogue entry pinned to the verified standalone plugin. The in-tree vendored engine exceeded Hermes code-health limits; the plugin source stays in its own repository.

### Fixed (serving red-team, round 3)
- **Web, browser and MCP results keep Hermes' untrusted-data wrapper.** The `errors` rung (and any body the ladder
  keeps) of a `web_extract` / `web_search` / `browser_*` / `mcp_*` result sat outside the wrapper, where stock Hermes
  keeps no page text at all; `tameru_expand` handed such originals back unwrapped; the artifact trail copied their error
  lines into the summary. Now bodies are re-wrapped, expand answers are wrapped whole, and the ledger skips them.
- **The artifact trail is redacted** by Hermes' own compaction redactor like every other section it appends (URL
  credentials, API keys that the plugin's probable-credential filter does not know).
- **`tameru_expand` finds a ref after a compaction that rotated the session** (the originals stay in the parent
  session; the archive read now covers the compression lineage).
- **Building the engine no longer looks the placeholder model `pending` up in the model catalogue** (a network call of
  up to 15 s per agent start when the catalogue is unreachable, plus a warning); `register()` hands the compressor a
  stand-in window until Hermes' `update_model` sizes it.
- A `grep` that needs a child process answers with a clear error when `sys.executable` is empty; a NaN, infinite or
  out-of-range `compression.threshold` no longer reaches `update_model`; `plugin.yaml` uses Hermes' `label` field.

## [1.4.1] - 2026-10-12

Source/package update; GitHub tag and Release pending. This includes the live-session,
red-team and fix-all changes measured in [docs/REPORT-1.4.1.md](docs/REPORT-1.4.1.md).
The engine is now synchronised with upstream 1.4.1; the full source commit is recorded in
`hermes_tameru_plugin/tameru/__init__.py` as `VENDORED_FROM`.

### Release consistency audit
- Align package metadata, plugin manifest and vendored `ENGINE_VERSION` at 1.4.1.
- Replace stale candidate/local-only provenance claims with the current upstream source identity.
- Verified: 1,125 passed / 268 skipped in stub mode (Python 3.12), and 1,386 passed / 5 skipped against real Hermes `a0a4992` (Python 3.14). Replay/baseline acceptance checks passed without re-recording metrics.
- Bump the CI Hermes pin to `a0a4992`; refresh verbatim stub helpers and the third-party attribution revision to match. Strict parity assertions remain enabled.
- The import-safety test now measures module-import effects before constructing an engine; Hermes constructor home/config initialisation is not a plugin-import side effect.
- Install the plugin dev extra in the existing real-Hermes CI jobs so YAML, packaging and fuzz coverage runs; pin checkout/setup-python actions to full SHAs.
- Ruff, build and wheel-content/version checks passed. See [AUDIT-1.4.1.md](docs/AUDIT-1.4.1.md).

### Fix-all round (same branches, see [REPORT-1.4.1.md §8](docs/REPORT-1.4.1.md))
- **`tameru_expand` grep can no longer hang the agent** (R4, open since 1.4.0). A pattern with a repeat or an
  alternation now runs in a child Python process (`-I -S`, stdlib only) that is killed after
  `expand_grep_timeout` seconds (default 2, `TAMERU_HERMES_EXPAND_GREP_TIMEOUT`, 0.05-600) with
  "grep timed out ... simplify it". `.*.*.*x` on a 3,000-char line (>275 s before) now returns that error after
  2.00 s (1.00 s at a 1 s timeout). Repeat-free patterns stay in-process. Lines are capped at `expand_grep_line_chars` (default
  20,000) before matching (`lines_capped` in the result). The nested-repeat pre-check is unchanged. Cost on a
  5,000-line original (median of 20): repeat-free 3.0-3.8 to 3.4-3.7 ms, child path 3.2-11.4 to 21-26 ms per call. Uses `subprocess.run` with a
  timeout (TerminateProcess on Windows); only tested on Linux.
- **Briefs honour the pass-2 reservation** (R5). The room left for the newer rows of a pass is now taken from an
  estimate of each row's own brief (`max(brief_chars, brief_share * size)`, one growth step for a row with an error
  fingerprint, capped at its extract cap) instead of a flat `brief_chars`, and it limits the brief as well as the
  extract. Reservation probe (6 orders x 4 budgets): the newest row keeps a body in 24/24 cases (7/24 before), no
  brief under 400 chars (6 before); a tight budget is used less fully in some orders (e.g. 2,321 of 3,000).
  Replay product arm: gold 12/17 to 13/17.
- **Secrets screen** (R3, engine `91f00e0`): a whole-line env/shell (`DB_PASSWORD=correct_horse_battery_staple`) or
  YAML (`secret: correct_horse_battery_staple`) assignment is a secret again, whatever the value looks like, unless it
  is an expression or placeholder, ends in `,` `;` `)` (a keyword argument on its own line) or, in YAML, is an
  attribute chain. Hermes probe: 15 of 2,372 non-test `.py` files before and after. Still not flagged: the same values
  inside code (`password = settings_default_password`), which cannot be told apart from code.
- **Brief rung inlines short gaps** (R8, engine `b391ed9`): `brief_context` honours `InliningGapMarker`, and the brief
  rung uses it, so a dropped line shorter than Hermes' ~100-char marker stays verbatim, as in extracts.
- **Spill check tolerates small format changes** (R1): the untrusted-wrapper header may carry any attributes in any
  order, other case, CRLF or trailing blanks, a 0-4 line notice, extra blank lines, or be nested up to 3 deep. The
  notice must still end in a blank line.
- **Refs are 64 bits** (R7): `make_ref` returns 16 hex chars. A pre-change ref is the first 8 of them; headers,
  `tameru_expand`, the store and the session-archive lookup accept both (an 8-hex ref that prefixes two keys is a
  miss). Committed rows keep their bytes. Cost: 8 chars per rendered header.
- Tests (R8): the `[read_file]` case of the bare-tool-name test now fails without the filter; the inline-gap test
  asserts at the brief rung too; `pyyaml`, `setuptools` and `hypothesis` are in the `dev` extra, so the direct
  `config.yaml` and offline packaging tests run.
- Measured against the red-team head `d8fd2d6`: replay `tameru_1_4` 13/17 at 1,019,153 to 13/17 at 1,019,675
  cache-priced; product 12/17 at 990,003 to 13/17 at 989,114; per-result 14/14 answers, 22/22 templates both; engine
  corpus through the plugin 67.5% to 67.2% saved (the longer refs). Baselines re-recorded.

### Red-team round (see [REPORT-1.4.1.md §7](docs/REPORT-1.4.1.md))
- **A spilled web/browser/MCP result was no longer recognised as a spill** (regression in this candidate). Hermes wraps
  `web_extract`, `web_search`, `browser_*` and `mcp_*` results in `<untrusted_tool_result>` after the spill, so the
  "starts with the tag" test missed them: they took the brief rung (~1k of the external preview) instead of the 1.4.0
  `spilled to <path>` header. `is_persisted_block` now looks past that wrapper header.
- **`/compress terminal` lost its focus on `terminal` rows** (regression in this candidate): the query de-duplicates the
  focus and the call name into one part, and the bare-tool-name filter dropped it. A part equal to the focus stays.
- **The recovery store refused ordinary source files** (regression from engine `173a948`): the new unquoted
  `KEY=value` secrets pattern matched code such as `password = get_password_from_vault()` (141 of 2,372 non-test
  Hermes `.py` files, 9 before), and the store never keeps an original with a secret, so `tameru_expand` could not serve
  them from the store. Engine `a1e8a7f` excludes code expressions (15 of 2,372 files, all literal fixture keys).
- Added `tests/test_fuzz_redteam.py` (hypothesis; skipped without it) and `tests/test_redteam_141.py`.
- Left open in this round, fixed in the fix-all round above: polynomial-time `grep` regexes in `tameru_expand`
  (`.*.*x` on a long line), briefs not covered by the pass-2 reservation, very small briefs (~250-330 chars) at the end
  of a tight budget.

### Fixed
- **A result that quoted `<persisted-output>` was taken for a Hermes spill.** A live 29.5K `git log` that mentioned
  the tag was reduced to the spill one-liner. A result is a spill only when it starts with the tag, or with Hermes'
  untrusted-data wrapper header followed by the tag (`hermes_compat.is_persisted_block`; the wrapper case was added in
  the red-team round). Hermes may append subdirectory hints after the closing tag, so "ends with" would be wrong.
- **Both answers 1.4.0 lost are kept** (per-result QA set, `tests/eval/per_result.py`: 12/14 answers in 1.4.0,
  14/14 now, 22/22 error templates both):
  - a pytest failure with a long captured log now takes the extract (the engine collapses the captured section);
  - a saturated `rg -n Name` keeps the definition line (the engine no longer treats `-n` as the query's selector,
    and one host marker covers a run of fully omitted files instead of one ~100-char marker each).
- **Replay regression in the 1.4.1 work, before release.** With the larger pytest extract, one old row used the
  whole retained budget in pass 2 (Hermes visits oldest first) and the newer rows fell to headers: `tameru_1_4`
  gold 13/17 went to 10/17. An extract in pass 2 now leaves `brief_chars` per later row that could still be
  rendered. Gold is back to 13/17 (equal gates) and 12/17 (product), as in 1.4.0.
- `tests/eval/compare.py` always uses a throwaway `HERMES_HOME` (a replay writes Hermes state). Opt out with
  `TAMERU_EVAL_HERMES_HOME=<dir>` or `TAMERU_EVAL_KEEP_HERMES_HOME=1`.

### Changed
- The extract query no longer includes the bare tool name (`terminal`, `read_file`): every row of a tool shares it,
  so it only selected incidental lines. Engine corpus through the plugin: mean 65.9% to 67.5% saved, `git_log`
  69.1% to 87.6%, gold 15/15 before and after.
- Short gaps are inlined when Hermes' ~100-char marker would be longer than the text it replaces (engine
  `inline_short_gaps`). Engine corpus: `travis_yaml` 13.5% to 23.4%, `jp_musubi` 26.5% to 29.4%.
- Product-arm replay cost: 986,072 to 990,003 cache-priced (+0.4%), baseline re-recorded.

### Added
- `tests/eval/per_result.py`: one demotion per agent output (rung, saved %, answers, error templates) and the
  engine corpus through the plugin and through the engine alone.

### Notes
- `tameru_expand` is only offered when Hermes injects context-engine tools: no `-t`/`enabled_toolsets` restriction,
  or one that includes `context_engine`. `hermes chat -t terminal,file` hides it.
- Newer Hermes logs `capability_check ... capability=tools.override decision=deny evidence=not granted` at every
  plugin load. That is Hermes probing the tool-override policy; it is not about `tameru_expand` and needs no action.

## [1.4.0] - 2026-10-06

### Fixed (QA round 2, same release; see [docs/REPORT-1.4.0.md](docs/REPORT-1.4.0.md))
- **Oversized results lost the answer.** An extract over `max_extract_chars` got one fixed-budget retry. Log, grep
  and pytest output cannot shrink under it, so those rows fell to a fixed 1,200-char brief. Over 13 agent outputs,
  5 of 14 answers and 6 of 20 error templates were lost. Now:
  - fixed-budget retries iterate;
  - a forced cut must keep one of the engine's top query-evidence lines;
  - the brief grows in steps up to the cap until it holds every error template.

  After the fix: 12/14 answers, 20/20 error templates. New opt-in `brief_share`.
- `tameru_expand` refuses catastrophic-backtracking `grep` patterns (`(x|x)*y` hung the agent).
- Supersession no longer hides a result that answered a different question:
  - quoted spacing differs (`grep "a  b"` vs `grep "a b"`);
  - the same relative command before and after a `cd`;
  - `ls -R` followed by `ls`.
- Telemetry no longer counts renders for passes Hermes declined to commit.
- The status message honours Hermes' suppress flag and the kill switch.
- A note quoting `[tameru:` cannot hijack header parsing.
- Engine re-vendored at `11b9e5e`:
  - test-runner reduction keeps errors printed around a run;
  - log templates no longer merge messages that differ by a quoted name or path;
  - zero-count failure lines keep error protection;
  - JSON fold is byte-exact;
  - recursion markers are found inside JSON payloads.

### Fixed (QA round, same release)
- **Benchmark parity.** The task query included the tool's own command (`cat report.txt`); the engine reads a
  file name that is absent from the output as "the answer is not here" and keeps the whole result, so 11 of 14
  cases of the engine's QA corpus came back uncompressed and fell to the query-blind brief, which kept
  distractors in 5 and lost the answer in 2. Query parts that name something absent from the output are now
  dropped (`query.usable_query`), and the defaults match the engine's own: `max_risk` `high`, `min_savings` `0.10`.
  `tests/test_benchmark_parity.py` runs the corpus through the plugin: every gold string kept, no distractor.

Rebuilt on Hermes' own demotion seam, on the 1.4.0 Tameru engine. The "G" numbers refer to the gap matrix in
[docs/compaction-research.md](docs/compaction-research.md).

### Breaking changes

- **Requires Python >= 3.11** (was 3.10), matching Hermes.
- **The post-check is gone.** 1.3.0 ran its own prune, called Hermes' summarizer, and returned the pruned
  transcript if the summary lost "query facts" or bulky tool content. 1.4.0 hooks Hermes' per-result demotion
  instead and no longer second-guesses Hermes' compaction (G3).
- **`prune_tail` default is `tokens`.** The proactive prune protects the shorter of `protect_last_n` and the full
  compaction's tail token budget, and also runs Hermes' pass 4, instead of protecting 20 messages whatever they
  weigh. Set `prune_tail: count` for Hermes' behaviour.
- **`ledger` is on by default**: Hermes' compaction summary gains an exact artifact trail section, including an
  `Errors:` block.
- **New tool `tameru_expand` appears in the model's tool list** (`expand_tool: false` removes it).
- **Pruned rows look different**: Hermes' one-line summary followed by `[tameru:<rung> kept K/N lines ... ref=xxxxxxxx]`
  and an optional compact meta line, instead of bare `[…]` gaps. 1.3.0-era rows in existing sessions are
  recognized and never re-extracted.
- `proactive_prune_tokens` defaults to 48,000 and `proactive_prune_min_result_chars` to 2,000 unless the user's
  config sets them.
- Packaging: the invalid `kind: context-engine` manifest entry is gone; `plugin.yaml` carries only the name,
  description, version, licence and homepage.

### Added

- **Per-result ladder** inside Hermes' demotion step: supersession, query-aware extract, structural brief,
  header-only, then Hermes' own line. Hermes' gates, tail protection, `archive_and_compact` commit and pairing
  repair apply unchanged (G1, G14).
- **Structural supersession** (`supersession`): stale reads after a later write/patch, repeated reads of the same
  path and range, repeated identical calls and old state snapshots become header-only rows that say what
  superseded them.
- **`tameru_expand`** recovery tool and `ref=` on every rendered row; originals come from a per-agent store
  (`store_max_entries`, `store_max_chars`) and from the Hermes compaction archive (G8).
- **Artifact-trail ledger** in compaction summaries (files, commands with exit codes, error exemplars).
- **Retained-extract budget** (`retained_extract_budget_chars`, default 6,000): older rendered rows step down to a
  header with their ref kept, only inside a pass Hermes is already committing (G15).
- **Configuration**: `TameruSettings` with environment (`TAMERU_HERMES_<FIELD>`), `ctx.get_config` and
  `plugins.entries.tameru.settings` layers, and a kill switch (G13). The user's `compression.*` section is mirrored
  into the compressor (G11).
- **Telemetry** in `get_status()["tameru"]` and an optional JSONL log (`telemetry_log`).
- Lean-tail support: tail rows Hermes stubs keep a Tameru header with their ref.
- Seam probe at start-up with a stock-behaviour fallback and a stated reason when Hermes' internals differ.
- Test suite (stub Hermes and real Hermes), a seam contract test, a deterministic 30-turn replay harness
  (`tests/eval`) with saved baselines, and CI (`.github/workflows/ci.yml`).
- `docs/compaction-research.md`, `docs/DEVELOPMENT.md`, and the upstream commits as patches in
  `docs/upstream-patches/`.

### Fixed (user-visible behaviour)

- **G1** The plugin no longer prunes before Hermes' gates and returns a new list when Hermes would do nothing; the
  prompt-cache prefix is only broken in a pass Hermes commits.
- **G2** Rows already rendered are not re-compressed under a new query; committed bytes stay stable between
  commits, so loss no longer compounds (one "continue" turn used to drop most error lines from a result).
- **G3** No more wasted summarizer call whose result is then rejected after Hermes mutated its state, and no
  quadratic post-check (91 s on a large transcript).
- **G4** `compress(..., bypass_cooldown=...)` and other keyword arguments reach Hermes.
- **G5** The task query is built from the latest real user request: multimodal content, base64 and Hermes' own
  synthetic or summary rows no longer produce a repr or a filler query ("continue", "thanks", "ok").
- **G6** Error loss is now checked plugin-side: an extract must keep an exemplar of every ERROR/FAIL fingerprint
  of the original and stay within `max_risk`; otherwise the row falls to a brief.
- **G7** Gaps and cuts carry Hermes' own elision marker, and every rendered row has a header, so Hermes' guard
  against side-effecting calls that paste pruned text applies.
- **G9** Hermes' wrapper fields (`exit_code`, `error`, `total_lines`) survive on the row, and Hermes' own
  `"exit_code":` matching keeps working.
- **G10** One orphan tool-call id no longer disables pruning for the rest of the session.
- **G11** The user's `compression.*` settings are honoured by the plugin engine.
- **G12** pip installs work once enabled (`plugins.enabled`); sibling modules import safely under Hermes' directory
  loader; per-agent state, `clone_for_agent` and session reset are handled; session-store isolation is detected.
- **G14** Stale reads and repeated calls are demoted structurally.
- **G15** Retained extracts are budgeted and age down.
- **G16** Replay metrics after cache pricing, re-fetch rate and saved baselines exist; on the 30-turn replay the
  plugin costs 20.0% less than stock Hermes at equal gates (22.5% shipped), keeps 7/7 error fingerprints against
  stock's 0/7, and breaks no committed row. See `tests/eval/README.md`.

### Engine (Tameru 1.4.0, vendored)

- Critical-line regex flag bug fixed; quadratic hot spots removed (a 650 KB log: 22 s to under 1 s).
- Logs keep one exemplar per error fingerprint, in order; Drain3-style template masking with a `[last]` exemplar.
- Numbered file reads (`cat -n`, `N|`, `N:`), unified diffs, grep/rg output and pytest/jest/go/cargo summaries have
  adapters; code is no longer read as CSV/TSV/YAML. A query that matches nothing fails open.
- Lossless folding (CR redraws, ANSI codes, trailing whitespace, identical runs, round-trip-checked JSON minify)
  and a JSON brief.
- Filler queries no longer cause high-risk cuts.
- Vendored from upstream commit `11b9e5e` (see `VENDORED_FROM`). The three upstream commits are in
  `docs/upstream-patches/`.

## [1.3.0]

Initial release: standalone Hermes plugin wrapping Tameru 1.3.0's extractive tool-result prune around Hermes'
summarizer, with a post-check that returned the pruned transcript when the summary lost query facts.

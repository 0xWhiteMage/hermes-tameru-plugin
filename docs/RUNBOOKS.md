# Runbooks

Short, step-by-step answers for when something looks wrong. Everything here uses switches and files that exist in
this release. Tameru never edits saved chat history, so every step below is safe to take and undo.

**Where settings live.** `$HERMES_HOME/config.yaml` under `plugins.entries.tameru.settings.<name>`, or an environment
variable `TAMERU_HERMES_<NAME>` (any setting). The four emergency switches `edits`, `manifest`, `autotune` and
`adaptive_host` also answer to the short `TAMERU_<NAME>` (for example `TAMERU_EDITS=off`); the long name wins. Restart the Hermes gateway after changing either.
**Where to look.** `/tameru` in any client prints compression progress and, at the bottom, a *Health* line that counts
secrets held back and commands refused (it appears only when something was counted). Desktop shows the same counters
in its hover cards.

| Switch | Turns off |
|---|---|
| `edits: off` | Restore and Keep, and the request overlay behind them |
| `manifest: off` | the per-turn `<tameru_note>` sent to the model |
| `autotune: off` | self-tuning (the policy stays where it is) |
| `/tameru pause` | all Tameru changes to this chat until `/tameru resume` |
| `enabled: false` | Tameru entirely |

## A. A secret appeared where it should not

Tameru refuses to store, show, restore or hand on any original that looks like a credential, and counts each refusal.

1. If a credential is visible somewhere, **rotate it first**. Hermes keeps the raw tool output in its own session
   database whatever Tameru does.
2. Set `edits: off` and `manifest: off`, restart the gateway.
3. Run `/tameru` and read the Health line: *held-back secrets* tells you whether the screen caught something.
4. If a credential shape slipped through, add it to the vendored engine's secret patterns (engine repo first, then
   re-vendor) with a test, and release a fix.

## B. Text from a web page or tool seems to be steering the model

1. Run `/tameru cancel` to drop any pending change, and `/tameru unkeep REF` for anything pinned.
2. Set `edits: off`. Web and MCP output can never be restored or kept, so the source is a different row: check
   `/tameru changes`, and use **Re-Read** (which wraps outside text as untrusted data) rather than Add.
3. Check what the model was told: the `<tameru_note>` holds only counts and file base names. If a file name looks
   hostile, set `manifest: off`.

## C. Cost went up, or the prompt cache keeps missing

A Restore or Keep costs one cache rebuild, then reuses the cache again.

1. Hover the gauge in Desktop: it shows "Reused N% from the last reply" when your provider reports cache use.
2. Run `/tameru cancel`; unpin anything with `/tameru unkeep REF`. A Restore lets go by itself after five replies.
3. If the note is the cause (it changes only when something was shortened, plus once after a Hermes summary), set
   `manifest: off`.
4. Still high? `/tameru pause`, then compare.

## D. Requests fail right after a Restore or Keep

Tameru fails open: an error while building the overlay sends the request exactly as Hermes built it, and three errors
switch edits off for the chat. If a provider still returns errors:

1. `/tameru cancel`, then set `edits: off` and restart the gateway.
2. Note the model, the provider and the size of what was restored (`/tameru changes`).
3. Reproduce with `python -m pytest tests/test_edits.py` and add the case.

## E. A chat's saved Tameru state looks wrong

State per chat lives in Hermes session metadata under two keys, `tameru_tuner_v1` (self-tuning, pause, bias) and
`tameru_edits_v1` (Keeps). Both are validated and clamped on load, and a record that does not parse is ignored.

1. `/tameru cancel` and `/tameru unkeep REF` clear edits one by one.
2. Starting a new chat starts from clean per-chat state.
3. There is no reset command in 1.8.0; if you must clear a chat's keys, do it through Hermes' session tools, not by
   editing the database by hand.

## F. Self-tuning seems off (too aggressive or stuck)

1. Set `autotune: off` to freeze the policy, or choose **Preserve detail** in the dropdown.
2. The profile-wide memory is `$HERMES_HOME/tameru/learned.json` (five numbers). It moves at most 0.12 per setting
   per day. Delete the file to reset it; a missing or unreadable file means "nothing learned yet".

### F2. Hermes summarises earlier or later than `compression.threshold` says
Tameru moves Hermes' *effective* `compression.threshold` (at most 10 points under and 15 over, never past 85% of the
window) and `protect_last_n` (half to double, 6 to 60) per chat, from how fast the chat grows, how well the cache is
reused, and how often old outputs are revisited. It never writes `config.yaml`; the Desktop memory hint shows
"Tuned for this chat" with both numbers. To put the configured values back at once, set `adaptive_host: off` (or
`TAMERU_ADAPTIVE_HOST=off`), or pause Tameru for the chat. Stored per chat as `tameru_host_v1`.

## G. The panel says "Out of date: reload Desktop"

The Desktop panel and the Python plugin carry the same build number; a mismatch means one side is old.

1. Reload the Desktop window (Ctrl+R). If it persists, copy `hermes_tameru_plugin/desktop/plugin.js` over
   `$HERMES_HOME/desktop-plugins/tameru/plugin.js` and reload.
2. Restart the gateway so the Python side matches the installed files.
3. After a reload, Desktop may reconnect to a remote gateway; check the connection menu shows the one you intend.

## H. Roll back

Use `scripts/rollback.ps1`. It restores a tagged release (default `v1.8.0`, the previous local release; `v1.6.0` is the last published one) from this
repository into `$HERMES_HOME/plugins/tameru` (or `tameru_(ためる)` if `scripts/use_display_name.ps1` was run) and the Desktop copy, keeps a timestamped backup of what it replaced in `$HERMES_HOME/plugin-backups/` (outside the folders Hermes and Desktop scan),
makes no network calls, and changes nothing unless you pass `-Apply`.

```powershell
pwsh scripts/rollback.ps1                 # shows the plan
pwsh scripts/rollback.ps1 -Apply          # does it
```

Then restart the gateway and confirm `compression.threshold` and `compression.protect_last_n` in
`$HERMES_HOME/config.yaml` are what you expect (Tameru never writes them). Newer per-chat state and `learned.json` are
ignored by older releases.

## I. Desktop's engine list says "Tameru", not "Tameru (ためる)", or the engine vanished after renaming

Desktop labels the engine from the saved `context.engine` value, and Hermes loads it from a plugin folder of the same
name, so the two must always match. `pwsh scripts/use_display_name.ps1` shows the current state and what it would
change; `-Revert -Apply` puts the plain `tameru` folder and value back. If `context.engine` names a folder that does
not exist, Hermes falls back to its built-in compressor and logs "Context engine ... not found": fix the value in
Settings or `config.yaml`, then restart the gateway. Only one copy of the plugin may sit in `plugins/` and one panel in `desktop-plugins/`: both are loaded from every folder
there, hidden or not, so a leftover backup breaks the panel ("duplicate id tameru" in `desktop.log`). Move backups to
`plugin-backups/`.

#!/usr/bin/env bash
# Roll the installed Tameru plugin back to a release tag or to any commit of this repository.
#
#   scripts/rollback.sh                                  # plan only
#   scripts/rollback.sh --tag v1.6.0 --apply             # the last published release
#   scripts/rollback.sh --ref 06a1b0a --apply            # a local commit: the usual way back after a bad update
#   scripts/rollback.sh --hermes-home /path/to/hermes    # an explicit Hermes folder
#
# The same behaviour as scripts/rollback.ps1: the folders it replaces are kept as timestamped backups in
# <hermes-home>/plugin-backups (outside the folders Hermes and Desktop scan), there is no network access, nothing is
# changed without --apply, and Hermes' own files and config.yaml are never touched.
# v1.6.0 cannot serve chats started under 1.9 (their saved system prompt names the tool tameru_read_original, which
# v1.6.0 does not have), so after a bad update prefer the previous local commit (--ref).
set -euo pipefail

tag="v1.6.0"; ref=""; home_dir=""; apply=0
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) tag="$2"; shift 2 ;;
    --ref) ref="$2"; shift 2 ;;
    --hermes-home) home_dir="$2"; shift 2 ;;
    --apply) apply=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -e "$repo/.git" ] || { echo "Run this from a clone of hermes-tameru-plugin (no .git in $repo)." >&2; exit 1; }

if [ -z "$home_dir" ]; then
  for candidate in "${HERMES_HOME:-}" "$HOME/.hermes" "${LOCALAPPDATA:-}/hermes"; do
    if [ -n "$candidate" ] && [ -d "$candidate/plugins" ]; then home_dir="$candidate"; break; fi
  done
  [ -n "$home_dir" ] || home_dir="${HERMES_HOME:-$HOME/.hermes}"
fi

if [ -n "$ref" ]; then
  commit="$(git -C "$repo" rev-parse --verify --quiet "$ref^{commit}")" || { echo "'$ref' is not a commit, branch or tag of this repository." >&2; exit 1; }
  label="$ref (${commit:0:12})"
else
  commit="$(git -C "$repo" rev-parse --verify --quiet "refs/tags/$tag^{commit}")" || { echo "Tag '$tag' does not exist here. Known tags: $(git -C "$repo" tag --list | tr '\n' ' ')" >&2; exit 1; }
  label="$tag"
fi

stamp="$(date +%Y%m%d-%H%M%S)"
kana="$(printf '\xe3\x81\x9f\xe3\x82\x81\xe3\x82\x8b')"
# The plugin may live in `tameru` or, after scripts/use_display_name.ps1, in `tameru_(<kana>)`: restore into whichever is there.
if [ -d "$home_dir/plugins/tameru_($kana)" ]; then folder="tameru_($kana)"; else folder="tameru"; fi
plugins="$home_dir/plugins/$folder"
backups="$home_dir/plugin-backups"
plugin_backup="$backups/plugins.$folder.bak-$stamp"
desktop="$home_dir/desktop-plugins/$folder"
panel_backup="$backups/desktop-plugins.$folder.plugin.js.bak-$stamp"
[ -d "$plugins" ] || { echo "No installed plugin at $plugins (check --hermes-home)." >&2; exit 1; }

echo "Tameru rollback plan"
echo "  release:        $label"
echo "  plugin folder:  $plugins   (backup: $plugin_backup)"
echo "  desktop panel:  $desktop/plugin.js   (backup: $panel_backup)"
if [ "$apply" -ne 1 ]; then echo "Nothing was changed. Re-run with --apply to do it."; exit 0; fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
git -C "$repo" archive --format=tar "$commit" hermes_tameru_plugin | tar -x -C "$work"
source_dir="$work/hermes_tameru_plugin"
[ -f "$source_dir/plugin.yaml" ] || { echo "The archive for $label has no plugin.yaml; aborting before changing anything." >&2; exit 1; }

mkdir -p "$backups"
mv "$plugins" "$plugin_backup"
cp -R "$source_dir" "$plugins"
if [ -d "$desktop" ]; then
  if [ -f "$source_dir/desktop/plugin.js" ]; then
    [ -f "$desktop/plugin.js" ] && cp "$desktop/plugin.js" "$panel_backup"
    cp "$source_dir/desktop/plugin.js" "$desktop/plugin.js"
  else
    # That release has no Desktop panel: leaving a newer one beside an older backend would show errors.
    mv "$desktop" "$backups/desktop-plugins.$folder.bak-$stamp"
    echo "$label has no Desktop panel, so the current one was moved aside to $backups/desktop-plugins.$folder.bak-$stamp"
  fi
fi
echo "Restored $label. Previous plugin kept at $plugin_backup"
echo "Next: restart the Hermes gateway, then check compression.threshold and compression.protect_last_n in $home_dir/config.yaml."

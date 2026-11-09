<#
.SYNOPSIS
  Roll the installed Tameru plugin back to a tagged release of this repository.

.DESCRIPTION
  Copies hermes_tameru_plugin from a git tag into $HERMES_HOME\plugins\tameru (or tameru_(<kana>) when scripts\use_display_name.ps1
  was run) and the Desktop panel into $HERMES_HOME\desktop-plugins\tameru\plugin.js. The folders it replaces are kept as
  timestamped backups in $HERMES_HOME\plugin-backups (outside the folders Hermes and Desktop scan). No network access. Nothing is changed unless -Apply is given; without it the script only prints its plan.
  It never touches Hermes' own files or config.yaml.

.EXAMPLE
  pwsh scripts/rollback.ps1                      # plan only
  pwsh scripts/rollback.ps1 -Tag v1.6.0 -Apply   # do it (v1.6.0 is the last published release)
#>
[CmdletBinding()]
param(
  [string]$Tag = 'v1.6.0',
  [string]$HermesHome = $(if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $HOME '.hermes' }),
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$kana = -join ([char[]](0x305F, 0x3081, 0x308B))
$pluginRoot = Join-Path $HermesHome 'plugins'
# The plugin may live in `tameru` or, after scripts\use_display_name.ps1, in `tameru_(<kana>)`: restore into whichever is there.
$folder = if (Test-Path (Join-Path $pluginRoot "tameru_($kana)")) { "tameru_($kana)" } else { 'tameru' }
$plugins = Join-Path $pluginRoot $folder
$backups = Join-Path $HermesHome 'plugin-backups'               # outside plugins\ and desktop-plugins\: Hermes and Desktop load every folder in those, hidden or not
$pluginBackup = Join-Path $backups "plugins.$folder.bak-$stamp"
$desktop = Join-Path $HermesHome "desktop-plugins\$folder"      # Hermes names the Desktop half after the plugin folder

if (-not (Test-Path (Join-Path $repo '.git'))) { throw "Run this from a clone of hermes-tameru-plugin (no .git in $repo)." }
git -C $repo rev-parse --verify --quiet "refs/tags/$Tag" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Tag '$Tag' does not exist here. Known tags: $((git -C $repo tag --list) -join ', ')" }
if (-not (Test-Path $plugins)) { throw "No installed plugin at $plugins (check -HermesHome)." }

Write-Host "Tameru rollback plan"
Write-Host "  release:        $Tag"
Write-Host "  plugin folder:  $plugins   (backup: $pluginBackup)"
Write-Host "  desktop panel:  $desktop\plugin.js   (backup: plugin.js.bak-$stamp)"
if (-not $Apply) { Write-Host "Nothing was changed. Re-run with -Apply to do it."; return }

$work = Join-Path ([IO.Path]::GetTempPath()) "tameru-rollback-$stamp"
New-Item -ItemType Directory -Path $work | Out-Null
try {
  $zip = Join-Path $work 'release.zip'
  git -C $repo archive --format=zip --output=$zip "$Tag" hermes_tameru_plugin
  Expand-Archive -Path $zip -DestinationPath (Join-Path $work 'src')
  $source = Join-Path $work 'src\hermes_tameru_plugin'
  if (-not (Test-Path (Join-Path $source 'plugin.yaml'))) { throw "The archive for $Tag has no plugin.yaml; aborting before changing anything." }

  New-Item -ItemType Directory -Force -Path $backups | Out-Null
  Move-Item -Path $plugins -Destination $pluginBackup
  Copy-Item -Path $source -Destination $plugins -Recurse
  if (Test-Path $desktop) {
    $panel = Join-Path $source 'desktop\plugin.js'
    if (Test-Path $panel) {
      Copy-Item -Path (Join-Path $desktop 'plugin.js') -Destination (Join-Path $desktop "plugin.js.bak-$stamp") -ErrorAction SilentlyContinue
      Copy-Item -Path $panel -Destination (Join-Path $desktop 'plugin.js') -Force
    } else {
      # That release has no Desktop panel: leaving a newer one beside an older backend would show errors.
      Move-Item -Path $desktop -Destination (Join-Path $backups "desktop-plugins.tameru.bak-$stamp")
      Write-Host "$Tag has no Desktop panel, so the current one was moved aside to $backups\desktop-plugins.tameru.bak-$stamp"
    }
  }
  Write-Host "Restored $Tag. Previous plugin kept at $pluginBackup"
  Write-Host "Next: restart the Hermes gateway, then check compression.threshold and compression.protect_last_n in $HermesHome\config.yaml."
}
finally {
  Remove-Item -Path $work -Recurse -Force -ErrorAction SilentlyContinue
}

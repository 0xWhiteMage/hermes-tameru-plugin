<#
.SYNOPSIS
  Roll the installed Tameru plugin back to a release tag or to any commit of this repository.

.DESCRIPTION
  Copies hermes_tameru_plugin from a git tag (-Tag) or any commit, branch or tag (-Ref) into $HERMES_HOME\plugins\tameru (or tameru_(<kana>) when scripts\use_display_name.ps1
  was run) and the Desktop panel into $HERMES_HOME\desktop-plugins\tameru\plugin.js. The folders it replaces are kept as
  timestamped backups in $HERMES_HOME\plugin-backups (outside the folders Hermes and Desktop scan). No network access. Nothing is changed unless -Apply is given; without it the script only prints its plan.
  It never touches Hermes' own files or config.yaml.

.EXAMPLE
  pwsh scripts/rollback.ps1                      # plan only
  pwsh scripts/rollback.ps1 -Tag v1.6.0 -Apply   # do it (v1.6.0 is the last published release)
  pwsh scripts/rollback.ps1 -Ref 06a1b0a -Apply  # a local commit: the usual way back after a bad update

.NOTES
  v1.6.0 cannot serve chats started under 1.9: their saved system prompt names the tool tameru_read_original, which
  v1.6.0 does not have. After a bad update, prefer the previous local commit (-Ref) over the old release.
  -HermesHome defaults to $env:HERMES_HOME, then ~\.hermes, then %LOCALAPPDATA%\hermes (the first that has a plugins folder).
#>
[CmdletBinding()]
param(
  [string]$Tag = 'v1.6.0',
  [string]$Ref = '',
  [string]$HermesHome = '',
  [switch]$Apply
)
if (-not $HermesHome) {
  $candidates = @($env:HERMES_HOME, (Join-Path $HOME '.hermes'), $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'hermes' })) | Where-Object { $_ }
  $HermesHome = ($candidates | Where-Object { Test-Path (Join-Path $_ 'plugins') } | Select-Object -First 1)
  if (-not $HermesHome) { $HermesHome = $candidates[0] }
}
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
if ($Ref) {
  $commit = git -C $repo rev-parse --verify --quiet "$Ref^{commit}"
  if ($LASTEXITCODE -ne 0) { throw "'$Ref' is not a commit, branch or tag of this repository." }
  $Tag = "$Ref ($($commit.Substring(0, 12)))"
} else {
  $commit = git -C $repo rev-parse --verify --quiet "refs/tags/$Tag^{commit}"
  if ($LASTEXITCODE -ne 0) { throw "Tag '$Tag' does not exist here. Known tags: $((git -C $repo tag --list) -join ', ')" }
}
if (-not (Test-Path $plugins)) { throw "No installed plugin at $plugins (check -HermesHome)." }
$panelBackup = Join-Path $backups "desktop-plugins.$folder.plugin.js.bak-$stamp"   # never inside desktop-plugins\: Desktop scans it

Write-Host "Tameru rollback plan"
Write-Host "  release:        $Tag"
Write-Host "  plugin folder:  $plugins   (backup: $pluginBackup)"
Write-Host "  desktop panel:  $desktop\plugin.js   (backup: $panelBackup)"
if (-not $Apply) { Write-Host "Nothing was changed. Re-run with -Apply to do it."; return }

$work = Join-Path ([IO.Path]::GetTempPath()) "tameru-rollback-$stamp"
New-Item -ItemType Directory -Path $work | Out-Null
try {
  $zip = Join-Path $work 'release.zip'
  git -C $repo archive --format=zip --output=$zip "$commit" hermes_tameru_plugin
  Expand-Archive -Path $zip -DestinationPath (Join-Path $work 'src')
  $source = Join-Path $work 'src\hermes_tameru_plugin'
  if (-not (Test-Path (Join-Path $source 'plugin.yaml'))) { throw "The archive for $Tag has no plugin.yaml; aborting before changing anything." }

  New-Item -ItemType Directory -Force -Path $backups | Out-Null
  Move-Item -Path $plugins -Destination $pluginBackup
  Copy-Item -Path $source -Destination $plugins -Recurse
  if (Test-Path $desktop) {
    $panel = Join-Path $source 'desktop\plugin.js'
    if (Test-Path $panel) {
      Copy-Item -Path (Join-Path $desktop 'plugin.js') -Destination $panelBackup -ErrorAction SilentlyContinue
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

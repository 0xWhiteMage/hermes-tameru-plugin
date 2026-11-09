<#
.SYNOPSIS
  Make Hermes Desktop's Settings > Context Engine list this plugin as "Tameru (<kana>)" instead of "Tameru".

.DESCRIPTION
  Desktop labels each engine from the saved `context.engine` value (underscores become spaces, words are
  capitalised), and Hermes finds the engine by looking for a plugin folder with that exact name. So the label can only
  change by renaming the folder and the saved value together. This script does both, nothing else:

    plugins\tameru               ->  plugins\tameru_(<kana>)        (the old folder is kept in plugin-backups\, outside
                                                                    every folder Hermes and Desktop scan: both load
                                                                    anything under plugins\ and desktop-plugins\, hidden
                                                                    or not, and two copies clash on the id "tameru")
    config.yaml  context.engine  ->  "tameru_(<kana>)"              (config.yaml is copied to config.yaml.bak-<time> first)

  The plugin's own id stays `tameru` (it comes from plugin.yaml, not from the folder), so `plugins.enabled`,
  `plugins.entries.tameru.settings` and the Desktop panel folder are untouched. Run it only when the gateway is not
  serving a chat you care about, then restart the gateway and reload Desktop.

  Nothing changes unless -Apply is given. -Revert puts the plain `tameru` folder and value back. No network access.

.EXAMPLE
  pwsh scripts/use_display_name.ps1            # plan only
  pwsh scripts/use_display_name.ps1 -Apply     # do it
  pwsh scripts/use_display_name.ps1 -Revert -Apply
#>
[CmdletBinding()]
param(
  [string]$HermesHome = $(if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $HOME '.hermes' }),
  [switch]$Revert,
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$kana = -join ([char[]](0x305F, 0x3081, 0x308B))              # <kana>, built from code points so the script is encoding-proof
$plain = 'tameru'
$display = "tameru_($kana)"
$from, $to = if ($Revert) { $display, $plain } else { $plain, $display }
$plugins = Join-Path $HermesHome 'plugins'
$source = Join-Path $plugins $from
$target = Join-Path $plugins $to
$config = Join-Path $HermesHome 'config.yaml'
$backups = Join-Path $HermesHome 'plugin-backups'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

if (-not (Test-Path (Join-Path $source 'plugin.yaml'))) { throw "No installed plugin at $source (check -HermesHome, or it is already renamed)." }
if (Test-Path $target) { throw "$target already exists; remove or move it first so there is only one copy of the plugin." }
if (-not (Test-Path $config)) { throw "No config.yaml at $config." }
$text = [IO.File]::ReadAllText($config, [Text.UTF8Encoding]::new($false))
$pattern = '(?m)^(?<head>context:\s*\r?\n(?:[ \t]+[^\r\n]*\r?\n)*?[ \t]+engine:[ \t]*)(?<value>"[^"\r\n]*"|''[^''\r\n]*''|[^\s#]+)(?<tail>[ \t]*(?:#[^\r\n]*)?)(?=\r?$)'
$match = [regex]::Match($text, $pattern)
if (-not $match.Success) { throw "config.yaml has no 'context: engine:' value to change." }
$current = $match.Groups['value'].Value.Trim('"', "'")
if ($current -ne $from) { throw "context.engine is '$current', not '$from'; nothing to do (or change it yourself in Settings)." }

Write-Host "Tameru display-name plan"
Write-Host "  plugin folder:   $source  ->  $target   (old copy kept at $backups\plugins.$from.bak-$stamp)"
Write-Host "  context.engine:  '$from'  ->  '$to'   (config.yaml backup: config.yaml.bak-$stamp)"
Write-Host "  unchanged:       plugin id 'tameru', plugins.enabled, plugins.entries.tameru.settings, desktop-plugins\tameru"
if (-not $Apply) { Write-Host "Nothing was changed. Re-run with -Apply to do it."; return }

Copy-Item -Path $config -Destination "$config.bak-$stamp"
Copy-Item -Path $source -Destination $target -Recurse
New-Item -ItemType Directory -Force -Path $backups | Out-Null
Move-Item -Path $source -Destination (Join-Path $backups "plugins.$from.bak-$stamp")
$quoted = '"' + $to + '"'
$new = $text.Substring(0, $match.Groups['value'].Index) + $quoted + $text.Substring($match.Groups['value'].Index + $match.Groups['value'].Length)
[IO.File]::WriteAllText($config, $new, [Text.UTF8Encoding]::new($false))
Write-Host "Done. Next: restart the Hermes gateway and reload Desktop. Settings will list the engine as '$(if ($Revert) { 'Tameru' } else { "Tameru ($kana)" })'."
Write-Host "Then check compression.threshold and compression.protect_last_n in $config."

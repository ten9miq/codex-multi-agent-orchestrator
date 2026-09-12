param(
    [string]$CodexHome = $env:CODEX_HOME
)

if ([string]::IsNullOrWhiteSpace($CodexHome)) {
    $CodexHome = Join-Path $env:USERPROFILE '.codex'
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot 'config.example.toml'
$destination = Join-Path $CodexHome 'config.toml'
$sourceAgents = Join-Path $repoRoot 'agents'
$destinationAgents = Join-Path $CodexHome 'agents'

New-Item -ItemType Directory -Force -Path $CodexHome | Out-Null
if (Test-Path -LiteralPath $destination) {
    $backup = "$destination.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $destination -Destination $backup
    Write-Host "既存config.tomlをバックアップしました: $backup"
}

Copy-Item -LiteralPath $source -Destination $destination -Force
New-Item -ItemType Directory -Force -Path $destinationAgents | Out-Null
Copy-Item -Path (Join-Path $sourceAgents '*.toml') -Destination $destinationAgents -Force
Write-Host "公開テンプレートを適用しました: $destination"
Write-Host "agent設定を適用しました: $destinationAgents"
Write-Host '端末固有のnotify/MCP/projects設定は必要に応じて手動で戻してください。'

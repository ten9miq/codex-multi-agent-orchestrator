param(
    [string]$CodexHome = $env:CODEX_HOME,
    [switch]$WhatIf
)

if ([string]::IsNullOrWhiteSpace($CodexHome)) {
    $CodexHome = Join-Path $env:USERPROFILE '.codex'
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot 'config.example.toml'
$destination = Join-Path $CodexHome 'config.toml'
$sourceAgents = Join-Path $repoRoot 'agents'
$destinationAgents = Join-Path $CodexHome 'agents'
$sourceMetrics = Join-Path $repoRoot 'metrics'
$destinationMetrics = Join-Path $CodexHome 'metrics'

$managedTopLevelKeys = @(
    'model',
    'model_reasoning_effort',
    'model_context_window',
    'personality',
    'service_tier'
)

function Get-MergedConfigText {
    param(
        [string]$ExistingText,
        [string]$TemplateText
    )

    $managedKeyPattern = '^\s*(?:' + (($managedTopLevelKeys | ForEach-Object { [regex]::Escape($_) }) -join '|') + ')\s*='
    $remaining = New-Object System.Collections.Generic.List[string]
    $currentSection = ''

    foreach ($line in ($ExistingText -split "`r?`n")) {
        $sectionMatch = [regex]::Match($line, '^\s*\[([^]]+)\]\s*$')
        if ($sectionMatch.Success) {
            $currentSection = $sectionMatch.Groups[1].Value
        }

        $managedSection = $currentSection -match '^(?:features(?:\.|$)|agents(?:\.|$))'
        if ($line -match $managedKeyPattern -and $currentSection -eq '') {
            continue
        }
        if ($managedSection) {
            continue
        }
        [void]$remaining.Add($line)
    }

    $templateLines = $TemplateText -split "`r?`n"
    $mergedLines = New-Object System.Collections.Generic.List[string]
    foreach ($line in $templateLines) {
        [void]$mergedLines.Add($line)
    }
    [void]$mergedLines.Add('')
    foreach ($line in $remaining) {
        [void]$mergedLines.Add($line)
    }
    return (($mergedLines -join "`r`n").TrimEnd() + "`r`n")
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Text)
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

$templateText = Get-Content -LiteralPath $source -Raw
$existingConfig = Test-Path -LiteralPath $destination

if ($WhatIf) {
    if ($existingConfig) {
        Write-Host "既存config.tomlをマージします（dry-run）: $destination"
    } else {
        Write-Host "config.example.tomlを新規配置します（dry-run）: $destination"
    }
    Write-Host "agent設定を配置します（dry-run）: $destinationAgents"
    Write-Host "Metricsを配置します（dry-run）: $destinationMetrics"
    exit 0
}

New-Item -ItemType Directory -Force -Path $CodexHome | Out-Null
if (Test-Path -LiteralPath $destination) {
    $backup = "$destination.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $destination -Destination $backup
    Write-Host "既存config.tomlをバックアップしました: $backup"
    $existingText = Get-Content -LiteralPath $destination -Raw
    Write-Utf8NoBom -Path $destination -Text (Get-MergedConfigText -ExistingText $existingText -TemplateText $templateText)
    Write-Host "公開テンプレートの管理対象だけを既存config.tomlへマージしました: $destination"
} else {
    Write-Utf8NoBom -Path $destination -Text $templateText
    Write-Host "公開テンプレートを新規配置しました: $destination"
}
New-Item -ItemType Directory -Force -Path $destinationAgents | Out-Null
Copy-Item -Path (Join-Path $sourceAgents '*.toml') -Destination $destinationAgents -Force
New-Item -ItemType Directory -Force -Path $destinationMetrics | Out-Null
Get-ChildItem -LiteralPath $sourceMetrics -File |
    Where-Object { $_.Name -notin @('state.json', 'routing-metrics.jsonl') } |
    Copy-Item -Destination $destinationMetrics -Force
Write-Host "公開テンプレートを適用しました: $destination"
Write-Host "agent設定を適用しました: $destinationAgents"
Write-Host "Metricsを適用しました: $destinationMetrics"
Write-Host '端末固有のnotify/MCP/projects設定を保持しました。'

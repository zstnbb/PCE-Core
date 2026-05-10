# SPDX-License-Identifier: Apache-2.0
<#
.SYNOPSIS
    Build the pce-mcp.mcpb Desktop Extension bundle.

.DESCRIPTION
    Runs npm install --production in pce_mcp/mcpb/ and invokes the
    official @anthropic-ai/mcpb CLI to produce a packed .mcpb archive
    in pce_mcp/mcpb/pack-output/.

    Requires Node 18+. Installs @anthropic-ai/mcpb globally on first
    run if it is not already on PATH.

    See ADR-016 §3.3 for design rationale and pce_mcp/mcpb/README.md
    for layout.

.EXAMPLE
    pwsh -File scripts/build_mcpb.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path "$PSScriptRoot\.."
$McpbDir  = Join-Path $RepoRoot "pce_mcp\mcpb"
$OutDir   = Join-Path $McpbDir "pack-output"

Write-Host "=== pce-mcp.mcpb build ==="
Write-Host "repo root : $RepoRoot"
Write-Host "source    : $McpbDir"

if (-not (Test-Path $McpbDir)) {
    throw "Bundle source directory not found: $McpbDir"
}

# --- Node + npm check ---
try {
    $nodeVer = (& node --version) 2>&1
    Write-Host "node      : $nodeVer"
} catch {
    throw "Node.js is required but was not found on PATH. Install Node 18+ from https://nodejs.org/"
}

try {
    $npmVer = (& npm --version) 2>&1
    Write-Host "npm       : $npmVer"
} catch {
    throw "npm is required but was not found on PATH."
}

# --- Install production deps ---
if (-not $SkipInstall) {
    Write-Host "`n--- npm install --production ---"
    Push-Location $McpbDir
    try {
        & npm install --production --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

# --- Ensure @anthropic-ai/mcpb is callable ---
$mcpbCmd = (Get-Command mcpb -ErrorAction SilentlyContinue)
if (-not $mcpbCmd) {
    Write-Host "`n--- Installing @anthropic-ai/mcpb globally (first run) ---"
    & npm install -g "@anthropic-ai/mcpb"
    if ($LASTEXITCODE -ne 0) {
        throw "npm install -g @anthropic-ai/mcpb failed"
    }
    $mcpbCmd = (Get-Command mcpb -ErrorAction SilentlyContinue)
    if (-not $mcpbCmd) {
        throw "mcpb CLI still not on PATH after global install. Check npm global bin dir."
    }
}
Write-Host "mcpb CLI  : $($mcpbCmd.Source)"

# --- Pack ---
# mcpb v2.x CLI shape is `mcpb pack [directory] [output]` where output
# is the FULL artifact PATH (filename inclusive), not a directory.
# Passing a directory triggers EISDIR. We resolve <name>-<version>.mcpb
# from manifest.json and build that explicit path.
$manifestPath = Join-Path $McpbDir "manifest.json"
$manifestJson = Get-Content $manifestPath -Raw | ConvertFrom-Json
$artifactName = "$($manifestJson.name)-$($manifestJson.version).mcpb"
$artifactPath = Join-Path $OutDir $artifactName

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
# mcpb pack errors with EISDIR if the output path exists as a directory
# left over from an earlier failed run; unconditionally remove a stale
# artifact path before retrying.
if (Test-Path $artifactPath) {
    Remove-Item -Recurse -Force $artifactPath
}

Write-Host "`n--- mcpb pack ---"
Write-Host "out artifact : $artifactPath"
Push-Location $McpbDir
try {
    & mcpb pack . $artifactPath
    if ($LASTEXITCODE -ne 0) {
        throw "mcpb pack failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

# --- Summary ---
$artifact = Get-ChildItem -Path $OutDir -Filter "*.mcpb" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($artifact) {
    $sizeMb = [math]::Round($artifact.Length / 1MB, 2)
    Write-Host "`n✅ Build succeeded"
    Write-Host "   artifact : $($artifact.FullName)"
    Write-Host "   size     : ${sizeMb} MB"
    Write-Host "   next     : drag into Claude Desktop Settings → Extensions → Advanced → Install Extension"
} else {
    throw "No .mcpb artifact was produced — check the mcpb pack output above."
}

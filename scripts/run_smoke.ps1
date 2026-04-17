<#
.SYNOPSIS
  Run PCE's P0 smoke suite locally on Windows.

.DESCRIPTION
  Installs dependencies if needed, then runs the smoke tests in
  ``tests/smoke/`` against an isolated temp data directory.

  This mirrors the CI workflow at ``.github/workflows/smoke.yml`` so a
  "green here" run means "green in CI".

.EXAMPLE
  pwsh scripts/run_smoke.ps1

.EXAMPLE
  # Verbose output + structured JSON logs
  $env:PCE_LOG_JSON = "1"
  pwsh scripts/run_smoke.ps1 -Verbose
#>

[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "==> PCE smoke suite" -ForegroundColor Cyan
Write-Host "    repo:   $RepoRoot"
Write-Host "    python: $Python"

if (-not $SkipInstall) {
    Write-Host "==> Ensuring dependencies" -ForegroundColor Cyan
    & $Python -m pip install --quiet --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

# Isolate test data directory
$TempData = Join-Path $env:TEMP "pce_smoke_$([guid]::NewGuid().ToString('N').Substring(0, 8))"
New-Item -ItemType Directory -Force -Path $TempData | Out-Null
$env:PCE_DATA_DIR = $TempData

Write-Host "==> Running tests/smoke/" -ForegroundColor Cyan
Write-Host "    PCE_DATA_DIR=$TempData"

& $Python -m pytest tests/smoke -v --tb=short
$exitCode = $LASTEXITCODE

Write-Host "==> Cleaning up $TempData" -ForegroundColor Cyan
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $TempData

if ($exitCode -ne 0) {
    Write-Host "==> smoke FAILED (exit $exitCode)" -ForegroundColor Red
    exit $exitCode
}

Write-Host "==> smoke OK" -ForegroundColor Green

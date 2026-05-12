<#
.SYNOPSIS
  Tear down PCE proxy chain — restore original system proxy + stop mitmdump.

.DESCRIPTION
  Reads `_harvest_state.json` (written by setup_proxy_chain.ps1) and:
    1. Stops the mitmdump process (and its child listener, if different).
    2. Restores HKCU ProxyServer / ProxyEnable to the captured originals.
    3. Broadcasts WinINet refresh so apps re-read the new (restored) value.
    4. Deletes the state file on success.

  Has an `-Emergency` mode for when state file is missing or corrupted.
  In emergency mode, sets ProxyServer to `-FallbackProxy` (default
  "127.0.0.1:7890", i.e. Clash) and kills any mitmdump.exe processes.

.PARAMETER StateFile
  Path to state file. Default `_harvest_state.json` in repo root.

.PARAMETER Emergency
  Skip state file; force-restore + kill all mitmdump processes.

.PARAMETER FallbackProxy
  When -Emergency: ProxyServer value to set. Default 127.0.0.1:7890.

.PARAMETER WorkingDirectory
  PCE repo root. Default: parent-parent of this script's dir.

.EXAMPLE
  .\scripts\harvest\teardown_proxy_chain.ps1

.EXAMPLE
  .\scripts\harvest\teardown_proxy_chain.ps1 -Emergency
#>

[CmdletBinding()]
param(
    [string] $StateFile = "",
    [switch] $Emergency,
    [string] $FallbackProxy = "127.0.0.1:7890",
    [string] $WorkingDirectory = ""
)

$ErrorActionPreference = "Continue"  # try every step even if one fails

if (-not $WorkingDirectory) {
    $WorkingDirectory = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
if (-not $StateFile) {
    $StateFile = Join-Path $WorkingDirectory "_harvest_state.json"
}

Write-Host "=== PCE Harvest: teardown_proxy_chain ===" -ForegroundColor Cyan
Write-Host "  Working dir : $WorkingDirectory"
Write-Host "  State file  : $StateFile"
if ($Emergency) {
    Write-Host "  Mode        : EMERGENCY (state file ignored)" -ForegroundColor Yellow
}

# --- Step 1: load state -----------------------------------------------------
$state = $null
if (-not $Emergency -and (Test-Path $StateFile)) {
    try {
        $state = Get-Content $StateFile -Raw | ConvertFrom-Json
        Write-Host "`n  Loaded state from: $StateFile" -ForegroundColor Gray
    } catch {
        Write-Warning "Could not parse $StateFile : $_"
        Write-Warning "Falling back to EMERGENCY behaviour."
        $Emergency = $true
    }
} elseif (-not $Emergency) {
    Write-Warning "$StateFile not found. Switching to EMERGENCY behaviour."
    $Emergency = $true
}

# --- Step 2: stop mitmdump -------------------------------------------------
$mitmKilled = @()

if ($state -and $state.mitm_spawner_pid) {
    $procs = @()
    foreach ($prop in @("mitm_spawner_pid", "mitm_listen_pid")) {
        $p = $state.$prop
        if ($p -and -not ($mitmKilled -contains $p)) {
            $procs += $p
        }
    }
    foreach ($p in $procs) {
        try {
            $running = Get-Process -Id $p -ErrorAction Stop
            Stop-Process -Id $p -Force
            $mitmKilled += $p
            Write-Host "  Stopped mitmdump PID $p" -ForegroundColor Green
        } catch {
            Write-Host "  PID $p already gone (ok)" -ForegroundColor Gray
        }
    }
}

if ($Emergency) {
    # Kill any stray mitmdump processes.
    Get-Process -Name "mitmdump" -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            Stop-Process -Id $_.Id -Force
            $mitmKilled += $_.Id
            Write-Host "  [emergency] Stopped mitmdump PID $($_.Id)" -ForegroundColor Yellow
        } catch {
            Write-Host "  [emergency] Could not stop PID $($_.Id): $_" -ForegroundColor Red
        }
    }
}

if ($mitmKilled.Count -eq 0) {
    Write-Host "  No mitmdump process found running." -ForegroundColor Gray
}

# --- Step 3: restore system proxy ------------------------------------------
$regKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"

if ($state -and -not $Emergency) {
    $restoreServer = $state.orig_proxy_server
    $restoreEnable = [int]$state.orig_proxy_enable
} else {
    $restoreServer = $FallbackProxy
    $restoreEnable = 1
    Write-Host "  [emergency] Using fallback: ProxyServer='$restoreServer', ProxyEnable=$restoreEnable" -ForegroundColor Yellow
}

try {
    if ($restoreServer) {
        Set-ItemProperty -Path $regKey -Name ProxyServer -Value $restoreServer
    }
    Set-ItemProperty -Path $regKey -Name ProxyEnable -Value $restoreEnable
    Write-Host "`n  ProxyServer restored to : '$restoreServer'" -ForegroundColor Green
    Write-Host "  ProxyEnable restored to : $restoreEnable" -ForegroundColor Green
} catch {
    Write-Host "  Registry restore failed: $_" -ForegroundColor Red
}

# --- Step 4: WinINet refresh -----------------------------------------------
try {
    $signature = @'
[DllImport("wininet.dll", SetLastError = true)]
public static extern bool InternetSetOption(IntPtr hInternet, int dwOption, IntPtr lpBuffer, int dwBufferLength);
'@
    $iws = Add-Type -MemberDefinition $signature -Name InternetSettingsTeardown -Namespace Net -PassThru
    [void]$iws::InternetSetOption([IntPtr]::Zero, 39, [IntPtr]::Zero, 0)  # SETTINGS_CHANGED
    [void]$iws::InternetSetOption([IntPtr]::Zero, 37, [IntPtr]::Zero, 0)  # REFRESH
    Write-Host "  WinINet refresh broadcast" -ForegroundColor Green
} catch {
    Write-Host "  WinINet refresh failed (non-fatal): $_" -ForegroundColor Yellow
}

# --- Step 5: clean up state file -------------------------------------------
if ($state -and (Test-Path $StateFile) -and -not $Emergency) {
    try {
        Remove-Item -Path $StateFile -Force
        Write-Host "  Removed state file" -ForegroundColor Gray
    } catch {
        Write-Host "  Could not remove state file: $_" -ForegroundColor Yellow
    }
}

# --- Done -------------------------------------------------------------------
Write-Host "`n✔ Teardown complete." -ForegroundColor Green
Write-Host "  Verify: " -NoNewline -ForegroundColor Cyan
Write-Host "Get-ItemProperty '$regKey' -Name ProxyServer, ProxyEnable" -ForegroundColor Gray

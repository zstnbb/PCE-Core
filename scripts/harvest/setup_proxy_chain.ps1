<#
.SYNOPSIS
  Set up PCE proxy chain for harvest sessions.

.DESCRIPTION
  1. Discovers the current HKCU system ProxyServer value (typically
     "127.0.0.1:7890" for Clash/Mihomo users).
  2. Writes the original value + enable state to a JSON state file
     so `teardown_proxy_chain.ps1` can restore them.
  3. Starts mitmdump on :8080 with `--mode upstream:<original_proxy>`
     — apps still route through the VPN/Clash exit, but PCE sees the
     plaintext HTTPS flow in the middle.
  4. Switches HKCU ProxyServer to `127.0.0.1:8080` and broadcasts
     WinINet refresh so running apps pick it up.

  Output: logs what it did; errors out loudly if any step failed.

.PARAMETER MitmPort
  Port mitmdump listens on. Default 8080.

.PARAMETER StateFile
  Path to write the restoration state file. Default `_harvest_state.json`
  in the repo root.

.PARAMETER WorkingDirectory
  PCE repo root (where run_proxy.py lives). Default: parent-parent of
  this script's directory.

.EXAMPLE
  .\scripts\harvest\setup_proxy_chain.ps1

.EXAMPLE
  .\scripts\harvest\setup_proxy_chain.ps1 -MitmPort 8081
#>

[CmdletBinding()]
param(
    [int] $MitmPort = 8080,
    [string] $StateFile = "",
    [string] $WorkingDirectory = ""
)

$ErrorActionPreference = "Stop"

# --- Resolve paths -----------------------------------------------------------
if (-not $WorkingDirectory) {
    $WorkingDirectory = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
if (-not $StateFile) {
    $StateFile = Join-Path $WorkingDirectory "_harvest_state.json"
}

$runProxyPath = Join-Path $WorkingDirectory "run_proxy.py"
if (-not (Test-Path $runProxyPath)) {
    throw "Cannot find run_proxy.py at $runProxyPath. Are you in the PCE repo root?"
}

Write-Host "=== PCE Harvest: setup_proxy_chain ===" -ForegroundColor Cyan
Write-Host "  Working dir : $WorkingDirectory"
Write-Host "  State file  : $StateFile"
Write-Host "  Mitm port   : $MitmPort"

# --- Step 1: snapshot current system proxy ----------------------------------
$regKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
$origProxyServer = $null
$origProxyEnable = 0
try {
    $origProxyServer = (Get-ItemProperty -Path $regKey -Name ProxyServer -ErrorAction Stop).ProxyServer
} catch {
    $origProxyServer = ""
}
try {
    $origProxyEnable = (Get-ItemProperty -Path $regKey -Name ProxyEnable -ErrorAction Stop).ProxyEnable
} catch {
    $origProxyEnable = 0
}

Write-Host "`n  Original ProxyServer : '$origProxyServer'"
Write-Host "  Original ProxyEnable : $origProxyEnable"

# If no original proxy, we cannot run upstream mode — bail with guidance.
if (-not $origProxyServer -or $origProxyEnable -eq 0) {
    Write-Warning "System proxy is currently not enabled (or is empty)."
    Write-Warning "Upstream mode requires a valid upstream proxy to forward to."
    Write-Warning "If you want a non-upstream setup, start mitmdump directly:"
    Write-Warning "    mitmdump -s run_proxy.py -p $MitmPort"
    throw "Cannot proceed — no upstream proxy to forward to."
}

# --- Step 2: check port availability ----------------------------------------
$listener = Get-NetTCPConnection -LocalPort $MitmPort -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    $pidList = ($listener | Select-Object -ExpandProperty OwningProcess -Unique) -join ", "
    throw "Port $MitmPort is already in use by PID(s) $pidList. Stop that process first (Stop-Process -Id $pidList -Force)."
}

# --- Step 3: start mitmdump in upstream mode -------------------------------
$logStdout = Join-Path $WorkingDirectory "_harvest_mitm_stdout.log"
$logStderr = Join-Path $WorkingDirectory "_harvest_mitm_stderr.log"

$mitmArgs = @(
    "-s", "`"$runProxyPath`"",
    "-p", "$MitmPort",
    "--mode", "upstream:http://$origProxyServer",
    "--set", "flow_detail=1"
)

Write-Host "`n  Starting mitmdump..." -ForegroundColor Yellow
$mitmProc = Start-Process -FilePath "mitmdump" `
    -ArgumentList $mitmArgs `
    -WorkingDirectory $WorkingDirectory `
    -RedirectStandardOutput $logStdout `
    -RedirectStandardError $logStderr `
    -NoNewWindow `
    -PassThru

# Wait up to 5 seconds for it to start listening.
$deadline = (Get-Date).AddSeconds(5)
$started = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $lsn = Get-NetTCPConnection -LocalPort $MitmPort -State Listen -ErrorAction SilentlyContinue
    if ($lsn) {
        $started = $true
        break
    }
}

if (-not $started) {
    # Dump the log so the user knows what went wrong.
    Write-Host "`nmitmdump failed to start. Last 20 lines of stderr:" -ForegroundColor Red
    if (Test-Path $logStderr) {
        Get-Content $logStderr -Tail 20 | ForEach-Object { Write-Host "  | $_" }
    }
    throw "mitmdump did not bind to :$MitmPort within 5 s."
}

# Figure out the actual listening PID (mitmdump may spawn a child).
$listenPID = (Get-NetTCPConnection -LocalPort $MitmPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)
Write-Host "  mitmdump started (spawner PID $($mitmProc.Id), listening PID $listenPID)" -ForegroundColor Green

# --- Step 4: switch HKCU system proxy to 8080 -------------------------------
Set-ItemProperty -Path $regKey -Name ProxyServer -Value "127.0.0.1:$MitmPort"
Set-ItemProperty -Path $regKey -Name ProxyEnable -Value 1

# Broadcast WinINet refresh so running apps see the change.
$signature = @'
[DllImport("wininet.dll", SetLastError = true)]
public static extern bool InternetSetOption(IntPtr hInternet, int dwOption, IntPtr lpBuffer, int dwBufferLength);
'@
$iws = Add-Type -MemberDefinition $signature -Name InternetSettings -Namespace Net -PassThru
[void]$iws::InternetSetOption([IntPtr]::Zero, 39, [IntPtr]::Zero, 0)  # INTERNET_OPTION_SETTINGS_CHANGED
[void]$iws::InternetSetOption([IntPtr]::Zero, 37, [IntPtr]::Zero, 0)  # INTERNET_OPTION_REFRESH

# --- Step 5: persist state file --------------------------------------------
$state = [pscustomobject]@{
    timestamp_iso       = (Get-Date).ToString("o")
    orig_proxy_server   = $origProxyServer
    orig_proxy_enable   = $origProxyEnable
    mitm_spawner_pid    = $mitmProc.Id
    mitm_listen_pid     = $listenPID
    mitm_port           = $MitmPort
    log_stdout          = $logStdout
    log_stderr          = $logStderr
    working_directory   = $WorkingDirectory
}
$state | ConvertTo-Json -Depth 4 | Set-Content -Path $StateFile -Encoding utf8

# --- Done -------------------------------------------------------------------
Write-Host "`n✔ Setup complete. State written to:" -ForegroundColor Green
Write-Host "    $StateFile"
Write-Host "`nSystem proxy now: 127.0.0.1:$MitmPort  (upstream: $origProxyServer)" -ForegroundColor Green
Write-Host "mitmdump log (stderr): $logStderr" -ForegroundColor Gray
Write-Host "`nWhen done, run:  .\scripts\harvest\teardown_proxy_chain.ps1" -ForegroundColor Cyan

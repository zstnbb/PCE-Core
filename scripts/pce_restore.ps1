# SPDX-License-Identifier: Apache-2.0
#
# PCE Emergency Restore — Pure PowerShell version
#
# Zero dependencies. Will run on any Windows machine even if Python is
# uninstalled, because PowerShell is built into Windows.
#
# Use this when your computer can't reach the network after PCE crashed
# or was force-killed: most apps fail because the OS system proxy still
# points at PCE's mitmproxy (127.0.0.1:8080), but mitmproxy is no longer
# running.
#
# Usage (from PowerShell or by double-clicking pce_restore.cmd):
#
#     .\pce_restore.ps1                 # auto: read snapshot, restore
#     .\pce_restore.ps1 -Disable        # force-disable system proxy
#     .\pce_restore.ps1 -Show           # just show current state

param(
    [switch]$Disable,
    [switch]$Show
)

$ErrorActionPreference = 'Continue'

$StateFile = Join-Path $env:USERPROFILE '.pce\state\system_state.json'
$InternetSettingsKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'

Write-Host '============================================================'
Write-Host 'PCE Emergency Restore  (PowerShell, zero-dep)'
Write-Host '============================================================'
Write-Host ''

# ---------------------------------------------------------------------------
# WinINET refresh — broadcasts that proxy settings changed.
# Without this, browsers + WinHTTP-based tools keep using the old value.
# ---------------------------------------------------------------------------
$winInetSig = @'
[DllImport("wininet.dll", SetLastError = true, CharSet = CharSet.Auto)]
public static extern bool InternetSetOption(
    IntPtr hInternet, int dwOption, IntPtr lpBuffer, int dwBufferLength);
'@

function Invoke-WinINetRefresh {
    try {
        $t = Add-Type -MemberDefinition $winInetSig `
                      -Name 'PCEWinINet' -Namespace 'PCE' -PassThru
        [void]$t::InternetSetOption([IntPtr]::Zero, 39, [IntPtr]::Zero, 0)
        [void]$t::InternetSetOption([IntPtr]::Zero, 37, [IntPtr]::Zero, 0)
    } catch {
        # Best-effort; not fatal if it fails.
    }
}

# ---------------------------------------------------------------------------
# State read / show
# ---------------------------------------------------------------------------

function Show-Current {
    Write-Host "Snapshot   : $StateFile"
    if (Test-Path $StateFile) {
        Write-Host '             EXISTS'
    } else {
        Write-Host '             (absent)'
    }
    Write-Host ''
    try {
        $ie = Get-ItemProperty -Path $InternetSettingsKey -ErrorAction Stop
    } catch {
        Write-Host 'Could not read Internet Settings.'
        return
    }
    Write-Host "ProxyEnable   : $($ie.ProxyEnable)"
    Write-Host "ProxyServer   : $($ie.ProxyServer)"
    Write-Host "ProxyOverride : $($ie.ProxyOverride)"
}

# ---------------------------------------------------------------------------
# Disable
# ---------------------------------------------------------------------------

function Disable-Proxy {
    Set-ItemProperty -Path $InternetSettingsKey -Name 'ProxyEnable' -Value 0
    Invoke-WinINetRefresh
    Write-Host '[OK] System proxy disabled.'
}

# ---------------------------------------------------------------------------
# Enable to a specific host/port + bypass list
# ---------------------------------------------------------------------------

function Enable-Proxy {
    param(
        [string]$Host_, [int]$Port, [string[]]$Bypass
    )
    $server = "$($Host_):$Port"
    $override = ''
    if ($Bypass -and $Bypass.Count -gt 0) {
        # Dedup, preserve order
        $seen = @{}
        $kept = @()
        foreach ($b in $Bypass) {
            if (-not $seen.ContainsKey($b)) {
                $seen[$b] = $true
                $kept += $b
            }
        }
        $override = $kept -join ';'
    }
    Set-ItemProperty -Path $InternetSettingsKey -Name 'ProxyEnable' -Value 1
    Set-ItemProperty -Path $InternetSettingsKey -Name 'ProxyServer' -Value $server
    if ($override) {
        Set-ItemProperty -Path $InternetSettingsKey -Name 'ProxyOverride' -Value $override
    }
    Invoke-WinINetRefresh
    Write-Host "[OK] System proxy restored to $server"
    if ($override) {
        Write-Host "     ($([System.Linq.Enumerable]::Count($kept)) bypass entries)"
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if ($Show) {
    Show-Current
    exit 0
}

if ($Disable) {
    Write-Host 'Forcibly disabling system proxy (--disable)...'
    Disable-Proxy
    exit 0
}

# Default: snapshot-based restore.
if (Test-Path $StateFile) {
    Write-Host "Reading PCE snapshot: $StateFile"
    try {
        $snap = Get-Content $StateFile -Raw | ConvertFrom-Json
    } catch {
        Write-Host "WARNING: snapshot is unreadable: $_"
        Write-Host 'Falling back to clean disable...'
        Disable-Proxy
        Remove-Item $StateFile -Force -ErrorAction SilentlyContinue
        exit 0
    }

    $proxy = $snap.proxy
    if ($proxy -and $proxy.enabled -and $proxy.host -and $proxy.port) {
        Write-Host "Snapshot says: proxy = $($proxy.host):$($proxy.port)"
        $bp = @()
        if ($proxy.bypass) { $bp = @($proxy.bypass) }
        Enable-Proxy -Host_ $proxy.host -Port $proxy.port -Bypass $bp
    } else {
        Write-Host 'Snapshot says proxy was OFF — disabling.'
        Disable-Proxy
    }
    Remove-Item $StateFile -Force -ErrorAction SilentlyContinue
    Write-Host 'Snapshot file deleted.'
    exit 0
}

# No snapshot — but check for orphaned PCE-port setting just in case.
Write-Host "No snapshot at $StateFile"
Write-Host 'Inspecting current system proxy...'
Write-Host ''
try {
    $ie = Get-ItemProperty -Path $InternetSettingsKey -ErrorAction Stop
    $srv = if ($ie.ProxyServer) { $ie.ProxyServer } else { '' }
    if ($ie.ProxyEnable -eq 1 -and ($srv -match '127\.0\.0\.1:8080' -or
                                      $srv -match 'localhost:8080')) {
        Write-Host "  ! system proxy = $srv"
        Write-Host '    This looks like an orphaned PCE setting.'
        Write-Host '    Disabling...'
        Disable-Proxy
    } else {
        Write-Host "  system proxy = '$srv' (enabled=$($ie.ProxyEnable))"
        Write-Host '  Not a PCE setting — leaving alone.'
    }
} catch {
    Write-Host "Could not read Internet Settings: $_"
}
exit 0

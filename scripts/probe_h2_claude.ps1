# SPDX-License-Identifier: Apache-2.0
#
# probe_h2_claude.ps1 -- non-destructive H2 empirical test for ADR-018 OQ.
#
# H2: Does Anthropic cert-pin api.anthropic.com on Claude Desktop (MSIX)?
#
# Approach:
#   1. Install the mitmproxy CA into the CurrentUser Root store (no UAC).
#   2. Start mitmdump chained to the user's existing upstream proxy
#      (typically 127.0.0.1:7890 on GFW-bypassed hosts) so Anthropic
#      reachability is preserved.
#   3. Control test: GET https://github.com through the proxy chain
#      with curl.exe. If this fails, the chain itself is broken -- abort
#      before disrupting Claude.
#   4. Flip HKCU Internet Settings proxy to the mitmproxy port.
#   5. Kill + restart Claude Desktop via shell:appsFolder\<AUMID>.
#   6. Wait 25s; parse mitmdump's stdout log for api.anthropic.com /
#      claude.ai hits vs TLS / handshake errors.
#   7. `try / finally` cleanup restores the proxy, kills mitmproxy,
#      kills Claude, and removes the CA -- even on any mid-run failure.
#
# Result semantics:
#   - >=1 successful HTTP request to api.anthropic.com / claude.ai
#     returning 2xx/4xx => H2 = PASS (no cert pin; A1 mitmproxy viable).
#   - Only TLS handshake errors / no cleartext => H2 = FAIL (cert pinned;
#     A1 dead; A2 SSLKEYLOGFILE is the only in-app chat-region path).
#
# Side-effects this script touches + restores:
#   - HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings
#     ProxyEnable / ProxyServer  (saved before, restored in finally)
#   - Cert:\CurrentUser\Root  (CA added, then removed in finally)
#   - Claude Desktop process state  (killed at start, killed again at
#     end so the user's next launch is clean)

[CmdletBinding()]
param(
    [int]$MitmPort = 8090,
    [string]$Upstream = "http://127.0.0.1:7890",
    [int]$SleepSeconds = 25,
    [string]$ClaudeAumid = "Claude_pzs8sxrjxfjjc!Claude"
)

$ErrorActionPreference = "Stop"

$caPath      = Join-Path $env:USERPROFILE ".mitmproxy\mitmproxy-ca-cert.cer"
$logPath     = Join-Path $env:TEMP "pce_h2_mitm.log"
$errLogPath  = Join-Path $env:TEMP "pce_h2_mitm.err.log"
$regKey      = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"

if (-not (Test-Path $caPath)) {
    Write-Host "FATAL: mitmproxy CA not found at $caPath" -ForegroundColor Red
    Write-Host "Run 'mitmdump' once to generate it." -ForegroundColor Red
    exit 2
}

$thumb = (Get-FileHash -Algorithm SHA1 $caPath).Hash
$saved = Get-ItemProperty $regKey
$savedProxyEnable = $saved.ProxyEnable
$savedProxyServer = $saved.ProxyServer

Write-Host "=== H2 PROBE -- saved state ==="
Write-Host "  thumbprint (SHA1): $thumb"
Write-Host "  ProxyEnable=$savedProxyEnable ProxyServer=$savedProxyServer"
Write-Host "  mitmproxy port: $MitmPort, upstream: $Upstream"
Write-Host ""

$mitm = $null

try {
    # --- 1. Install CA into CurrentUser Root ---------------------------
    Write-Host "=== 1. Install CA into CurrentUser Root (no UAC) ==="
    $out = & certutil -user -addstore -f Root $caPath 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Write-Host $out
        throw "certutil addstore failed (rc=$LASTEXITCODE)"
    }
    Write-Host "  CA installed"

    # --- 2. Start mitmproxy chained to upstream ------------------------
    Write-Host "=== 2. Start mitmdump (upstream $Upstream, listen $MitmPort) ==="
    Remove-Item $logPath, $errLogPath -ErrorAction SilentlyContinue
    $mitmArgs = @(
        "--mode", "upstream:$Upstream",
        "--listen-port", "$MitmPort",
        "--set", "flow_detail=2",
        "--set", "console_layout=single"
    )
    $mitm = Start-Process mitmdump `
        -ArgumentList $mitmArgs `
        -RedirectStandardOutput $logPath `
        -RedirectStandardError $errLogPath `
        -WindowStyle Hidden `
        -PassThru
    Start-Sleep -Seconds 3
    if ($mitm.HasExited) {
        Write-Host "FATAL: mitmproxy died on startup (exit=$($mitm.ExitCode))"
        if (Test-Path $errLogPath) {
            Write-Host "--- stderr ---"
            Get-Content $errLogPath -Tail 30
        }
        if (Test-Path $logPath) {
            Write-Host "--- stdout ---"
            Get-Content $logPath -Tail 30
        }
        throw "mitmproxy startup failed"
    }
    Write-Host "  mitmproxy PID: $($mitm.Id)"

    # --- 3. Control test: proxy chain sanity ---------------------------
    Write-Host "=== 3. Control test: https://github.com via proxy chain ==="
    $code = & curl.exe -x "http://127.0.0.1:$MitmPort" -sS -k -o NUL `
        -w "%{http_code}" --max-time 20 https://github.com 2>&1
    Write-Host "  github.com status via chain: $code"
    if ($code -notmatch "^(200|301|302)$") {
        throw "control test failed (got '$code'); aborting before Claude restart"
    }

    # --- 4. Flip system proxy ------------------------------------------
    Write-Host "=== 4. Flip system proxy -> 127.0.0.1:$MitmPort ==="
    Set-ItemProperty $regKey -Name ProxyServer -Value "127.0.0.1:$MitmPort"
    Set-ItemProperty $regKey -Name ProxyEnable -Value 1
    Write-Host "  done"

    # --- 5. Restart Claude Desktop -------------------------------------
    Write-Host "=== 5. Restart Claude Desktop ==="
    Get-Process -Name "Claude*" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
    Start-Process "shell:appsFolder\$ClaudeAumid"
    Write-Host "  launched; sleeping ${SleepSeconds}s for TLS handshakes..."
    Start-Sleep -Seconds $SleepSeconds

    # --- 6. Observation ------------------------------------------------
    Write-Host ""
    Write-Host "=== 6. Observation ==="
    $logSize = if (Test-Path $logPath) { (Get-Item $logPath).Length } else { 0 }
    $errSize = if (Test-Path $errLogPath) { (Get-Item $errLogPath).Length } else { 0 }
    Write-Host "  stdout log: $logSize bytes  |  stderr log: $errSize bytes"

    $anthropicMatches = @()
    $errorMatches = @()
    if ($logSize -gt 0) {
        $anthropicMatches = @(Select-String -Path $logPath `
            -Pattern "anthropic\.com|claude\.ai" `
            -AllMatches -ErrorAction SilentlyContinue)
        # Anchor to mitmproxy's actual error-line grammar so HTTP header
        # content (cookies / HSTS / business fields containing "pin",
        # "failed", "refused") cannot masquerade as TLS errors.
        $errorMatches = @(Select-String -Path $logPath `
            -Pattern "TLS handshake failed|TlsException|bad_certificate|unknown_ca|certificate_verify_failed|tls_alert_fatal|Cannot establish TLS|Client disconnected" `
            -AllMatches -ErrorAction SilentlyContinue)
    }
    Write-Host "  anthropic/claude.ai hits: $($anthropicMatches.Count)"
    Write-Host "  TLS/cert error hits:      $($errorMatches.Count)"
    Write-Host ""

    if ($logSize -gt 0) {
        Write-Host "--- stdout tail (last 60 lines) ---"
        Get-Content $logPath -Tail 60
    }
    if ($errSize -gt 0) {
        Write-Host ""
        Write-Host "--- stderr tail (last 30 lines) ---"
        Get-Content $errLogPath -Tail 30
    }

    # Heuristic verdict
    Write-Host ""
    Write-Host "=== VERDICT ==="
    if ($anthropicMatches.Count -gt 0 -and $errorMatches.Count -eq 0) {
        Write-Host "  H2 = PASS (no cert pin; A1 mitmproxy viable)" -ForegroundColor Green
    } elseif ($anthropicMatches.Count -eq 0 -and $errorMatches.Count -gt 0) {
        Write-Host "  H2 = FAIL (cert pin; A1 dead; A2 SSLKEYLOGFILE only path)" -ForegroundColor Yellow
    } elseif ($anthropicMatches.Count -eq 0 -and $errorMatches.Count -eq 0) {
        Write-Host "  INCONCLUSIVE (Claude didn't hit anthropic.com in window; extend SleepSeconds)" -ForegroundColor Yellow
    } else {
        Write-Host "  MIXED (see hit counts; likely partial pin / partial pass)" -ForegroundColor Yellow
    }
}
finally {
    Write-Host ""
    Write-Host "=== CLEANUP ==="
    # Restore system proxy first (most critical)
    try {
        Set-ItemProperty $regKey -Name ProxyServer -Value $savedProxyServer -ErrorAction Stop
        Set-ItemProperty $regKey -Name ProxyEnable -Value $savedProxyEnable -ErrorAction Stop
        Write-Host "  system proxy restored: ProxyServer=$savedProxyServer ProxyEnable=$savedProxyEnable"
    } catch {
        Write-Host "  WARN: failed to restore proxy: $($_.Exception.Message)" -ForegroundColor Red
    }

    # Kill mitmproxy
    if ($mitm -and -not $mitm.HasExited) {
        try {
            Stop-Process -Id $mitm.Id -Force -ErrorAction Stop
            Write-Host "  mitmproxy PID $($mitm.Id) stopped"
        } catch {
            Write-Host "  WARN: failed to kill mitmproxy: $($_.Exception.Message)" -ForegroundColor Red
        }
    }

    # Kill Claude so the next launch reads the restored proxy
    try {
        $c = Get-Process -Name "Claude*" -ErrorAction SilentlyContinue
        if ($c) {
            $c | Stop-Process -Force -ErrorAction SilentlyContinue
            Write-Host "  Claude processes killed"
        }
    } catch {}

    # Remove CA from CurrentUser Root
    try {
        $null = & certutil -user -delstore Root $thumb 2>&1 | Out-String
        $still = Get-ChildItem Cert:\CurrentUser\Root -ErrorAction SilentlyContinue |
            Where-Object { $_.Thumbprint -eq $thumb }
        if ($still) {
            Write-Host "  WARN: CA still present after delstore (thumb $thumb)" -ForegroundColor Red
        } else {
            Write-Host "  CA removed"
        }
    } catch {
        Write-Host "  WARN: CA removal error: $($_.Exception.Message)" -ForegroundColor Red
    }

    Write-Host "=== CLEANUP DONE ==="
    Write-Host ""
    Write-Host "Logs preserved for diagnosis:"
    Write-Host "  stdout: $logPath"
    Write-Host "  stderr: $errLogPath"
}

# PCE RECON — P4 Windsurf cert-trust probe
# Purpose: Verify whether NODE_EXTRA_CA_CERTS unlocks Cascade chat capture
#
# Prerequisites:
#   - mitmproxy installed (pip install mitmproxy)
#   - Windsurf installed
#   - mitmproxy CA cert at ~/.mitmproxy/mitmproxy-ca-cert.pem
#
# Usage:
#   1. Close Windsurf completely
#   2. Run this script (it starts mitmproxy + launches Windsurf with cert trust)
#   3. Send a prompt in Cascade: "What is 2+2?"
#   4. Press Ctrl+C to stop mitmproxy
#   5. Check output for Cascade chat captures

$ErrorActionPreference = "Stop"

# --- Configuration ---
$MITM_PORT = 8080
$UPSTREAM_PROXY = "http://127.0.0.1:7890"  # Clash proxy (adjust if different)
$MITM_CA_CERT = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem"
$WINDSURF_EXE = "$env:LOCALAPPDATA\Programs\Windsurf\Windsurf.exe"
$LOG_FILE = ".\.diag_recon_windsurf_cert.log"

# --- Validation ---
if (-not (Test-Path $MITM_CA_CERT)) {
    Write-Error "mitmproxy CA cert not found at: $MITM_CA_CERT"
    exit 1
}

if (-not (Test-Path $WINDSURF_EXE)) {
    # Try alternative paths
    $alt = "C:\Users\$env:USERNAME\AppData\Local\Programs\Windsurf\Windsurf.exe"
    if (Test-Path $alt) { $WINDSURF_EXE = $alt }
    else {
        Write-Warning "Windsurf.exe not found at expected path. Please set `$WINDSURF_EXE manually."
        Write-Host "Searched: $WINDSURF_EXE"
        exit 1
    }
}

Write-Host "=== PCE RECON: P4 Windsurf cert-trust probe ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "CA cert:    $MITM_CA_CERT"
Write-Host "Windsurf:   $WINDSURF_EXE"
Write-Host "MITM port:  $MITM_PORT"
Write-Host "Upstream:   $UPSTREAM_PROXY"
Write-Host ""

# --- Step 1: Start mitmproxy in background ---
Write-Host "[1/3] Starting mitmproxy (upstream mode)..." -ForegroundColor Yellow

$mitmArgs = @(
    "--mode", "upstream:$UPSTREAM_PROXY",
    "--listen-port", "$MITM_PORT",
    "--set", "stream_large_bodies=1",
    "-s", "run_proxy.py"
)

$mitmProcess = Start-Process -FilePath "mitmdump" -ArgumentList $mitmArgs `
    -PassThru -RedirectStandardOutput "$LOG_FILE.mitm.out" `
    -RedirectStandardError "$LOG_FILE.mitm.err" -WindowStyle Hidden

Write-Host "  mitmproxy PID: $($mitmProcess.Id)"
Start-Sleep -Seconds 3

# --- Step 2: Launch Windsurf with cert trust + proxy ---
Write-Host "[2/3] Launching Windsurf with NODE_EXTRA_CA_CERTS..." -ForegroundColor Yellow

$env:NODE_EXTRA_CA_CERTS = $MITM_CA_CERT
$env:http_proxy = "http://127.0.0.1:$MITM_PORT"
$env:https_proxy = "http://127.0.0.1:$MITM_PORT"
$env:HTTP_PROXY = "http://127.0.0.1:$MITM_PORT"
$env:HTTPS_PROXY = "http://127.0.0.1:$MITM_PORT"

$wsProcess = Start-Process -FilePath $WINDSURF_EXE -PassThru
Write-Host "  Windsurf PID: $($wsProcess.Id)"

# --- Step 3: Wait for user interaction ---
Write-Host ""
Write-Host "[3/3] Windsurf is running with cert trust injection." -ForegroundColor Green
Write-Host ""
Write-Host "  ACTION REQUIRED:" -ForegroundColor White
Write-Host "  1. Open Cascade panel (Ctrl+L)"
Write-Host "  2. Send a prompt: 'What is 2+2?'"
Write-Host "  3. Wait for response"
Write-Host "  4. Press ENTER here when done"
Write-Host ""

Read-Host "Press ENTER after sending a Cascade prompt..."

# --- Step 4: Check results ---
Write-Host ""
Write-Host "=== Checking captures ===" -ForegroundColor Cyan

# Query the PCE database for recent Codeium captures
$checkScript = @"
import sqlite3, os, json
from datetime import datetime, timedelta

db_path = os.path.expanduser('~/.pce/data/pce.db')
if not os.path.exists(db_path):
    print('ERROR: PCE database not found at', db_path)
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Look for recent captures from server.codeium.com
cutoff = (datetime.utcnow() - timedelta(minutes=10)).isoformat()
rows = conn.execute('''
    SELECT id, host, path, direction, length(body) as body_size,
           created_at, content_type
    FROM raw_captures
    WHERE host LIKE '%codeium%'
      AND created_at > ?
    ORDER BY created_at DESC
    LIMIT 30
''', (cutoff,)).fetchall()

print(f'\nCaptures from *codeium* in last 10 min: {len(rows)}')
print('-' * 80)

chat_found = False
for r in rows:
    path = r['path'] or ''
    is_chat = 'Chat' in path or 'Cascade' in path or 'Language' in path
    marker = ' <<<< CHAT?' if is_chat else ''
    print(f"  {r['direction']:8s} {r['host']:30s} {path[:50]:50s} {r['body_size'] or 0:>8d}B{marker}")
    if is_chat:
        chat_found = True

print('-' * 80)
if chat_found:
    print('\n*** SUCCESS: Cascade chat traffic captured! ***')
    print('NODE_EXTRA_CA_CERTS bypass WORKS.')
elif len(rows) > 0:
    print('\nManagement plane captured but no chat traffic yet.')
    print('Possible causes:')
    print('  - Cascade chat uses a different pinning mechanism')
    print('  - The prompt was not sent yet')
    print('  - Chat endpoint path does not contain Chat/Cascade/Language')
else:
    print('\nNo Codeium captures at all in last 10 min.')
    print('Possible causes:')
    print('  - Proxy env vars not picked up by Windsurf')
    print('  - mitmproxy not running correctly')
    print('  - Windsurf was already running (env vars only apply at launch)')

conn.close()
"@

python -c $checkScript

# --- Cleanup ---
Write-Host ""
Write-Host "=== Cleanup ===" -ForegroundColor Yellow
Write-Host "Stopping mitmproxy (PID $($mitmProcess.Id))..."
Stop-Process -Id $mitmProcess.Id -Force -ErrorAction SilentlyContinue

# Clear env vars
Remove-Item Env:\NODE_EXTRA_CA_CERTS -ErrorAction SilentlyContinue
Remove-Item Env:\http_proxy -ErrorAction SilentlyContinue
Remove-Item Env:\https_proxy -ErrorAction SilentlyContinue
Remove-Item Env:\HTTP_PROXY -ErrorAction SilentlyContinue
Remove-Item Env:\HTTPS_PROXY -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Done. Review $LOG_FILE.mitm.err for any TLS errors." -ForegroundColor Cyan
Write-Host "If chat was NOT captured, check for 'TLS handshake' errors in the log."
Write-Host ""
Write-Host "Next steps:"
Write-Host "  - If SUCCESS: proceed to Stage 3 (write RECON findings + matrix)"
Write-Host "  - If FAIL: try Option B (MCP middleware) or Option C (CDP)"

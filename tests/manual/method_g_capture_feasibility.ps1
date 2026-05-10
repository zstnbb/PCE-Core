# Method G: Capture Feasibility Probe (ADR-018 Phase 1)
#
# One-shot probe that tests three independent hypotheses for closed-source
# Store-distributed Electron AI applications (Claude Desktop / ChatGPT Desktop).
# The combined verdict locks the operating scenario per ADR-018 §3.6
# (optimistic / neutral / pessimistic) and gates Phase 3 - 5 implementation.
#
# Hypotheses tested:
#   H2  Does the app honor Windows system proxy?
#       (Sufficient pre-condition for L1 mitmproxy capture, ADR-018 §3.5 Axis "Chat-region real-time")
#   H3  Does the app write `SSLKEYLOGFILE` env var?
#       (Sufficient pre-condition for A2 passive TLS keylog capture, ADR-018 §3.5)
#   H4  Is the Electron Fuse `EnableNodeOptionsEnvironmentVariable` enabled?
#       (Sufficient pre-condition for B1 NODE_OPTIONS preload capture, ADR-018 §3.5)
#
# Out of scope (run a separate mitmdump-based probe if needed):
#   - cert pinning verdict ("honors proxy" != "no pinning")
#   - actual capture round-trip
#
# Outputs:
#   tests/manual/_reports/method_g_<timestamp>.json
#
# Usage:
#   pwsh -File tests/manual/method_g_capture_feasibility.ps1
#   pwsh -File tests/manual/method_g_capture_feasibility.ps1 -WaitSeconds 90
#   pwsh -File tests/manual/method_g_capture_feasibility.ps1 -SkipH2H3      # H4 only

[CmdletBinding()]
param(
    [int]$WaitSeconds = 60,
    [switch]$SkipH4,
    [switch]$SkipH2H3,
    [int]$ProxyPort = 8080
)

$ErrorActionPreference = 'Stop'

$Timestamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
$ReportsDir = Join-Path $PSScriptRoot '_reports'
$ReportFile = Join-Path $ReportsDir "method_g_$Timestamp.json"
New-Item -ItemType Directory -Path $ReportsDir -Force | Out-Null

$Verdict = [ordered]@{
    timestamp           = $Timestamp
    host                = $env:COMPUTERNAME
    user                = $env:USERNAME
    claude_install      = $null
    chatgpt_install     = $null
    local_cache         = $null
    h2 = [ordered]@{ tested = $false; verdict = $null; evidence = $null }
    h3 = [ordered]@{ tested = $false; verdict = $null; evidence = $null }
    h4 = [ordered]@{ tested = $false; verdict = $null; evidence = $null }
    summary = [ordered]@{
        scenario              = $null
        chat_coverage_estimate = $null
        cowork_coverage        = '~95% (M-plane independent of H2/H3/H4)'
        code_coverage          = '~90% (H1 CLI wrap independent)'
    }
}

Write-Host ''
Write-Host '================================================================' -ForegroundColor Cyan
Write-Host ' ADR-018 Phase 1 - Method G: Capture Feasibility Probe' -ForegroundColor Cyan
Write-Host '================================================================' -ForegroundColor Cyan
Write-Host "  Timestamp: $Timestamp"
Write-Host "  Report:    $ReportFile"
Write-Host ''

# ─────────────────────────────────────────────────────────────────
# Pre-flight: detect installations + cache topology
# ─────────────────────────────────────────────────────────────────

Write-Host '[Pre-flight] detecting installations ...' -ForegroundColor Yellow

$claudePkg = Get-AppxPackage -Name 'Claude*' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($claudePkg) {
    $claudeExe = Get-ChildItem -Path $claudePkg.InstallLocation -Filter 'Claude.exe' -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $Verdict.claude_install = [ordered]@{
        channel           = 'MSIX'
        package_full_name = $claudePkg.PackageFullName
        install_location  = $claudePkg.InstallLocation
        version           = $claudePkg.Version.ToString()
        exe_path          = if ($claudeExe) { $claudeExe.FullName } else { $null }
        capabilities      = if ($claudePkg.Capabilities) { @($claudePkg.Capabilities) } else { @() }
    }
    Write-Host ("  Claude Desktop: MSIX v{0}" -f $claudePkg.Version) -ForegroundColor Green
    Write-Host ("    install: {0}" -f $claudePkg.InstallLocation)
    Write-Host ("    exe:     {0}" -f $(if ($claudeExe) { $claudeExe.FullName } else { '(NOT FOUND)' }))
} else {
    $squirrelPath = Join-Path $env:LOCALAPPDATA 'AnthropicClaude\Claude.exe'
    if (Test-Path $squirrelPath) {
        $Verdict.claude_install = [ordered]@{
            channel  = 'Squirrel'
            exe_path = $squirrelPath
            version  = (Get-Item $squirrelPath).VersionInfo.ProductVersion
        }
        Write-Host "  Claude Desktop: Squirrel (legacy) at $squirrelPath" -ForegroundColor Green
    } else {
        Write-Host '  Claude Desktop: NOT INSTALLED' -ForegroundColor DarkGray
    }
}

$chatgptPkg = Get-AppxPackage -Name '*ChatGPT*' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($chatgptPkg) {
    $chatgptExe = Get-ChildItem -Path $chatgptPkg.InstallLocation -Filter '*.exe' -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'ChatGPT|OpenAI' } | Select-Object -First 1
    $Verdict.chatgpt_install = [ordered]@{
        channel           = 'MSIX'
        package_full_name = $chatgptPkg.PackageFullName
        install_location  = $chatgptPkg.InstallLocation
        version           = $chatgptPkg.Version.ToString()
        exe_path          = if ($chatgptExe) { $chatgptExe.FullName } else { $null }
    }
    Write-Host ("  ChatGPT Desktop: MSIX v{0}" -f $chatgptPkg.Version) -ForegroundColor Green
} else {
    Write-Host '  ChatGPT Desktop: NOT detected' -ForegroundColor DarkGray
}

# Cache topology (P1 Claude only — P2 path varies per OpenAI's MSIX layout)
if ($claudePkg) {
    $cacheRoot = Join-Path $env:LOCALAPPDATA `
        ("Packages\{0}\LocalCache\Roaming\Claude" -f $claudePkg.PackageFamilyName)
    if (Test-Path $cacheRoot) {
        $cacheDirs = [ordered]@{}
        foreach ($name in 'Local Storage', 'IndexedDB', 'Cache', 'Network',
                          'local-agent-mode-sessions', 'vm_bundles',
                          'claude-code', 'claude-code-vm', 'logs', 'Session Storage') {
            $p = Join-Path $cacheRoot $name
            if (Test-Path $p) {
                $files = @(Get-ChildItem -Path $p -Recurse -File -ErrorAction SilentlyContinue)
                $size  = ($files | Measure-Object -Property Length -Sum).Sum
                $cacheDirs[$name] = [ordered]@{
                    exists    = $true
                    size_bytes = [int64]$size
                    file_count = $files.Count
                }
            } else {
                $cacheDirs[$name] = [ordered]@{ exists = $false }
            }
        }
        $Verdict.local_cache = [ordered]@{
            root        = $cacheRoot
            directories = $cacheDirs
        }
        Write-Host '  LocalCache scanned:'
        foreach ($k in $cacheDirs.Keys) {
            $d = $cacheDirs[$k]
            if ($d.exists) {
                Write-Host ("    {0,-32} {1,12:N0} bytes  {2,5} files" -f $k, $d.size_bytes, $d.file_count) -ForegroundColor DarkGreen
            } else {
                Write-Host ("    {0,-32} (absent)" -f $k) -ForegroundColor DarkGray
            }
        }
    }
}

Write-Host ''

# ─────────────────────────────────────────────────────────────────
# H4: Electron Fuses (fully automated)
# ─────────────────────────────────────────────────────────────────

if (-not $SkipH4 -and $Verdict.claude_install -and $Verdict.claude_install.exe_path) {
    Write-Host '[H4] Reading Electron Fuses from Claude.exe ...' -ForegroundColor Yellow

    # Electron Fuses sentinel: ASCII "dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX"
    # Layout immediately after sentinel:
    #   1 byte: fuse format version (0x01 known)
    #   1 byte: fuse count
    #   N bytes: fuse states, where each byte is
    #       0x00=REMOVED  0x01=DISABLED  0x02=ENABLED  0x03=INHERIT
    # Fuse name order (Electron 22+):
    #   [0] RunAsNode
    #   [1] EnableCookieEncryption
    #   [2] EnableNodeOptionsEnvironmentVariable    <-- H4 target
    #   [3] EnableNodeCliInspectArguments
    #   [4] EnableEmbeddedAsarIntegrityValidation
    #   [5] OnlyLoadAppFromAsar
    #   [6] LoadBrowserProcessSpecificV8Snapshot
    #   [7] GrantFileProtocolExtraPrivileges
    #   [8] EnableCookieEncryption (may extend in newer Electron)

    $sentinel       = 'dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX'
    $sentinelBytes  = [Text.Encoding]::ASCII.GetBytes($sentinel)
    $exePath        = $Verdict.claude_install.exe_path
    $bytes          = [IO.File]::ReadAllBytes($exePath)

    $idx = -1
    $maxStart = $bytes.Length - $sentinelBytes.Length - 32
    for ($i = 0; $i -lt $maxStart; $i++) {
        $match = $true
        for ($j = 0; $j -lt $sentinelBytes.Length; $j++) {
            if ($bytes[$i + $j] -ne $sentinelBytes[$j]) { $match = $false; break }
        }
        if ($match) { $idx = $i; break }
    }

    if ($idx -ge 0) {
        $afterSentinel = $idx + $sentinelBytes.Length
        $version = [int]$bytes[$afterSentinel]
        # Field after version is documented as either fuse-count or first fuse;
        # we treat next 16 bytes as fuse states and clip to known names.
        $fuseRegion = $bytes[($afterSentinel + 1)..($afterSentinel + 16)]
        $fuseNames = @(
            'RunAsNode',
            'EnableCookieEncryption',
            'EnableNodeOptionsEnvironmentVariable',
            'EnableNodeCliInspectArguments',
            'EnableEmbeddedAsarIntegrityValidation',
            'OnlyLoadAppFromAsar',
            'LoadBrowserProcessSpecificV8Snapshot',
            'GrantFileProtocolExtraPrivileges'
        )
        $stateNames = @{
            0 = 'REMOVED'
            1 = 'DISABLED'
            2 = 'ENABLED'
            3 = 'INHERIT'
        }

        $fuses = [ordered]@{}
        for ($i = 0; $i -lt $fuseNames.Count; $i++) {
            $val = [int]$fuseRegion[$i]
            $state = if ($stateNames.ContainsKey($val)) { $stateNames[$val] } else { "UNKNOWN_0x{0:X2}" -f $val }
            $fuses[$fuseNames[$i]] = [ordered]@{ value = $val; state = $state }
        }

        $nodeOptionsState = $fuses['EnableNodeOptionsEnvironmentVariable'].state
        $h4Verdict = switch ($nodeOptionsState) {
            'ENABLED'  { 'NODE_OPTIONS_HONORED' }
            'DISABLED' { 'NODE_OPTIONS_BLOCKED' }
            'REMOVED'  { 'FUSE_REMOVED' }
            default    { "INDETERMINATE_$nodeOptionsState" }
        }

        $Verdict.h4.tested = $true
        $Verdict.h4.verdict = $h4Verdict
        $Verdict.h4.evidence = [ordered]@{
            sentinel_offset      = $idx
            fuse_format_version  = $version
            all_fuses            = $fuses
        }

        Write-Host ("  Sentinel at offset 0x{0:X}, version=0x{1:X2}" -f $idx, $version)
        foreach ($name in $fuseNames) {
            $color = if ($fuses[$name].state -eq 'ENABLED') { 'Green' }
                     elseif ($fuses[$name].state -eq 'DISABLED') { 'Red' }
                     else { 'Gray' }
            Write-Host ("    {0,-50} {1,-10} (0x{2:X2})" -f $name, $fuses[$name].state, $fuses[$name].value) -ForegroundColor $color
        }
        Write-Host ''
        Write-Host ("  >>> H4 verdict: {0} <<<" -f $h4Verdict) -ForegroundColor Cyan
    } else {
        $Verdict.h4.tested = $true
        $Verdict.h4.verdict = 'SENTINEL_NOT_FOUND'
        $Verdict.h4.evidence = [ordered]@{
            note = 'Electron fuses sentinel not present; either non-fused build or sentinel obfuscated. Treat as INDETERMINATE.'
        }
        Write-Host '  Fuse sentinel NOT FOUND in Claude.exe' -ForegroundColor Red
    }
} elseif ($SkipH4) {
    Write-Host '[H4] SKIPPED (-SkipH4 flag)' -ForegroundColor DarkGray
} else {
    Write-Host '[H4] SKIPPED (Claude not installed)' -ForegroundColor DarkGray
}

Write-Host ''

# ─────────────────────────────────────────────────────────────────
# H2 + H3: combined system proxy + SSLKEYLOGFILE probe (semi-auto)
# ─────────────────────────────────────────────────────────────────

if (-not $SkipH2H3) {
    Write-Host '[H2+H3] Combined system proxy + SSLKEYLOGFILE probe' -ForegroundColor Yellow
    Write-Host ''

    $keylogFile = Join-Path $env:TEMP "pce_method_g_keylog_$Timestamp.txt"
    if (Test-Path $keylogFile) { Remove-Item $keylogFile -Force }

    # ── Backup proxy state for restoration ─────────────────────────
    $regKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
    $priorProxyServer = (Get-ItemProperty -Path $regKey -Name ProxyServer -ErrorAction SilentlyContinue).ProxyServer
    $priorProxyEnable = (Get-ItemProperty -Path $regKey -Name ProxyEnable -ErrorAction SilentlyContinue).ProxyEnable
    $priorKeylogEnv   = [Environment]::GetEnvironmentVariable('SSLKEYLOGFILE', 'User')

    # ── Try to bind TCP listener on $ProxyPort (records CONNECT bytes) ──
    $listener = $null
    $listenerError = $null
    try {
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $ProxyPort)
        $listener.Start()
    } catch {
        $listenerError = $_.Exception.Message
        $listener = $null
    }

    $observedConnections = New-Object 'System.Collections.Generic.List[hashtable]'

    try {
        # ── Apply proxy + keylog env ────────────────────────────────
        Set-ItemProperty -Path $regKey -Name ProxyServer -Value "127.0.0.1:$ProxyPort"
        Set-ItemProperty -Path $regKey -Name ProxyEnable -Value 1
        [Environment]::SetEnvironmentVariable('SSLKEYLOGFILE', $keylogFile, 'User')

        if ($listener) {
            Write-Host "  TCP listener bound on 127.0.0.1:$ProxyPort (records CONNECT bytes)" -ForegroundColor DarkGreen
        } else {
            Write-Host "  TCP listener bind FAILED on $ProxyPort ($listenerError)" -ForegroundColor Red
            Write-Host '    H2 will rely on Get-NetTCPConnection snapshots only (less precise)'
        }
        Write-Host "  System proxy applied: 127.0.0.1:$ProxyPort"
        Write-Host "  SSLKEYLOGFILE applied: $keylogFile"
        Write-Host ''

        Write-Host '  >>> ACTION REQUIRED <<<' -ForegroundColor Magenta
        Write-Host '  1. CLOSE Claude Desktop completely (right-click tray icon -> Quit Claude).'
        Write-Host '  2. REOPEN Claude Desktop fresh.'
        Write-Host '  3. SEND any single message (e.g. "hi") and wait for the response to start.'
        Write-Host "     (SSLKEYLOGFILE only captures keys for connections opened AFTER the env was set;"
        Write-Host "      proxy traffic is also only sent if the new instance honors system proxy at startup.)"
        Write-Host ''
        Write-Host "  Probing for $WaitSeconds seconds. Press Ctrl+C to abort (cleanup is idempotent)."
        Write-Host ''

        # ── Polling loop ───────────────────────────────────────────
        $endTime = (Get-Date).AddSeconds($WaitSeconds)
        while ((Get-Date) -lt $endTime) {
            if ($listener -and $listener.Pending()) {
                try {
                    $client = $listener.AcceptTcpClient()
                    $stream = $client.GetStream()
                    $stream.ReadTimeout = 600
                    $buf = New-Object byte[] 1024
                    $n = 0
                    try { $n = $stream.Read($buf, 0, $buf.Length) } catch {}
                    $first = if ($n -gt 0) { [Text.Encoding]::ASCII.GetString($buf, 0, $n) } else { '' }
                    $sample = if ($first.Length -gt 200) { $first.Substring(0, 200) + '...' } else { $first }
                    $observedConnections.Add(@{
                        ts          = Get-Date -Format 'HH:mm:ss.fff'
                        remote      = $client.Client.RemoteEndPoint.ToString()
                        first_bytes = $sample
                    })
                    $client.Close()
                } catch {}
            }

            $remaining = [int]($endTime - (Get-Date)).TotalSeconds
            if ($remaining -lt 0) { $remaining = 0 }
            Write-Host ("`r    Probing ... {0}s remaining   ({1} CONNECT events seen)   " -f $remaining, $observedConnections.Count) -NoNewline
            Start-Sleep -Milliseconds 250
        }
        Write-Host ''
    }
    finally {
        # ── ALWAYS restore env + proxy + listener ────────────────────
        if ($listener) { try { $listener.Stop() } catch {} }

        if ($null -ne $priorProxyServer) {
            Set-ItemProperty -Path $regKey -Name ProxyServer -Value $priorProxyServer
        } else {
            Remove-ItemProperty -Path $regKey -Name ProxyServer -ErrorAction SilentlyContinue
        }
        $enableValue = if ($priorProxyEnable) { [int]$priorProxyEnable } else { 0 }
        Set-ItemProperty -Path $regKey -Name ProxyEnable -Value $enableValue

        if ($null -ne $priorKeylogEnv) {
            [Environment]::SetEnvironmentVariable('SSLKEYLOGFILE', $priorKeylogEnv, 'User')
        } else {
            [Environment]::SetEnvironmentVariable('SSLKEYLOGFILE', $null, 'User')
        }
        Write-Host '  Cleanup: proxy registry + SSLKEYLOGFILE env restored.' -ForegroundColor DarkGreen
    }

    Write-Host ''

    # ── H2 verdict ─────────────────────────────────────────────────
    $aiHostPattern = 'api\.anthropic\.com|claude\.ai|chatgpt\.com|api\.openai\.com|cdn\.openai\.com|sentry\.io'
    $aiCONNECTs = @($observedConnections | Where-Object { $_.first_bytes -match $aiHostPattern })
    $totalCONNECTs = $observedConnections.Count

    if ($aiCONNECTs.Count -gt 0) {
        $Verdict.h2.tested = $true
        $Verdict.h2.verdict = 'HONORS_SYSTEM_PROXY'
        $Verdict.h2.evidence = [ordered]@{
            total_connect_events     = $totalCONNECTs
            ai_host_connect_events   = $aiCONNECTs.Count
            ai_host_samples          = @($aiCONNECTs | Select-Object -First 3)
            note                     = 'PROVES: app routes traffic through system proxy. DOES NOT PROVE: app accepts third-party CA (cert pinning verdict needs separate mitmdump-based test, see ADR-018 Phase 1.5).'
        }
        Write-Host ("  H2: HONORS_SYSTEM_PROXY ({0} AI-host CONNECT events; {1} total)" -f $aiCONNECTs.Count, $totalCONNECTs) -ForegroundColor Green
        Write-Host '       Pinning verdict still needs follow-up mitmdump+CA round-trip test.'
    }
    elseif ($totalCONNECTs -gt 0) {
        $Verdict.h2.tested = $true
        $Verdict.h2.verdict = 'PROXY_TRAFFIC_NOT_FROM_AI_APP'
        $Verdict.h2.evidence = [ordered]@{
            total_connect_events = $totalCONNECTs
            samples              = @($observedConnections | Select-Object -First 5)
            note                 = 'Some app honors system proxy but not the target Claude/ChatGPT. User may not have triggered an AI request.'
        }
        Write-Host ("  H2: PROXY_TRAFFIC_NOT_FROM_AI_APP ({0} non-AI events). Did you actually send a message in Claude?" -f $totalCONNECTs) -ForegroundColor Yellow
    }
    else {
        $Verdict.h2.tested = $true
        $Verdict.h2.verdict = 'NO_PROXY_TRAFFIC'
        $Verdict.h2.evidence = [ordered]@{
            total_connect_events = 0
            note                 = 'No CONNECT events observed. Either the app does not honor system proxy, the user did not send a message, or the listener bound to wrong port.'
        }
        Write-Host '  H2: NO_PROXY_TRAFFIC. Re-run with longer -WaitSeconds and verify a message was actually sent.' -ForegroundColor Red
    }

    # ── H3 verdict ─────────────────────────────────────────────────
    if (Test-Path $keylogFile) {
        $size = (Get-Item $keylogFile).Length
        $head = if ($size -gt 0) {
            (Get-Content $keylogFile -TotalCount 5 -ErrorAction SilentlyContinue) -join "`n"
        } else { '' }
        if ($size -gt 0) {
            # Validate it looks like NSS key log format ("CLIENT_RANDOM ..." or "SERVER_HANDSHAKE_TRAFFIC_SECRET ...")
            $isNssFormat = $head -match 'CLIENT_RANDOM|SERVER_HANDSHAKE|CLIENT_HANDSHAKE|EXPORTER_SECRET'
            if ($isNssFormat) {
                $Verdict.h3.tested = $true
                $Verdict.h3.verdict = 'WRITES_KEYLOG'
                $Verdict.h3.evidence = [ordered]@{
                    file        = $keylogFile
                    size_bytes  = [int64]$size
                    line_count  = ($head -split "`n").Count
                    head_sample = $head
                }
                Write-Host ("  H3: WRITES_KEYLOG ({0} bytes, NSS-format detected)" -f $size) -ForegroundColor Green
            } else {
                $Verdict.h3.tested = $true
                $Verdict.h3.verdict = 'KEYLOG_FILE_BUT_UNRECOGNIZED_FORMAT'
                $Verdict.h3.evidence = [ordered]@{
                    file        = $keylogFile
                    size_bytes  = [int64]$size
                    head_sample = $head
                }
                Write-Host ("  H3: KEYLOG_FILE_BUT_UNRECOGNIZED_FORMAT ({0} bytes, not NSS)" -f $size) -ForegroundColor Yellow
            }
        } else {
            $Verdict.h3.tested = $true
            $Verdict.h3.verdict = 'FILE_CREATED_BUT_EMPTY'
            $Verdict.h3.evidence = [ordered]@{ file = $keylogFile; size_bytes = 0 }
            Write-Host '  H3: FILE_CREATED_BUT_EMPTY (env honored but no key log written)' -ForegroundColor Yellow
        }
    } else {
        $Verdict.h3.tested = $true
        $Verdict.h3.verdict = 'NO_KEYLOG_FILE'
        $Verdict.h3.evidence = [ordered]@{
            file_attempted = $keylogFile
            exists         = $false
            note           = 'App did not honor SSLKEYLOGFILE env var. May be Electron security policy or the env was not present at app start (close-and-reopen required).'
        }
        Write-Host '  H3: NO_KEYLOG_FILE (SSLKEYLOGFILE not honored)' -ForegroundColor Red
    }
} else {
    Write-Host '[H2+H3] SKIPPED (-SkipH2H3 flag)' -ForegroundColor DarkGray
}

Write-Host ''

# ─────────────────────────────────────────────────────────────────
# Scenario classification per ADR-018 §3.6
# ─────────────────────────────────────────────────────────────────

$h2v = $Verdict.h2.verdict
$h3v = $Verdict.h3.verdict
$h4v = $Verdict.h4.verdict

# Determine scenario per ADR-018 §3.6 truth table
$scenario  = 'INDETERMINATE'
$coverage  = 'unknown'
$reasoning = @()

$h2_pass = ($h2v -eq 'HONORS_SYSTEM_PROXY')
$h3_pass = ($h3v -eq 'WRITES_KEYLOG')
$h4_pass = ($h4v -eq 'NODE_OPTIONS_HONORED')

if ($h2_pass -and $h4_pass) {
    $scenario  = 'OPTIMISTIC'
    $coverage  = '~95% T1 (A1 mitm + B1 NODE_OPTIONS preload + persist + UIA + M)'
    $reasoning += 'H2 PASS: L1 mitmproxy main route is viable (pin verdict pending Phase 1.5)'
    $reasoning += 'H4 PASS: B1 NODE_OPTIONS preload also viable, providing redundant Chat capture'
}
elseif ($h2_pass) {
    $scenario  = 'NEUTRAL'
    $coverage  = '~92% T1 (A1 mitm + persist + UIA + M)'
    $reasoning += 'H2 PASS: L1 mitmproxy main route is viable'
    $reasoning += 'H4 FAIL: B1 NODE_OPTIONS not viable (Electron Fuse blocks)'
}
elseif ($h3_pass) {
    $scenario  = 'NEUTRAL_VIA_KEYLOG'
    $coverage  = '~88% T1 (A2 SSLKEYLOGFILE + persist + UIA + M)'
    $reasoning += 'H2 FAIL: L1 mitm fails (proxy not honored or pinning blocks)'
    $reasoning += 'H3 PASS: A2 SSLKEYLOGFILE is the Chat-region primary route'
}
else {
    $scenario  = 'PESSIMISTIC'
    $coverage  = '~75% T2-dominant (persist + UIA + M; Chat real-time absent)'
    $reasoning += 'H2/H3/H4 all fail. Chat real-time capture not viable on this app+channel.'
    $reasoning += 'L3g persistence + L4b UIA fallback + M-plane MCP still operational.'
}

$Verdict.summary.scenario = $scenario
$Verdict.summary.chat_coverage_estimate = $coverage
$Verdict.summary.reasoning = $reasoning

Write-Host '================================================================' -ForegroundColor Cyan
Write-Host ' VERDICT SUMMARY' -ForegroundColor Cyan
Write-Host '================================================================' -ForegroundColor Cyan
Write-Host ("  H2 (system proxy):      {0}" -f $h2v)
Write-Host ("  H3 (SSLKEYLOGFILE):     {0}" -f $h3v)
Write-Host ("  H4 (Electron Fuses):    {0}" -f $h4v)
Write-Host ''
Write-Host ("  Scenario per ADR-018:   {0}" -f $scenario) -ForegroundColor Magenta
Write-Host ("  Chat coverage estimate: {0}" -f $coverage)
Write-Host ("  Cowork coverage:        {0}" -f $Verdict.summary.cowork_coverage)
Write-Host ("  Code coverage:          {0}" -f $Verdict.summary.code_coverage)
Write-Host ''
Write-Host '  Reasoning:'
foreach ($r in $reasoning) { Write-Host ("    - {0}" -f $r) }
Write-Host ''

# ─────────────────────────────────────────────────────────────────
# Persist report
# ─────────────────────────────────────────────────────────────────

$Verdict | ConvertTo-Json -Depth 10 | Set-Content -Path $ReportFile -Encoding UTF8
Write-Host "Report saved: $ReportFile" -ForegroundColor Green
Write-Host ''
Write-Host 'Next steps per ADR-018 §3.8:'
Write-Host '  Phase 2 (always proceed): pce_mcp/.mcpb packaging completion'
Write-Host '  Phase 3 (always proceed): pce_persistence_watcher/ (L3g)'
Write-Host '  Phase 4 (always proceed): pce_cli_wrapper/ (H1)'
if ($scenario -eq 'NEUTRAL_VIA_KEYLOG') {
    Write-Host '  Phase 5 (PROMOTED):      pce_proxy/keylog_mode.py (A2) — H3 PASS makes this the Chat-region primary route'
} elseif ($h3_pass) {
    Write-Host '  Phase 5 (proceed):       pce_proxy/keylog_mode.py (A2) — supplementary'
} else {
    Write-Host '  Phase 5 (skip for now):  SSLKEYLOGFILE not honored on this build'
}
if (-not $h2_pass -and -not $h3_pass) {
    Write-Host '  ALSO consider: promote L4b Accessibility (UIA) from P6 to P5.B given Chat real-time gap' -ForegroundColor Yellow
}

exit 0

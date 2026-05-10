# Method E: ApplicationActivationManager COM API
# Last-resort experiment to launch MSIX Claude Desktop with --remote-debugging-port=9222
# via low-level Win32 COM API instead of PowerShell cmdlet.
#
# AppUserModelID: Claude_pzs8sxrjxfjjc!Claude  (per Get-StartApps)
# Args:           --remote-debugging-port=9222
#
# Outcome interpretation:
#   exit 0 + CDP_READY     => Method E works, update launcher.py to use this API
#   exit 0 + CDP_NOT_READY => Method E launches but args don't reach Electron Chromium argv
#   non-zero exit          => API call failed, ApplicationActivationManager rejects

$ErrorActionPreference = "Stop"

$AUMID = "Claude_pzs8sxrjxfjjc!Claude"
$ARGS_STR = "--remote-debugging-port=9222"

Write-Output "=== Method E: ApplicationActivationManager COM API ==="
Write-Output "AUMID: $AUMID"
Write-Output "Args:  $ARGS_STR"
Write-Output ""

# Compile the IApplicationActivationManager COM interop type.
# Reference: https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nn-shobjidl_core-iapplicationactivationmanager
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

namespace MethodE {
    public enum ActivateOptions : uint {
        None             = 0x00000000,
        DesignMode       = 0x00000001,
        NoErrorUI        = 0x00000002,
        NoSplashScreen   = 0x00000004,
    }

    [ComImport]
    [Guid("2e941141-7f97-4756-ba1d-9decde894a3d")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IApplicationActivationManager {
        [PreserveSig]
        int ActivateApplication(
            [In] string appUserModelId,
            [In] string arguments,
            [In] ActivateOptions options,
            [Out] out uint processId);
    }

    // Wrapper: PowerShell can't cast __ComObject to a non-IDispatch interface,
    // so we do the cast in compiled C# where COM runtime applies QueryInterface.
    public static class AamLauncher {
        public static int Launch(string aumid, string args, out uint pid) {
            var clsid = new Guid("45BA127D-10A8-46EA-8AB7-56EA9078943C");
            var type = Type.GetTypeFromCLSID(clsid);
            var instance = (IApplicationActivationManager)System.Activator.CreateInstance(type);
            return instance.ActivateApplication(aumid, args, ActivateOptions.None, out pid);
        }
    }
}
"@ -Language CSharp

Write-Output "[E1] type loaded"

# Invoke via the C# wrapper (COM cast happens inside compiled IL)
$processId = [uint32]0
$hresult = [MethodE.AamLauncher]::Launch($AUMID, $ARGS_STR, [ref]$processId)
Write-Output ("[E3] ActivateApplication returned HRESULT=0x{0:X8}, processId={1}" -f $hresult, $processId)

if ($hresult -ne 0) {
    Write-Output ""
    Write-Output "RESULT: API_REJECTED (HRESULT not S_OK)"
    exit 1
}

# Wait for Electron Chromium to come up
Write-Output "[E4] waiting 6s for Electron + Chromium init ..."
Start-Sleep -Seconds 6

$claude = Get-Process -Name claude -ErrorAction SilentlyContinue
if ($claude) {
    Write-Output ("[E5] claude_alive: {0} processes; PIDs: {1}" -f $claude.Count, ($claude.Id -join ","))
    $hasReturnedPid = $claude.Id -contains $processId
    Write-Output ("    returned PID $processId is among them: $hasReturnedPid")
} else {
    Write-Output "[E5] no claude processes alive — process exited or never started"
}

# Poll CDP /json/version for up to 12s
Write-Output ""
Write-Output "[E6] polling http://127.0.0.1:9222/json/version (max 12s) ..."
$cdpReady = $false
$cdpResp = $null
$deadline = (Get-Date).AddSeconds(12)
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:9222/json/version" -TimeoutSec 1 -ErrorAction Stop
        if ($r.StatusCode -eq 200) {
            $cdpReady = $true
            $cdpResp = $r.Content
            break
        }
    } catch {}
    Start-Sleep -Milliseconds 500
}

Write-Output ""
if ($cdpReady) {
    Write-Output "RESULT: SUCCESS — CDP endpoint responsive"
    Write-Output "/json/version response:"
    Write-Output $cdpResp
    exit 0
} else {
    Write-Output "RESULT: PARTIAL — process launched but CDP NOT on 9222"
    Write-Output "Means: args did NOT reach Electron Chromium argv (MSIX swallowed them)"
    exit 2
}

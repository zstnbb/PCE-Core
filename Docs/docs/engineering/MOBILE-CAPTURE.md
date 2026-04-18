# Mobile capture setup (P4.5)

PCE's mitmproxy listener on the workstation is the only component you
actually need on the desktop side to capture LLM traffic from a phone
on the same Wi-Fi. This guide walks through the three setup steps —
configuring the phone's HTTP proxy, installing the mitmproxy CA, and
trusting it — and shows the tooling PCE ships to make each step a
one-minute job.

## TL;DR

1. Make sure `pce` proxy is running on the desktop (`python pce.py
   proxy` or however you normally start it; defaults to listening on
   `0.0.0.0:8080` — if yours is bound to `127.0.0.1` set
   `PCE_PROXY_HOST=0.0.0.0`).
2. On the desktop, run:

   ```powershell
   python -m pce_core.mobile_wizard
   ```

   This prints an ASCII QR code with the deep-link URL, the proxy
   endpoint (`<LAN_IP>:8080`) and the cert download URL (`http://mitm.it`).

3. On the phone, configure the Wi-Fi network's HTTP proxy to the LAN
   IP + port, open `http://mitm.it`, and install + trust the
   downloaded certificate.

That's it. Every matched request/response will land in PCE's SQLite
database like any desktop-origin capture.

## Why mitmproxy needs a CA

Modern LLM apps ship over HTTPS. To see inside the TLS tunnel the
proxy has to terminate TLS on the workstation and re-encrypt it with a
certificate the phone trusts. The root of that certificate chain is
mitmproxy's per-installation CA, stored under:

- Linux / macOS: `~/.mitmproxy/mitmproxy-ca-cert.pem`
- Windows: `%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.cer`

The CA is generated on first run of mitmproxy and is **unique to your
machine**. Do not reuse someone else's — you'd be giving them a MitM
vector into your phone forever.

## Step 1 — point the phone's Wi-Fi proxy at PCE

The proxy URL is the workstation's LAN IPv4 address plus the port the
mitmproxy listener is bound to (8080 by default). You can discover
both by running the wizard:

```powershell
python -m pce_core.mobile_wizard --json
```

Sample output:

```json
{
  "proxy_host": "192.168.1.42",
  "proxy_port": 8080,
  "proxy_endpoint": "192.168.1.42:8080",
  "cert_url": "http://mitm.it",
  "setup_url": "pce-proxy://setup?host=192.168.1.42&port=8080",
  "instructions": ["..."]
}
```

Or hit the HTTP endpoint (useful if you're on the phone and want the
info in a browser):

```
GET http://<LAN_IP>:<INGEST_PORT>/api/v1/mobile_wizard/info
```

### iOS 17+

1. `Settings` → `Wi-Fi` → tap the **(i)** next to your network.
2. Scroll to `HTTP Proxy`, tap `Configure Proxy` → `Manual`.
3. Enter the workstation's LAN IP as `Server` and `8080` as `Port`.
4. Leave `Authentication` off.
5. Tap `Save`.

### Android 14+

1. `Settings` → `Network & internet` → `Internet` → long-press the
   current Wi-Fi → `Modify`.
2. Expand `Advanced options`.
3. Set `Proxy` to `Manual`, `Proxy hostname` to the LAN IP and
   `Proxy port` to `8080`.

## Step 2 — install the mitmproxy CA

With the proxy live the easiest route is the built-in helper page
mitmproxy serves at `http://mitm.it` whenever traffic is routed
through it.

### iOS

1. Open Safari (must be Safari — other browsers won't present the
   trust prompt) and go to `http://mitm.it`.
2. Tap the `iOS` row. Safari asks to download a configuration
   profile.
3. `Settings` → `General` → `VPN & Device Management` → tap the
   `mitmproxy` profile → `Install` (twice, and enter your passcode).
4. **Critical**: `Settings` → `General` → `About` → `Certificate
   Trust Settings` → enable the `mitmproxy` toggle. Without this
   step, HTTPS capture silently fails.

### Android

1. Open Chrome and go to `http://mitm.it`.
2. Tap the `Android` row, confirm the PEM download.
3. `Settings` → `Security` → `Encryption & credentials` → `Install
   a certificate` → `CA certificate` → pick the downloaded file.
4. Accept the warning. The cert appears under `Trusted credentials`.

**Android 7+ caveat**: user-installed CAs are no longer trusted by
apps whose `network_security_config.xml` opts out (which is most
popular apps). If you don't see app traffic, try a different LLM
client that does trust user CAs, or fall back to the embedded CDP
capture (`python -m pce_core.cdp`) on the desktop.

## Step 3 — verify

On the phone, open any LLM app (ChatGPT, Claude, Gemini…). On the
desktop:

```powershell
curl http://127.0.0.1:<INGEST_PORT>/api/v1/captures?limit=5
```

You should see new rows whose `source_id` is `proxy-default` and
whose `host` matches the app's API domain.

## QR code helpers

For faster onboarding the wizard renders the `setup_url` (a compact
deep-link) as a scannable QR code.

- **Terminal**: `python -m pce_core.mobile_wizard` prints ASCII art
  the phone's camera can scan from the monitor.
- **PNG**: `python -m pce_core.mobile_wizard --png ~/pce-setup.png`
  (requires `pip install qrcode[pil]`).
- **HTTP**:
  - `GET /api/v1/mobile_wizard/qr.txt` — ASCII art
  - `GET /api/v1/mobile_wizard/qr.png` — PNG (503 without
    `qrcode[pil]`)

The deep link uses the `pce-proxy://` scheme. Scanning opens the
phone's URL handler; iOS / Android show the raw URL with `host` and
`port` query params that the operator can copy into the Wi-Fi dialog.

## Turning it off

- **Remove proxy on phone**: Wi-Fi settings → proxy → `Off`.
- **Remove trusted CA**:
  - iOS: `General` → `VPN & Device Management` → profile →
    `Remove Profile`.
  - Android: `Security` → `Encryption & credentials` → `User
    credentials` → tap the cert → `Remove`.
- **Stop PCE**: kill the proxy process — traffic goes direct again.

Leaving the CA installed after you're done is a security risk; always
remove it when you're no longer capturing.

## Troubleshooting

| Symptom                                               | Fix                                                                 |
|:------------------------------------------------------|:--------------------------------------------------------------------|
| Phone shows "cannot connect" after proxy set          | Desktop proxy bound to `127.0.0.1`; export `PCE_PROXY_HOST=0.0.0.0` |
| `http://mitm.it` redirects to search engine           | Phone is not routing through the proxy — double-check Wi-Fi config  |
| HTTPS sites say "not private" after cert install      | Forgot to enable the iOS `Certificate Trust Settings` toggle        |
| Specific app works in browser but not in its own app  | App pins its own CA (Android 7+); use CDP capture instead           |
| `python -m pce_core.mobile_wizard` picks wrong NIC    | `--ip 192.168.x.y` to force                                         |

## Privacy notes

- The deep-link URL embeds only the LAN IP + proxy port. It does not
  carry any capture data, credentials, or identifying info.
- All HTTPS interception happens on the workstation; the phone's CA
  never sees decrypted bytes.
- PCE redacts `Authorization`, `Cookie`, `X-API-Key` etc. before
  writing to SQLite, same as on the desktop path.

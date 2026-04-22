# Security Policy

PCE handles sensitive local data (your AI conversations, system proxy state, root CA certificates) on your machine. We take security reports seriously.

## Supported Versions

| Version | Status |
|---------|--------|
| v1.x (latest) | ✅ Actively supported |
| Pre-1.0 development builds | ❌ Not supported |

Once we are past v1.0, the last two minor versions will receive security patches.

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security vulnerabilities.**

Instead, use GitHub's built-in private vulnerability reporting:

→ **[Report a vulnerability](https://github.com/zstnbb/PCE-Core/security/advisories/new)** — routes privately to the repository maintainer. No email mailbox required.

(If you prefer email, leave a contact address in your first message on the advisory and we will follow up there.)

What to include in your report:

- Affected component (e.g., `pce_core/cert_wizard/manager.py`, browser extension manifest, dashboard endpoint)
- Affected version(s) / commit(s)
- Reproduction steps (minimal is best)
- Impact assessment (what an attacker could do)
- Your suggestion for a fix (optional but appreciated)

### Our Response

- **Initial acknowledgment**: within 48 hours
- **Triage and severity assessment**: within 5 business days
- **Fix timeline**: depends on severity (see below)
- **Public disclosure**: coordinated with reporter, standard 90-day window

### Severity Levels

| Severity | Example | Target Fix |
|----------|---------|-----------|
| Critical | RCE, data exfiltration, cert hijack | 7 days |
| High | Privilege escalation, sensitive data leak | 30 days |
| Medium | Local DoS, cert trust bypass | 60 days |
| Low | Information disclosure, UI issues | 90 days |

After a fix is released, we will publish a GitHub Security Advisory with CVE assignment (where applicable) and credit the reporter (if they choose).

## Security-Sensitive Areas

Reports in these areas are prioritized:

1. **Certificate management** — `pce_core/cert_wizard/` handles root CA install/uninstall. A bug here could compromise the user's trust store.
2. **System proxy control** — `pce_core/proxy_toggle/` writes to OS-level proxy settings. Incorrect logic could leave a persistent proxy in place.
3. **Local HTTP endpoints** — `pce_core/server.py` exposes the Ingest API and dashboard on `127.0.0.1`. While local-only by default, any path traversal / SSRF / auth bypass is critical.
4. **Browser extension** — `pce_browser_extension_wxt/` runs with host permissions across multiple AI domains. Any XSS, permission leak, or data exfiltration in the extension is high-severity.
5. **Capture event pipeline** — `pce_core/normalizer/` and `pce_core/redact.py` handle potentially sensitive conversation content. Redaction bypass or injection is high-severity.

## Out of Scope

- Vulnerabilities requiring physical access to an already-compromised machine
- Social engineering against PCE users
- Denial of service via resource exhaustion that requires privileged local access
- Issues in third-party dependencies — please report those upstream (we will track downstream)
- Issues in the Pro edition (closed-source; separate disclosure contact)

## Hall of Fame

We will publicly recognize (with permission) security researchers who responsibly disclose issues.

_No entries yet — be the first!_

## PGP Key

GitHub private advisories are end-to-end encrypted between reporter and maintainer (TLS in transit, GitHub-managed storage). A separate PGP key is therefore not published. Reports outside the advisory flow are accepted but will be treated as public unless you specify otherwise.

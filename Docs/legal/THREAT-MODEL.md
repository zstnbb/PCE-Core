# PCE Legal Threat Model

> **Status**: canonical — risk frame for legal-posture decisions.
>
> **Adopted**: 2026-05-12 (P5.C.0 contract freeze, per ADR-019 §3.5).
>
> **Authority**:
> - `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-019-maintenance-as-first-class-concern.md` §3.5 Governance Artefact 5
> - `@f:\INVENTION\You.Inc\PCE Core\Docs\legal\CEASE-AND-DESIST-RESPONSE.md` (operational playbook)
>
> **Audience**: project owner + maintainers + legal counsel (when retained) + contributors evaluating new capture techniques.
>
> **Not legal advice**: This document is a structured engineering risk model. It is NOT legal advice. Any binding legal posture requires counsel review.

---

## 0. Threat Model Anchor

> The goal of this threat model is **not** to prove PCE is invulnerable to lawsuits. The goal is to **score every capture technique by its specific legal exposure**, so that:
>
> 1. High-risk techniques never enter the OSS repo.
> 2. Each merged feature has a documented good-faith justification.
> 3. When a notice arrives, the response is calibrated to the actual risk surface — not improvised.

---

## 1. PCE's Baseline Legal Posture

### 1.1 What we are

- **Software publisher**, not a service operator.
- Code distributed under **Apache-2.0** (OSS) and a separate proprietary license (Pro, in a separate repo).
- **Local-first**: no PCE-operated server receives user data.
- **Record-not-intervention**: PCE never modifies upstream requests or responses (ADR-001).
- **User-installed CA**: any TLS interception is on the user's own machine, with the user's explicit consent (ADR-002 onboarding wizard).

### 1.2 What we explicitly are NOT

- Not a SaaS — no hosted endpoint receives, stores, or processes user data we do not control.
- Not an authentication proxy — PCE never holds, transmits, or rotates user credentials for AI services.
- Not a data broker — we sell no aggregated dataset.
- Not an "AI bypass" tool — PCE does not unblock geo-restrictions, rate limits, or content moderation. It records what the user already sees.

### 1.3 Closest legitimate analogues (used as legal-posture defenses)

| Analogue | What it does | Why it survives | Lessons for PCE |
|---|---|---|---|
| **Wireshark** | Captures network traffic on the user's machine | Tool, not service; user-controlled; widely used in compliance | Frame PCE as a "personal observability tool" |
| **Charles Proxy / Fiddler / mitmproxy** | TLS-MITM with self-signed CA on user's own box | User installs CA explicitly; user is the sole party intercepting their own traffic | PCE's L1 follows this pattern exactly |
| **Screen readers (NVDA, JAWS)** | Read on-screen content via OS Accessibility APIs | Disability accommodation; OS vendor sanctioned | PCE's L4b uses the same OS APIs |
| **Browser DevTools** | Reads DOM, network, performance | Built into the browser; vendor-sanctioned | PCE's L3a does no more than DevTools could |
| **Browser ad blockers** | Modify DOM at render | First-Amendment-protected speech (US); user-installed | PCE never modifies, only reads |
| **yt-dlp** | Downloads media that user can already see | Tool, not infringement; user-side; no platform key extracted | PCE's posture mirror — when YouTube DMCA'd yt-dlp, it was reinstated |

### 1.4 Closest dangerous analogues (used as anti-pattern)

| Analogue | What killed it | What PCE must avoid |
|---|---|---|
| **Power Ventures (Facebook 2009)** | Continued accessing FB after C&D; held user credentials | C&D response within 24h; never hold AI-service credentials |
| **3taps (Craigslist 2013)** | Sold scraped data; ignored C&D | Don't sell user data; don't ignore notices |
| **BrandTotal (Meta 2024)** | Browser extension scraping ads for sale | Don't aggregate captured data into a saleable product on the OSS side |
| **Hooks bypassing DRM (e.g. RIAA actions)** | Distributed circumvention code targeting protected content | L2 Frida / SSL-pinning bypass NEVER in OSS |

---

## 2. Threats by Legal Theory

For each theory: claim, who would assert it, statutory anchor, our defense, residual risk.

### 2.1 Computer Fraud and Abuse Act (CFAA, 18 U.S.C. §1030)

| Field | Value |
|---|---|
| Claim | "PCE accesses our servers without authorization or in excess of authorization." |
| Asserter | AI service providers (OpenAI, Anthropic, Google, Microsoft) |
| Statute | 18 U.S.C. §1030(a)(2), (a)(4) |
| Defense | (1) PCE itself does not access provider servers — the user's browser/app does, with the user's authenticated credentials. (2) **Van Buren v. United States (2021)** narrowed CFAA: a user with valid credentials accessing their own account is not "without authorization." (3) **hiQ Labs v. LinkedIn (2019)** — automated access to material the user can see is not CFAA. |
| Residual risk | If a vendor sends a C&D and we continue support, **Power Ventures (2016)** may apply (post-C&D access becomes "without authorization"). Mitigation: 24h kill-switch (`@f:\INVENTION\You.Inc\PCE Core\Docs\legal\CEASE-AND-DESIST-RESPONSE.md`). |

### 2.2 Digital Millennium Copyright Act (DMCA §1201, anti-circumvention)

| Field | Value |
|---|---|
| Claim | "PCE circumvents technological measures protecting copyrighted AI outputs." |
| Asserter | Any vendor who applies pinning, encryption, or tokenization "as a measure controlling access" |
| Statute | 17 U.S.C. §1201(a)(1), §1201(b) |
| Defense | (1) PCE Core does not break encryption — TLS terminates at the user-installed CA, with user consent (analogous to corporate proxies, which are §1201-immune by industry consensus). (2) DOM reading and OS Accessibility APIs are not "circumvention" — they consume already-decrypted output the OS makes available to the user. |
| Residual risk | **L2 Frida SSL-pinning bypass and L0 kernel redirection ARE arguably §1201 circumvention.** This is why ADR-010 / ADR-018 keep them in the Pro repo (separate license, separate distribution, no public OSS exposure). The OSS repo MUST NOT contain code that defeats pinning, removes binary integrity checks, or extracts pinned certificates. |

### 2.3 Tortious Interference with Contract

| Field | Value |
|---|---|
| Claim | "PCE encourages users to violate the AI vendor's Terms of Service, causing economic harm." |
| Asserter | AI vendor whose ToS prohibits "automated capture" or similar |
| Statute | Common-law tort (state-by-state) |
| Defense | (1) PCE does not market itself on bypassing ToS. (2) PCE's marketing language describes it as personal observability ("capture your own AI conversations"). (3) Many AI ToS are silent or ambiguous on personal capture; PCE is not invited or knowing assistance to a "breach." (4) The user is the contracting party — they are responsible for evaluating ToS. |
| Residual risk | If a vendor's ToS explicitly prohibits client-side capture and our marketing materials suggest defeating it, the claim strengthens. **Mitigation**: README/PRIVACY language reviewed for unambiguous "personal observability" framing; avoid words like "bypass," "unlock," "defeat." |

### 2.4 Trade Secret Misappropriation

| Field | Value |
|---|---|
| Claim | "AI model outputs reveal trade secrets (system prompts, fine-tune signatures); PCE stores and exposes them." |
| Asserter | AI vendor |
| Statute | Defend Trade Secrets Act (DTSA, 18 U.S.C. §1836); state UTSAs |
| Defense | (1) The user lawfully accessed the output — there is no "improper means." (2) Once a trade secret is disclosed to a customer without an NDA, it is no longer secret. (3) PCE stores the user's own conversation locally; PCE itself does not "use" or "disclose" the secret. |
| Residual risk | If a user **publicly publishes** captured outputs claiming they reveal a system prompt, the user (not PCE) bears that risk. **Mitigation**: PRIVACY.md explicitly disclaims responsibility for downstream republication; CONTRIBUTING.md prohibits accepting PRs whose stated purpose is exfiltrating system prompts. |

### 2.5 Copyright Infringement

| Field | Value |
|---|---|
| Claim | "AI outputs are copyrighted by the vendor; storing them is infringement." |
| Asserter | AI vendor |
| Statute | 17 U.S.C. §106 |
| Defense | (1) Most AI outputs are **not copyrightable** — current US Copyright Office guidance says machine-generated content lacks human authorship (Thaler v. Perlmutter, 2023). (2) Even if copyrightable, the user has a license (the vendor's ToS typically grants the user broad use rights to outputs). (3) Local single-user storage is fair use. |
| Residual risk | Ambiguous; depends on the specific vendor's ToS and the eventual evolution of AI copyright law. Low priority. |

### 2.6 Browser Extension / App Store Policy

| Field | Value |
|---|---|
| Claim | "Your extension violates Chrome Web Store / Microsoft Store policy." |
| Asserter | Google / Microsoft |
| Statute | Platform policy, not law |
| Defense | (1) Manifest declares only the AI hosts we touch; no `<all_urls>`. (2) Privacy disclosures match runtime behavior. (3) No remote code execution (all code shipped in extension). |
| Residual risk | Platform policies change. Mitigation: re-audit submission package quarterly; maintain `Docs/store/SUBMISSION-PLAYBOOK.md`. Sideload distribution is the fallback if a platform delists us. |

### 2.7 GDPR / CCPA / China PIPL

| Field | Value |
|---|---|
| Claim | "PCE processes personal data without lawful basis." |
| Asserter | EU DPAs, California AG, China CAC |
| Statute | GDPR Art. 5–7; CCPA §1798.100; PIPL Ch. II |
| Defense | (1) PCE is a tool; the **user is the data controller**, PCE is at most a data processor running on the user's own machine. (2) No data crosses borders or leaves the user's device. (3) PRIVACY.md gives the user full visibility into what is captured. (4) For Pro modules deployed by enterprises, the enterprise becomes the controller and PCE-Pro the processor — DPA template available on request. |
| Residual risk | Enterprise deployments require formal DPA. Individual use is functionally outside GDPR scope (Art. 2(2)(c) household exemption arguably applies). |

### 2.8 Wiretapping / Interception (US §2511, EU 2002/58)

| Field | Value |
|---|---|
| Claim | "PCE intercepts communications without consent." |
| Asserter | Theoretical — government, vendor |
| Statute | Wiretap Act, 18 U.S.C. §2511; ePrivacy Directive |
| Defense | One-party consent: the user is a party to the AI conversation and consents to recording it. US wiretap law universally permits one-party consent at the federal level and in 38 states. EU ePrivacy similarly permits a party to record their own communications. |
| Residual risk | Two-party-consent states (e.g. California, Illinois). Risk concentrated when users invite others into a session. **Mitigation**: PRIVACY.md instructs users to obtain consent before capturing multi-party AI conversations. |

### 2.9 Anti-Circumvention Variants Outside US

| Field | Value |
|---|---|
| Claim | EU Software Directive Art. 6, China Anti-Unfair Competition Law Art. 12, Japan Unfair Competition Prevention Act |
| Asserter | Foreign vendors / regulators |
| Defense | Same logical structure as DMCA §1201: PCE Core does not circumvent; high-risk techniques are Pro-only. |
| Residual risk | Jurisdiction-specific. If/when a foreign action arises, counsel-of-jurisdiction required. |

---

## 3. Threats by Capture Layer

This is the **enforceable matrix**. Every PR that touches a capture layer must be readable against this table.

| Layer | Module | Risk score | Primary theory if attacked | Allowed in OSS? | Required mitigations |
|---|---|---|---|---|---|
| **L0** Kernel redirector | `pce_agent_kernel/` | 🔴 P0 | DMCA §1201; CFAA; possibly criminal (anti-circumvention) | **NO** — Pro only | Signed binary distribution; explicit user consent at install; not bundled with OSS installer |
| **L1** TLS MITM proxy | `pce_proxy/`, `pce_core/cert_wizard/` | 🟡 P2 | Wiretap (multi-party); CFAA if vendor sends C&D | ✅ Yes | User-installed CA (no system-level injection); first-run wizard requires explicit consent; cert is named clearly so user can audit; per-host pinning-detection so we degrade rather than break |
| **L2** Frida SSL hook | `pce_agent_frida/` | 🔴 P0 | DMCA §1201 (defeats pinning) | **NO** — Pro only | Distributed as Pro signed binaries; user-initiated; not invoked from OSS code paths; documented as "for users with explicit testing/auditing rights" |
| **L3a** Browser extension (DOM) | `pce_browser_extension_wxt/` | 🟢 P3 | Platform policy; tortious interference | ✅ Yes | Manifest restricted to declared AI hosts; no `<all_urls>` in store build; DOM-read-only (no DOM modification beyond own UI) |
| **L3b** Electron preload injection | `pce_agent_electron/` | 🟡 P1 | DMCA §1201 if pinning is bypassed; tortious interference | **Pro only** (ADR-016 deferred) | If revived: explicit user consent; no binary integrity defeat; respect Electron Fuses (`runAsNode=false`) |
| **L3c** VS Code / IDE extension | `pce_vscode_ext/` (planned) | 🟢 P3 | Platform policy | ✅ Yes | Use only documented IDE extension APIs; no private API hooking |
| **L3d** CDP launcher | `pce_core/cdp/` | 🟢 P3 | None significant | ✅ Yes | Launches the user's own browser with `--remote-debugging-port`; user-explicit |
| **L3e** LiteLLM SDK / OTel | `pce_core/normalizer/genai_semconv.py` | 🟢 P3 | None | ✅ Yes | Standard observability; opt-in OTLP export |
| **L3f** MCP middleware | `pce_mcp/`, `pce_mcp_proxy/` | 🟢 P3 | None significant | ✅ Yes | Open protocol, vendor-sanctioned |
| **L3g** Local persistence watcher | `pce_persistence_watcher/` | 🟢 P3 | None | ✅ Yes | Reads files in user-owned paths only; respects path-filter allowlist (ADR-018 §6.2) |
| **L3h** CLI wrap | `pce_cli_wrapper/` | 🟢 P3 | None | ✅ Yes | PATH-priority shim; user-initiated; transparent passthrough |
| **L4a** Clipboard capture | `pce_core/clipboard/` (planned) | 🟢 P3 | None | ✅ Yes | User-initiated; no background polling; OS-standard clipboard API |
| **L4b** Accessibility (UIA / AX) | `pce_agent_ax/` | 🟢 P3 | None — OS-sanctioned | **Pro currently**, OSS-eligible | Use only documented UIA/AX APIs; do not pose as assistive tech to bypass policy |
| **L4c** OCR | `pce_core/ocr/` (planned) | 🟢 P3 | None | ✅ Yes | OCR of pixels the user already sees; no decryption |

**Rules derived from the matrix** (enforced by `CONTRIBUTING.md` and CI):

1. **No L0 / L2 code in OSS repo, ever.** The capability is delivered exclusively through the Pro repo.
2. **L3b is dormant** until ADR-016 is reopened with an updated risk review; if revived, must respect Electron Fuses and ship with user-consent prompts.
3. **L1 must always preserve a "no-MITM" code path.** Users who do not install the CA must still get a working PCE Core (extension-only / clipboard / OCR). Capture failure must be fail-open.
4. **L3a manifest must always reflect runtime hosts.** Mismatch between `host_permissions` and runtime behavior is a Webstore-policy violation and a misrepresentation risk.
5. **All vendor-specific selectors / hosts live in `pce_core/sites/<vendor>.{yaml,py}`** — required for the 24h vendor kill-switch in `CEASE-AND-DESIST-RESPONSE.md`.

---

## 4. Threats by Adversary Profile

| Adversary | Most likely action | Earliest signal | Pre-positioned defense |
|---|---|---|---|
| **AI vendor legal team** | Cease-and-desist letter targeting one or more adapters | Email to `security@` GitHub-private channel; or comment from a verified-org account | `Docs/legal/CEASE-AND-DESIST-RESPONSE.md` playbook + 24h vendor kill-switch (per §4.2 of the playbook) |
| **Browser-platform Trust & Safety** | Webstore policy violation notice; potential delisting | Email from Chrome Web Store Developer Dashboard | `Docs/store/SUBMISSION-PLAYBOOK.md`; sideload distribution as fallback |
| **Rightsholder bot (DMCA §512)** | Automated takedown of a specific repo file | GitHub email "DMCA takedown notice" | GitHub counter-notice procedure if invalid; comply if valid; log in `TAKEDOWN-LOG.md` |
| **Competitor / hostile contributor** | PR introducing legally toxic code (L2 bypass, scraped vendor selectors with proprietary anti-bot signatures) | PR review | CI rejects forbidden imports; CONTRIBUTING.md Rule 5 (compliance boundary); maintainer review checklist |
| **Foreign regulator** | Information request; injunction | Email or notarized letter | Counsel-of-jurisdiction; respond per playbook |
| **Public researcher / journalist** | Article speculating on legal risk | Search alerts | Public talking points (this document is the talking-point source); no improvisation |
| **Disgruntled user** | Frivolous complaint | GitHub issue | Standard issue triage; do not respond legally |

---

## 5. Engineering Safeguards (cross-reference to CI / repo)

These are the **automated** controls the codebase enforces. Each one maps back to a threat above.

| Control | Mechanism | Threat covered |
|---|---|---|
| **OSS → Pro import block** | `.github/workflows/import-direction.yml` greps for `pce_agent_*`, `pce_core.capture_supervisor` | §2.2 DMCA §1201 (L0/L2 leakage) |
| **Vendor adapter isolation** | (P5.C.2) `scripts/check_vendor_isolation.py` enforces `pce_core/sites/<vendor>.{yaml,py}` only | §1.4 / §2.1 (24h kill-switch) |
| **Manifest ↔ runtime parity** | (P5.C.2) `scripts/check_extension_manifest.py` compares `wxt.config.ts` `COVERED_SITES` with content_scripts entries | §2.6 Webstore policy |
| **Privacy schema linter** | (P5.C.2) `scripts/check_capture_event.py` rejects fields with PII-suspect names not declared in PRIVACY.md | §2.7 GDPR / §1.2 Local-first |
| **Health beacon redaction** | `pce_core/health.py` (P5.C.1) refuses to record fields named `email`, `cookie`, `authorization`, `api_key` | §2.7 GDPR |
| **Forbidden-keyword grep on PR** | `.github/workflows/legal-keyword-scan.yml` (P5.C.0 next) scans diffs for `bypass`, `unlock`, `defeat`, `crack`, `pirate` in user-facing strings | §2.3 Tortious interference |
| **`SECURITY.md` private channel** | GitHub Security Advisories | C&D / DMCA / vulnerability privately routed |
| **`SUPPORTED_AI_TOOLS` registry** | Single source of truth for vendor support; updated in `README.md` + `wxt.config.ts` | §1.4 / §2.6 |

---

## 6. Decision Rules for New Capture Techniques

Before merging any PR that adds a new capture technique, the maintainer reviewer asks:

1. **Which row of the §3 matrix does this fall under?** If it's a new row, this is an architectural change requiring an ADR. Stop and request the ADR.
2. **Does it require defeating any technological measure (DRM, pinning, integrity check, anti-debug)?** If yes → **Pro only**, full stop. Do not merge in OSS.
3. **Does it hold or transmit user credentials for an AI service?** If yes → **reject**. PCE never proxies authentication.
4. **Does it operate without explicit user installation/consent?** If yes → **reject**. Every active capture surface must be installable AND uninstallable.
5. **Does the marketing copy associated with this PR use forbidden words (`bypass`, `unlock`, `defeat`, `crack`, `pirate`, `circumvent`)?** If yes → require rewording before merge.
6. **Is the vendor adapter properly isolated to `pce_core/sites/<vendor>.*`?** If no → require restructuring before merge.

If any answer is "I don't know," escalate to the project owner.

---

## 7. Living-Document Discipline

| Trigger | Action |
|---|---|
| New ADR affects a capture layer | Update §3 matrix in the same PR |
| Cease-and-desist received | After resolution, sanitized entry into `Docs/legal/TAKEDOWN-LOG.md`; if a new theory was used, add row to §2 |
| New jurisdiction surfaces | Add row to §2 (GDPR-style) and §4 (regulator-style) |
| New layer added | Add row to §3 BEFORE merging; require ADR |
| Annual review (2027-Q2) | Counsel review of §1.3, §2 defenses, §6 decision rules |

---

## 8. Anchor Sentence

**The matrix in §3 is the contract**: every line of capture code in the OSS repo must be answerable to a row whose "Allowed in OSS?" column is ✅. If a contributor cannot place their code in this matrix, the code does not belong in the OSS repo. This is what makes PCE legally legible to a future reviewing court — not its data flow, but its principled refusal to accept code that crosses the §1201 line.

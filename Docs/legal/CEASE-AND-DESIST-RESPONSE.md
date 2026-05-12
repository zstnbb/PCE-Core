# Cease-and-Desist / DMCA / Takedown Response Playbook

> **Status**: canonical — operational playbook for legal communications.
>
> **Adopted**: 2026-05-12 (P5.C.0 contract freeze, per ADR-019 §3.5).
>
> **Authority**:
> - `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-019-maintenance-as-first-class-concern.md` §3.5 Governance Artefact 5
> - `@f:\INVENTION\You.Inc\PCE Core\Docs\legal\THREAT-MODEL.md` (risk frame)
>
> **Audience**: project owner + maintainers + legal counsel (when retained).
>
> **Not legal advice**: This document is an internal operational playbook. It is NOT legal advice. When an actual notice is received, consult counsel before responding substantively.

---

## 0. Trigger Events

This playbook activates when any of the following arrives via email, GitHub, postal mail, or DMCA agent:

| Trigger | Likely sender | Authority cited | First response window |
|---|---|---|---|
| **Cease-and-Desist letter** | AI service provider, IDE vendor, browser-platform operator | ToS, CFAA, trade secret, tortious interference | 72h acknowledge, 7d substantive |
| **DMCA §512 takedown (GitHub)** | Rightsholder or automated bot | DMCA §512(c) / §1201 | GitHub auto-honours in 24h; counter-notice possible |
| **Chrome Web Store takedown** | Google Trust & Safety | Webstore policy | 7d to respond before permanent removal |
| **Court summons / subpoena** | Plaintiff's counsel via process server | Civil action, discovery | Immediate counsel engagement |
| **Informal complaint** | Vendor employee on GitHub / email | "We noticed your project..." | 7d acknowledge |
| **Press inquiry citing legal angle** | Journalist | — | Refer to canned statement; do not improvise |

**Important**: Do NOT ignore. Even informal complaints, if unanswered, can be cited later as evidence of "willful" conduct under CFAA or DMCA.

---

## 1. The 24-Hour Triage (project owner)

Within 24 hours of receiving any trigger event:

### 1.1 Preserve everything (before anything else)

- Save the original notice (email headers + body, PDF if postal, GitHub comment URL).
- Take screenshots if web-based (notice content + page state).
- Note exact UTC timestamp received.
- Place into a private repo or local encrypted folder: `~/pce-legal/incidents/YYYY-MM-DD-<sender-slug>/`.
- Do NOT delete or amend any PCE source code, issues, PRs, or releases until step 1.4 is decided.

### 1.2 Classify the notice

Answer in writing (`triage.md` in the incident folder):

1. **Sender**: who sent it? Are they (a) a corporate legal team, (b) a product manager, (c) automated system, (d) third-party rightsholder?
2. **Authority cited**: which statute / contract / policy is invoked?
3. **Scope**: which PCE component is targeted? (extension / proxy / specific adapter / specific commit)
4. **Demand**: what action is requested? (take down / disable feature / provide info / cease distribution)
5. **Deadline**: what is the stated deadline for response?
6. **Public vs private**: is the notice public (filed in court / posted to GitHub) or private?

### 1.3 Categorize severity

| Severity | Definition | Required next step |
|---|---|---|
| 🔴 **P0** | Court summons; DMCA §1201 (anti-circumvention); criminal complaint | Engage counsel within 24h |
| 🟠 **P1** | Formal cease-and-desist on corporate letterhead; large-scale DMCA §512 takedown | Engage counsel within 72h; do not respond substantively first |
| 🟡 **P2** | Informal complaint from vendor employee; single-component DMCA §512 | Acknowledge within 72h, plan response in 7d |
| 🟢 **P3** | Press inquiry; community concern; ToS reminder without legal threat | Acknowledge within 7d; standard PR response |

### 1.4 Decide preservation vs. removal of disputed content

**Default**: preserve evidence (do NOT delete) but **disable distribution** of disputed components within 24h.

For a vendor-specific adapter (e.g. C&D from "ExampleAI" about `pce_core/sites/exampleai.yaml`):
- Move file to `Docs/archive/legal-takedowns/exampleai-2026-05-12/` (preserves git history)
- Delete the original file in a follow-up commit signed `legal-takedown: exampleai-2026-05-12`
- Update `pce_core/sites/__init__.py` to no longer load that adapter
- Tag the git revision before removal: `git tag legal-snapshot-exampleai-2026-05-12`
- Push the tag to a private mirror, NOT to public origin (preserves evidence; doesn't broadcast capitulation)

**Never** delete:
- Issue or PR threads (close them, don't delete)
- Past releases (un-tag if needed, but don't force-push)
- Audit logs / capture history schemas

---

## 2. Acknowledgment Template (within 72h, P1/P2)

> **Subject**: Re: Your notice dated [DATE] regarding PCE
>
> [Sender Name],
>
> We received your notice dated [DATE] concerning [SPECIFIC PCE COMPONENT]. We take legal correspondence seriously and are reviewing your claims with counsel.
>
> While our review is in progress, we have [DESCRIBE INTERIM ACTION, e.g. "disabled the [exampleai] adapter in the public repository pending evaluation"]. This interim measure is taken in good faith and is not an admission of liability or of the validity of any claim asserted in your notice.
>
> We will respond substantively within [DEADLINE, default 7 days] following counsel review. If the matter is urgent, please contact [COUNSEL CONTACT, only if retained; otherwise omit this line].
>
> Sincerely,
> [Project Owner]
> PCE Project
> https://github.com/zstnbb/PCE-Core

**Critical rules for the acknowledgment**:
- Do NOT admit any factual allegation in the notice.
- Do NOT promise permanent removal — only describe interim action.
- Do NOT volunteer information about user counts, revenue, contributor identities.
- Do NOT respond on social media or in public GitHub issues. Acknowledge in the same private channel the notice arrived in.

---

## 3. Substantive Response (within 7-14d, with counsel)

### 3.1 If the claim is valid

- Permanently remove disputed component
- Add to public release notes: `Removed [component] in response to a takedown request from [sender]`
- Update `Docs/legal/TAKEDOWN-LOG.md` with sanitized record (per §6 below)
- No appeal

### 3.2 If the claim is mixed

- Comply with the valid portion
- Counter the over-broad portion via counsel reply
- Document position in incident folder

### 3.3 If the claim is invalid

- Counsel-drafted reply explaining why
- Common defences (counsel will tailor — examples only):
  - **Van Buren v. United States (2021)**: User accessing their own account is not "without authorization" under CFAA.
  - **Bright Data v. Meta (2024)**: Public data scraping is not a contract or trespass violation when no authentication is bypassed.
  - **17 U.S.C. §1201(f)** reverse-engineering exemption (only relevant if any anti-circumvention claim).
  - **DMCA §512(g) counter-notice** (for §512(c) takedowns): if content is non-infringing.
- If sender escalates, prepare for litigation; consider EFF or Software Freedom Law Center pro-bono request.

---

## 4. Vendor Kill-Switch (Engineering)

Pre-built capability to remove a vendor adapter within 24h.

### 4.1 Adapter file isolation (target architecture, P5.C.2)

Each vendor's capture logic should be isolated to:

```
pce_core/sites/<vendor>.yaml          # selectors + URL patterns (config-as-data)
pce_core/sites/<vendor>.py            # vendor-specific extractor (only if YAML insufficient)
tests/e2e_probe/sites/<vendor>.py     # T-case adapter
pce_browser_extension_wxt/sites/<vendor>.ts  # extension hook (if any)
```

**No vendor-specific logic outside these files.** This is enforced by `scripts/check_vendor_isolation.py` (to build in P5.C.2).

### 4.2 Removal procedure

```bash
# 1. Move all vendor files to archive (preserves git history)
git mv pce_core/sites/exampleai.yaml Docs/archive/legal-takedowns/exampleai-YYYY-MM-DD/
git mv tests/e2e_probe/sites/exampleai.py Docs/archive/legal-takedowns/exampleai-YYYY-MM-DD/
git mv pce_browser_extension_wxt/sites/exampleai.ts Docs/archive/legal-takedowns/exampleai-YYYY-MM-DD/

# 2. Update registries
# pce_core/sites/__init__.py — remove "exampleai" entry
# pce_browser_extension_wxt/wxt.config.ts — remove vendor host from COVERED_SITES

# 3. Snapshot pre-removal state to private mirror
git tag legal-snapshot-exampleai-YYYY-MM-DD
git push private-mirror legal-snapshot-exampleai-YYYY-MM-DD

# 4. Commit removal
git commit -s -m "legal: remove exampleai adapter per takedown request YYYY-MM-DD"

# 5. Push
git push origin master

# 6. Trigger nightly probe to confirm coverage matrix updated
gh workflow run nightly-probe.yml
```

Total elapsed time, with practice: **< 2 hours**.

### 4.3 What stays even after kill-switch

The CaptureEvent schema, normalizer pipeline, and dashboard remain unchanged. Users who already captured data from that vendor keep their local data — PCE has no remote control over user databases. This is by design (local-first) and an important defensive posture: PCE cannot be ordered to delete user data because PCE never had it.

---

## 5. DMCA §512(c) Counter-Notice (if used)

**Only file a counter-notice if** counsel confirms the takedown is invalid AND user is willing to consent to federal jurisdiction in their district.

Template (consult counsel before sending):

> 1. Identification of material removed: `[GitHub URL of removed file]` at git revision `[SHA]`.
> 2. Statement under penalty of perjury that the material was removed by mistake or misidentification.
> 3. User's name, address, telephone number.
> 4. Consent to jurisdiction of Federal District Court for the district where the user resides (or for foreign users, any judicial district where GitHub is located).
> 5. Statement that user will accept service of process from the original notice sender.
> 6. Physical or electronic signature.

GitHub DMCA process: <https://docs.github.com/en/site-policy/content-removal-policies/dmca-takedown-policy>

---

## 6. Takedown Log (public, after-the-fact)

After every legal response is closed, add a sanitized entry to a public log:

```
Docs/legal/TAKEDOWN-LOG.md
---
| Date | Sender (sanitized) | Component affected | Outcome | Notes |
|---|---|---|---|---|
| 2026-05-12 | (example, no real entry) | (none) | (none) | Initial log creation |
```

Sanitization rules:
- Sender name only if the sender's identity is already public (e.g. court filing).
- No personal email addresses, no individual-employee names.
- No quotes from notices unless already published elsewhere.
- Do mention statute cited and which adapter affected.

This public log serves three purposes:
1. Transparency to community (lina-Tor / Wikileaks model)
2. Demonstrates good-faith compliance pattern (mitigates "willful" allegations in future)
3. Reference for future contributors deciding whether to add support for a vendor

---

## 7. Escalation Tree

```
P3 informal complaint
  └─→ Project owner responds personally, no counsel needed.

P2 vendor-employee C&D, single-component DMCA
  └─→ 72h acknowledge → optional counsel consult ($500–2k, 1 session) → respond
       └─→ if escalates → P1

P1 corporate-letterhead C&D, multi-component DMCA, regulatory inquiry
  └─→ 72h acknowledge → retain counsel ($5–10k retainer) → counsel responds
       └─→ if litigation threatened → P0

P0 court summons, criminal investigation, DMCA §1201
  └─→ 24h counsel engagement
  └─→ Reach out to:
        - EFF intake: https://www.eff.org/issues/coders/online-services
        - Software Freedom Law Center: https://www.softwarefreedom.org/
        - Software Freedom Conservancy: https://sfconservancy.org/
  └─→ Pause public releases; preserve evidence; brief contributors via private channel only.
```

---

## 8. What NOT to Do

The following actions can convert a survivable incident into a project-ending one:

| Don't | Why |
|---|---|
| **Ignore the notice** | Establishes "willful" conduct; courts use it against you. |
| **Respond substantively without counsel (P0/P1)** | Statements can become admissions. |
| **Delete code / issues / PRs without preservation** | Spoliation of evidence; can result in adverse-inference jury instructions. |
| **Discuss the notice publicly** (Twitter, HN, Reddit) | Can be cited as evidence; can amplify the dispute. |
| **Counter-notice without consent to jurisdiction** | Subjects you personally to a foreign court. |
| **Personally insult or threaten the sender** | Can become tortious interference or defamation counterclaim. |
| **Promise permanent removal in acknowledgment** | Eliminates negotiating room. |
| **Ship an "even more aggressive" version in retaliation** | Establishes malicious intent. |

---

## 9. Pre-Drafted Counsel Engagement Template

When retaining counsel for the first time:

> Subject: Engagement inquiry — open-source software project, [P0/P1] notice received
>
> Hello,
>
> I am the maintainer of an open-source project called PCE (https://github.com/zstnbb/PCE-Core), a local-first AI-conversation capture tool. I received the attached notice on [DATE] from [SENDER].
>
> Brief technical context:
> - PCE runs entirely on the user's machine. It does not transmit user data to any server we operate.
> - The disputed component is [DESCRIBE].
> - The notice cites [STATUTE/CONTRACT].
>
> I am seeking advice on:
> 1. Validity of the asserted claim
> 2. Appropriate response strategy
> 3. Whether a public statement is advisable
>
> I have preserved all communications and the relevant code at git revision `[SHA]`.
>
> Are you available for an initial consultation? My budget for this stage is approximately $[AMOUNT].
>
> Thank you,
> [Name]

Recommended counsel directories:
- **EFF Coders' Rights**: https://www.eff.org/issues/coders/online-services
- **Lex Lumina** (modern OSS / scraping practice)
- **Software Freedom Law Center**: https://www.softwarefreedom.org/
- **Cohen & Gresser** (DMCA experience)
- For Chinese-jurisdiction maintainers: 大成 (Dentons China), 君合 (JunHe) — both have OSS-friendly groups

---

## 10. Anchor Sentence

**The goal is not to win every claim. The goal is to establish, in advance, a documented good-faith compliance pattern that converts any single legal action into a survivable event rather than a project-ending one.**

This playbook exists to remove decision-making latency in a stressful moment. When a notice arrives, the maintainer follows the playbook step-by-step rather than improvising — which is exactly when bad decisions are made.

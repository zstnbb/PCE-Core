# Contributing to PCE

Thank you for considering a contribution to PCE. This document explains how to propose changes, the conventions we follow, and the architectural guardrails you need to respect.

## Before You Start

PCE is a **local-first AI capture infrastructure** organized as an **Open Core** project:

- This repository (`pce`) is licensed under **Apache-2.0**.
- Advanced modules (kernel redirector, Frida SSL hook, Electron preload injection, Accessibility bridge, Capture Supervisor, advanced dashboard) live in a **separate proprietary repository** and are not accepted as contributions here.

Please read the following before opening a pull request:

- [`Docs/docs/PROJECT.md`](Docs/docs/PROJECT.md) — project scope and principles
- [`Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`](Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md) — UCS architecture (the plan that drives all current work)
- [`Docs/docs/engineering/adr/`](Docs/docs/engineering/adr/) — architecture decision records (especially ADR-001, ADR-002, ADR-009, ADR-010)

## How to Report an Issue

Before filing, please:

1. Search existing issues (including closed ones) — your problem may already be known or fixed.
2. Read the installation section in [`README.md`](README.md) and the troubleshooting notes.

When filing a bug, include:

- Your OS and version (Windows 11 / macOS 14 / Ubuntu 22.04 etc.)
- Your Python version (`python --version`)
- The AI tool(s) you were using (ChatGPT Web / Cursor / Copilot / ...)
- A minimal reproduction (the fewer steps the better)
- Relevant logs — JSON logs from `~/.pce/logs/` if they exist

For feature requests, please describe:

- The AI product / workflow you want supported
- Which of the 10 UCS forms (F1–F10, see design doc Appendix A) it falls under
- Why the current capture layers (L1 / L3a etc.) cannot cover it

## How to Propose a Code Change

1. **Open an issue first** for anything beyond typo fixes or trivial bugs. This avoids wasted work if the direction doesn't match the roadmap.
2. **Fork the repository** and create a feature branch: `git checkout -b feat/my-thing`
3. **Make your change** (see conventions below)
4. **Test locally** (see "Running Tests")
5. **Open a pull request** with a clear description linking the issue

## Dev Environment

### Prerequisites

- **Python** 3.10+
- **Node.js** 18+ and **pnpm** (for the browser extension)
- **mitmproxy** 10+

### Setup

```bash
git clone https://github.com/zstnbb/PCE-Core.git
cd PCE-Core
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

# Browser extension (optional)
cd pce_browser_extension_wxt
pnpm install
pnpm build
```

### Running Tests

```bash
# Python test suite
python -m pytest tests -v

# Smoke tests only (quick)
python -m pytest tests/smoke -v

# Browser extension tests
cd pce_browser_extension_wxt
pnpm test
```

All tests must pass before we merge a PR.

## Code Conventions

### Python

- **Formatter**: we follow PEP 8. Use `ruff check` and `ruff format` if you have them installed.
- **Type hints**: prefer type-hinted function signatures. Pydantic models for external contracts.
- **Imports at the top of the file**. Never import inside a function unless absolutely necessary (e.g., optional dependencies).
- **Naming**: `snake_case` for modules / functions / variables, `PascalCase` for classes.
- **SPDX header**: new `.py` files should start with `# SPDX-License-Identifier: Apache-2.0`.

### TypeScript / JavaScript

- **Formatter**: Prettier with 2-space indent.
- **Linter**: `pnpm lint` — must pass.
- **SPDX header**: new `.ts` / `.js` files should start with `// SPDX-License-Identifier: Apache-2.0`.
- **Style**: prefer `const` over `let`; avoid `any`; use the shared helpers in `pce_browser_extension_wxt/utils/`.

### Commit Messages

- Use **Conventional Commits**: `feat: ...` / `fix: ...` / `docs: ...` / `refactor: ...` / `test: ...` / `chore: ...`
- Keep the first line ≤ 72 characters. Body wraps at 80.
- Reference issues by `#NN` where applicable.

### DCO — Developer Certificate of Origin

By contributing, you agree to the [Developer Certificate of Origin](https://developercertificate.org/). Every commit must be signed off:

```bash
git commit -s -m "fix: correct proxy toggle on Windows"
```

The `-s` flag appends `Signed-off-by: Your Name <you@example.com>` to the commit message. This confirms you have the right to submit the code under Apache-2.0.

## Architecture Guardrails (ENFORCED BY CI)

These are hard rules. Violating them will fail CI and block merge.

### Rule 1 — OSS must never import Pro

This repository is the **OSS edition**. It must build and run **standalone**. The following imports are forbidden anywhere under `pce/`, `pce_core/`, `pce_proxy/`, etc.:

```python
# ❌ FORBIDDEN
from pce_agent_kernel import ...
from pce_agent_frida import ...
from pce_agent_electron import ...
from pce_agent_ax import ...
from pce_core.capture_supervisor import ...
```

Pro modules communicate with OSS via **local HTTP** `POST /api/v1/captures/v2`, not in-process calls.

### Rule 2 — CaptureEvent v2 schema is a public API

Once merged to `main`, the schema at `pce_core/capture_event.py` is a public contract. You may:

- ✅ ADD new optional fields
- ✅ ADD new values to open enums

You may NOT (in `pce` OSS repo):

- ❌ REMOVE or RENAME existing fields
- ❌ Change the semantics of existing fields
- ❌ Make optional fields required

Breaking schema changes require a v3 migration path and ADR review.

### Rule 3 — Respect the 10 UCS forms taxonomy

If you want to add support for a new AI product, first classify it under one of the 10 UCS forms (F1–F10) in the design doc Appendix A. Do not add a bespoke capture layer for a single product.

### Rule 4 — Local-first, fail-open

- **No data leaves the user's machine by default**. Optional OTLP export is opt-in (ADR-007).
- **Capture failure must not block the user's upstream request**. If mitmproxy addon crashes, the original HTTP request still reaches the AI service.

### Rule 5 — Compliance boundary (legal-risk surface)

PCE accepts code that records what a user can already see on their own machine. PCE does **NOT** accept code that defeats the technological measures of an AI service or its delivery channel. The full risk model is at [`Docs/legal/THREAT-MODEL.md`](Docs/legal/THREAT-MODEL.md). The hard rules contributors must internalize before opening a PR:

**5.1 — Forbidden in this OSS repository (no exceptions):**

- **L0 kernel-level network redirection** (`pce_agent_kernel/`) — Pro repo only.
- **L2 Frida SSL-pinning bypass or any anti-debug / integrity-defeat code** — Pro repo only.
- **Code that extracts, decrypts, or exposes pinned certificates, hardcoded API keys, or proprietary anti-bot signatures** of any AI vendor.
- **Code whose stated purpose is to publish, sell, or aggregate captured outputs across users** — PCE is a personal-capture tool, not a data product.
- **Code that forwards user-provided AI credentials (API keys, session cookies, OAuth tokens) anywhere other than the upstream AI service.** PCE never proxies authentication.

**5.2 — Required for any new vendor / capture surface:**

- **Vendor adapter must be isolated** to `pce_core/sites/<vendor>.{yaml,py}` (and corresponding `pce_browser_extension_wxt/sites/<vendor>.ts` if applicable). No vendor-specific logic outside these files. This is enforced so a single vendor can be removed within 24 hours per [`Docs/legal/CEASE-AND-DESIST-RESPONSE.md`](Docs/legal/CEASE-AND-DESIST-RESPONSE.md).
- **Manifest-runtime parity** for browser extensions: hosts in `wxt.config.ts` `COVERED_SITES` must match what content scripts actually run on. Webstore-policy violations fail review and put the entire extension at risk.
- **No `<all_urls>`** in the Webstore-distributed extension build. Sideload-only is the only permitted scope-broadening path.

**5.3 — Forbidden marketing / language:**

User-facing strings (UI labels, help text, README sections, store-listing copy, error messages) must NOT use the words `bypass`, `unlock`, `defeat`, `crack`, `circumvent`, `pirate`, or equivalents in connection with any AI service. Frame PCE consistently as a personal observability tool — "capture", "record", "observe", "archive". Marketing language that invites a tortious-interference theory is the cheapest legal mistake to avoid; we choose not to make it.

**5.4 — The §3 Threat Model matrix is the contract.**

Every PR that adds or modifies a capture layer must be answerable to a row in [`Docs/legal/THREAT-MODEL.md`](Docs/legal/THREAT-MODEL.md) §3. If you cannot place your code in that matrix, your PR is a new architectural surface and requires an ADR before review. When in doubt, open a discussion first.

## Fix a Broken Adapter (step-by-step)

When a nightly probe fails or a GitHub issue is filed with the `broken-adapter` label, follow this workflow to fix it. Total time budget: ≤30 minutes for a typical selector drift.

### 1. Reproduce the failure locally

```bash
# Run the specific failing case (example: ChatGPT T01)
python -m pytest tests/e2e_probe/test_matrix.py::test_chatgpt[T01] -v

# Or use the Test Conductor CLI
python -m pce_test_conductor run_case --target browser_chatgpt --case T01
```

If the test passes locally, the failure may be environment-specific (login wall, geo-block). Add a `SKIP` annotation and document why.

### 2. Identify the failure kind

```bash
# Classify the most recent failure
python -m pce_test_conductor classify_failure --target browser_chatgpt --case T01
```

Common failure kinds and their fix paths:

| FailureKind | Typical cause | Fix location |
|---|---|---|
| `UI_SELECTOR_MISS` | Vendor changed DOM structure | `pce_core/adapters/<site>.yaml` selectors section |
| `SCHEMA_DRIFT` | Response JSON shape changed | `pce_core/adapters/<site>.yaml` or normalizer |
| `LOGIN_WALL` | Session expired / new auth flow | Not a code fix — re-authenticate and re-run |
| `RACE_TIMEOUT` | Page load slower than timeout | `pce_core/adapters/<site>.yaml` timeouts section |
| `URL_PATTERN_DRIFT` | Vendor changed URL paths | `pce_core/adapters/<site>.yaml` url_patterns section |

### 3. Get an AI-assisted repair suggestion (optional)

```bash
# Dry-run (no API key needed, uses mock provider)
python -m tools.repair_adapter --target browser_chatgpt

# Real suggestion (requires ANTHROPIC_API_KEY or OPENAI_API_KEY in env)
python -m tools.repair_adapter --target browser_chatgpt --no-dry-run --provider anthropic
```

The tool outputs a YAML diff. Review it — never apply blindly.

### 4. Edit the YAML adapter

Open `pce_core/adapters/<site>.yaml` and apply the fix. Adapter files are pure data — selectors, timeouts, labels, URL patterns. No Python knowledge required for most fixes.

```bash
# Example: fix a selector for ChatGPT
# Edit pce_core/adapters/chatgpt.yaml → selectors → <broken_key>
```

### 5. Verify the fix

```bash
# Re-run the failing case
python -m pytest tests/e2e_probe/test_matrix.py::test_chatgpt[T01] -v

# Run the full site regression to check for side effects
python -m pytest tests/e2e_probe/test_matrix.py -k chatgpt -v

# Update the canary snapshot if the schema legitimately changed
python -m pce_test_conductor update_canary --target browser_chatgpt --case T01
```

### 6. Submit your fix

```bash
git checkout -b fix/chatgpt-selector-drift
git add pce_core/adapters/chatgpt.yaml
git commit -s -m "fix(adapter): update ChatGPT message container selector"
# Push and open a PR — the PR template will guide you through the checklist
```

The PR template requires: tests pass, canary updated (if schema changed), CODEOWNERS reviewer assigned.

## Pull Request Process

1. PR title follows Conventional Commits.
2. Description includes:
   - What problem this PR solves
   - What approach you chose (and alternatives considered for non-trivial changes)
   - How to test it
3. CI must be green: tests + import-direction + lint.
4. Request review from maintainers listed in `Docs/docs/PROJECT.md`.
5. Maintainers may request changes; we aim to respond within 1 week.
6. Once approved + CI green, a maintainer squash-merges your PR.

## What We Will NOT Merge

- Features that require data to leave the user's machine (violates local-first, ADR-002)
- Features that actively modify AI model responses (violates record-not-intervention, ADR-001)
- Ports of Pro-only features into this repo (violates Open Core boundary, ADR-010)
- Code that crosses the legal compliance boundary defined in Rule 5 — SSL-pinning bypass, kernel-level redirection, credential proxying, anti-bot signature exposure, or marketing language that invites a tortious-interference theory (see [`Docs/legal/THREAT-MODEL.md`](Docs/legal/THREAT-MODEL.md))
- Vendor-specific logic placed outside `pce_core/sites/<vendor>.{yaml,py}` — breaks the 24h vendor kill-switch (see [`Docs/legal/CEASE-AND-DESIST-RESPONSE.md`](Docs/legal/CEASE-AND-DESIST-RESPONSE.md))
- Dependencies with incompatible licenses (GPL-family, proprietary, anti-use)
- Code without tests (for non-trivial changes)
- Code that touches sensitive paths (cert wizard, proxy toggle, dashboard auth) without careful security review

## Reporting Security Issues

Please do **NOT** open a public issue for security vulnerabilities. See [`SECURITY.md`](SECURITY.md) for responsible disclosure.

## Questions

- GitHub Discussions — for design / philosophy / "how do I" questions
- GitHub Issues — for bugs and concrete feature requests

Thank you for contributing to PCE.

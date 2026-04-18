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
git clone https://github.com/zstnbb/pce.git
cd pce
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
- Dependencies with incompatible licenses (GPL-family, proprietary, anti-use)
- Code without tests (for non-trivial changes)
- Code that touches sensitive paths (cert wizard, proxy toggle, dashboard auth) without careful security review

## Reporting Security Issues

Please do **NOT** open a public issue for security vulnerabilities. See [`SECURITY.md`](SECURITY.md) for responsible disclosure.

## Questions

- GitHub Discussions — for design / philosophy / "how do I" questions
- GitHub Issues — for bugs and concrete feature requests

Thank you for contributing to PCE.

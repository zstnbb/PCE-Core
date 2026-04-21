# PCE Pro Repository Setup Guide

This document is the **step-by-step manual** for initializing the private proprietary `pce-pro` repository (the Pro edition of PCE) once it has been created on GitHub. It assumes you have completed **R3** from the UCS kickoff plan (`Docs/handoff/HANDOFF-P5A-KICKOFF.md`).

- Companion architecture doc: `ADR-010-open-core-module-boundary.md`
- Companion upstream decision: `docs/decisions/2026-04-18-ucs-and-release-strategy.md`

## Prerequisites

1. **R3 done** — `github.com/zstnbb/pce-pro` exists and is **Private**.
2. **You have push access** to both `pce` (OSS) and `pce-pro` (private).
3. GitHub **Team / Pro** plan enabled (private repos require a paid plan for unlimited Actions minutes).
4. This OSS `pce` repo already at commit that includes `ADR-010` and `CONTRIBUTING.md`.

## Phase 1 — Initial scaffolding (about 30 min)

Create this directory structure in the empty `pce-pro` repo:

```
pce-pro/
├── LICENSE.txt                   # proprietary
├── README.md                     # private — audience is Pro maintainers, not public
├── CONTRIBUTING.md               # internal contributor guide (DCO not required here)
├── .gitignore                    # mirror OSS's
├── pyproject.toml                # Python package config, depends on pce
├── requirements.txt              # runtime deps (pce + extras)
├── .github/
│   └── workflows/
│       ├── ci.yml                # lint + test
│       ├── check-no-cycle.yml    # Pro → OSS only, not the other way
│       └── release.yml           # signing + artifact upload
├── pce_agent_kernel/             # L0 Kernel redirector (Win WFP, macOS NE, Linux eBPF)
│   └── __init__.py
├── pce_agent_frida/              # L2 Frida SSL hook + signed scripts
│   ├── __init__.py
│   ├── scripts/                  # signed JS payloads
│   └── public.pem                # script-signing public key
├── pce_agent_electron/           # L3b Electron preload injection
│   └── __init__.py
├── pce_agent_ax/                 # L4b Accessibility / UIA bridge
│   └── __init__.py
├── pce_core/
│   └── capture_supervisor/       # Supervisor (namespace-merges with OSS pce_core)
│       └── __init__.py
├── pce_ide_vscode_pro/           # VS Code extension advanced features
├── pce_ide_jetbrains/            # JetBrains plugin
├── pce_dashboard_pro/            # dashboard advanced UI
└── tests_pro/                    # all Pro-only tests
```

Important: **`pce_core/capture_supervisor/` uses the same top-level namespace as OSS `pce_core`**. This works via PEP 420 implicit namespace packages: neither repo has `pce_core/__init__.py`, so Python merges subpackages from both at import time when both are installed. Verify `pip show pce pce-pro` both resolve after installation; if collision occurs, rename to `pce_pro.capture_supervisor` (update ADR-010 accordingly).

## Phase 2 — `LICENSE.txt` (proprietary)

Short proprietary license template (replace `<YEAR>` and `<OWNER>`):

```text
PCE Pro License (Proprietary)
Copyright (c) <YEAR> <OWNER>. All rights reserved.

This software and its documentation (the "Software") are proprietary and
confidential. Unauthorized copying, distribution, modification, public display,
or public performance is strictly prohibited.

Licensees may install and use the Software on devices they own or control in
accordance with the subscription agreement. The Software is provided "AS IS"
without warranty of any kind.

The Software may include or depend upon components released under the Apache
License 2.0 and other open-source licenses; those components are distributed
under their original licenses and are not affected by this proprietary license.

For commercial licensing inquiries: contact@pce.example.com
```

Replace placeholder contact before making any release.

## Phase 3 — `pyproject.toml`

```toml
[project]
name = "pce-pro"
version = "0.1.0-dev"
description = "PCE Pro — advanced capture layers (private)"
requires-python = ">=3.10"
dependencies = [
    "pce @ git+https://github.com/zstnbb/PCE-Core.git@v1.0.0",
    "frida>=16",
    "psutil>=5.9",
]

[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
include = ["pce_agent_*", "pce_ide_*", "pce_dashboard_pro*", "pce_core.capture_supervisor*"]
```

The key line is `"pce @ git+https://github.com/.../pce.git@<tag>"` — `pce-pro` **always** consumes a tagged release of `pce`, never `main`. This enforces the release coordination rule.

## Phase 4 — Required GitHub Actions secrets

In `pce-pro` → Settings → Secrets and variables → Actions, add these (initially with placeholder values, replace when real):

| Secret name | Purpose | Format |
|---|---|---|
| `APPLE_DEVELOPER_ID` | macOS code-signing identity | string (e.g., `Developer ID Application: Name (TEAMID)`) |
| `APPLE_APP_PASSWORD` | App-specific password for notarization | string |
| `APPLE_TEAM_ID` | Apple team ID | string |
| `WINDOWS_EV_CERT` | EV code-signing cert | base64-encoded `.pfx` |
| `WINDOWS_EV_PASSWORD` | EV cert password | string |
| `FRIDA_SIGNING_KEY` | Ed25519 private key for Frida script signing | PEM, full key text |
| `FRIDA_SIGNING_KEY_PASSPHRASE` | Optional key passphrase | string |
| `VSCE_PAT` | VS Code Marketplace publishing token | Azure DevOps PAT |
| `JETBRAINS_MARKETPLACE_TOKEN` | JetBrains Marketplace upload | string |

**Security practices**:
- Frida signing key is generated once, backed up offline (hardware token preferred)
- EV cert is issued by Sectigo / Digicert / Globalsign — allow 1–4 weeks lead time
- Rotate `APPLE_APP_PASSWORD` annually
- Never print any secret value in logs (CI must use `::add-mask::` where needed)

## Phase 5 — CI workflow: cycle check

The most important CI protection in pce-pro is detecting **reverse cycles** — Pro modules must never be imported back from OSS. Since OSS runs its own `scripts/check_import_direction.py`, pce-pro's complementary check is to ensure Pro modules import only `pce.*` public namespace (or `pce_core.*` for namespace merging).

Minimal `.github/workflows/check-no-cycle.yml`:

```yaml
name: check-no-cycle
on: [push, pull_request, workflow_dispatch]

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - name: Install pce from tagged release
        run: pip install "pce @ git+https://github.com/zstnbb/PCE-Core.git@v1.0.0"
      - name: Install pce-pro
        run: pip install -e .
      - name: Import smoke test
        run: |
          python -c "import pce_core.capture_event; print('pce reachable from pce-pro:', pce_core.capture_event.__file__)"
          python -c "import pce_agent_frida; import pce_core.capture_supervisor"
      - name: Assert OSS pce has no import of Pro
        run: |
          pip install pce --no-deps  # install just OSS
          python -c "import ast,sys,pathlib; [sys.exit(1) for p in pathlib.Path(__import__('pce_core').__file__).parent.rglob('*.py') if 'pce_agent' in p.read_text() or 'capture_supervisor' in p.read_text()]; print('clean')"
```

## Phase 6 — Release coordination with OSS

The canonical release cadence is:

1. `pce` (OSS) tags `vX.Y.Z` — triggers OSS release CI
2. `pce-pro` tags `vX.Y.Z+proN` where `N` is a Pro build counter
3. Pro release pins OSS tag in `pyproject.toml`, regenerates lockfile, signs artifacts, attaches to Pro release

**CaptureEvent v2 schema changes** require twin PRs (OSS + Pro) that reference each other. CI on both sides must pass before either merges. Label with `cross-repo` to make this explicit.

## Phase 7 — First commit checklist

Before you push the first commit, double-check:

- [ ] `LICENSE.txt` with placeholders replaced
- [ ] `README.md` states the repo is **private** and **proprietary**, not open source
- [ ] `.gitignore` includes `*.pem`, `*.pfx`, `*.p12` (never commit signing materials)
- [ ] `pyproject.toml` pins the exact OSS `pce` tag
- [ ] All empty package directories have an `__init__.py` (or are intentionally PEP 420 namespace packages)
- [ ] At least one smoke test in `tests_pro/` exists and passes locally
- [ ] `.github/workflows/ci.yml` runs pytest on ubuntu + windows + macos
- [ ] Branch protection rule on `main` requires passing checks

## Phase 8 — Coordination with OSS `pce` (this repo)

After pce-pro exists, add to this OSS repo:

1. Update `CONTRIBUTING.md` (Rule 1 — OSS must never import Pro) — already done.
2. Extend `scripts/check_import_direction.py` if new Pro top-level names emerge — the current list already covers ADR-010 modules.
3. In `README.md`, link to `pce-pro` only if it has a public landing page (most Pro repos keep a public product page at `pce.example.com` or similar; the repo URL itself is often not publicized).

## Troubleshooting

### "pce_core namespace collision" when installing both

Symptom: `ImportError: cannot import name 'xxx' from 'pce_core'`.
Cause: one of the two packages has a `pce_core/__init__.py` that shadows the other.
Fix: remove `pce_core/__init__.py` from both repos; they must be namespace packages (PEP 420).

### Frida script signing fails in CI

Symptom: `signature verification failed` at runtime.
Cause: The public key shipped in `pce_agent_frida/public.pem` doesn't match the private key stored in `FRIDA_SIGNING_KEY`.
Fix: regenerate both, update public.pem in-repo, update secret.

### CI cannot install `pce` from OSS tag

Symptom: `git+https://github.com/.../pce.git@vX.Y.Z` 404.
Cause: OSS tag doesn't exist yet, or OSS repo is still private.
Fix: ensure OSS has tagged the release first; R7 (repo rename + public) must be done before pce-pro can reach it from a public CI.

## See also

- `Docs/docs/engineering/adr/ADR-010-open-core-module-boundary.md`
- `Docs/docs/decisions/2026-04-18-ucs-and-release-strategy.md`
- `Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` §7
- `CONTRIBUTING.md` (OSS)

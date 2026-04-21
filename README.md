# PCE — Personal Capture Environment

**Local-first capture of every conversation you have with AI tools.**

> Capture ChatGPT, Claude, Cursor, Copilot, Gemini, and 15+ other AI tools. Everything stored locally. Searchable, replayable, exportable. Zero data leaves your machine.

[Install](#install) · [Supported tools](#supported-ai-tools) · [OSS vs Pro](#oss-vs-pro) · [Architecture](Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md) · [Docs](Docs/README.md) · [Contributing](CONTRIBUTING.md)

---

## Why PCE

You use 5+ AI tools every day. Each keeps your history in its own silo. Some let you export; most don't. None of them compare across tools, replay a session, or run on your laptop when the internet is out.

PCE sits between you and the AI tools you already use. It captures every conversation into one local SQLite database — with full text, metadata, attachments, and tool-calls — and gives you a dashboard to search, filter, and replay them.

Unlike LLM observability products (Langfuse, Helicone, Phoenix), PCE runs entirely on your laptop and requires no code changes in the AI tools. It captures by combining a trusted local proxy, a browser extension, IDE extensions, and optional deeper hooks.

## Install

### Prerequisites

- Python 3.10+
- (Optional, for browser extension) Node.js 18+ and pnpm

### Clone + install core

```bash
git clone https://github.com/zstnbb/PCE-Core.git
cd PCE-Core
pip install -r requirements.txt
```

### Launch

```bash
python -m pce_core.server
```

Then open `http://127.0.0.1:9800/dashboard`. The onboarding wizard walks you through:

1. Installing the PCE root CA (needed to capture HTTPS traffic)
2. Enabling the system proxy (one click)
3. Optionally loading the browser extension

### Browser extension (recommended)

```bash
cd pce_browser_extension_wxt
pnpm install && pnpm build
# Load .output/chrome-mv3/ in chrome://extensions → Developer mode → Load unpacked
```

### Data

All data lives at `~/.pce/data/pce.db` (override via `PCE_DATA_DIR`). Sensitive headers (Authorization / Cookie / API keys) are replaced with `REDACTED` before storage. Nothing is uploaded anywhere.

## Supported AI Tools

### Structured capture (body + metadata)

- **Web chats**: ChatGPT, Claude.ai, Gemini, Perplexity, DeepSeek, Kimi, Grok, Qwen, Zhipu GLM, Meta AI, Character.AI
- **Desktop chats**: ChatGPT Desktop (non-pinned versions), Poe Desktop
- **IDE AI**: GitHub Copilot (VS Code), Windsurf, Cline
- **CLI AI**: Codex CLI, Claude Code, Aider
- **Local models**: Ollama, LM Studio, llama.cpp, vLLM server
- **SDK-instrumented apps**: any LiteLLM / OpenTelemetry-enabled Python app

### UI-level capture (text + DOM)

- Notion AI, Microsoft 365 Copilot (web), Figma AI, Gmail AI, Jira AI, and any AI-powered SaaS (via browser extension)

### Requires PCE Pro

- **Cursor** (gRPC-web body via Electron preload)
- **Claude Desktop** / **ChatGPT Desktop** with certificate pinning (defeated via Frida SSL hook)
- **Kernel-level force capture** for uncooperative apps
- **JetBrains IDEs** (IntelliJ, PyCharm, WebStorm, etc.)

## OSS vs Pro

PCE is an **Open Core** project. The table below reflects the v1.0 release scope; see [`ADR-010`](Docs/docs/engineering/adr/ADR-010-open-core-module-boundary.md) for the full module boundary.

| Capability | OSS (Apache-2.0) | Pro (Subscription) |
|---|:-:|:-:|
| L1 TLS MITM proxy | ✅ | ✅ |
| L3a Browser extension (15+ sites) | ✅ | ✅ |
| L3d CDP channel (embedded Chromium) | ✅ | ✅ |
| L3e LiteLLM SDK capture | ✅ | ✅ |
| L3f OpenTelemetry export | ✅ | ✅ |
| L4a Clipboard capture | ✅ | ✅ |
| L4c OCR capture | ✅ | ✅ |
| VS Code extension (basic) | ✅ | ✅ |
| Local SQLite + FTS storage | ✅ | ✅ |
| DuckDB analytics + Parquet export | ✅ | ✅ |
| Semantic search (sqlite-vec) | ✅ | ✅ |
| Basic dashboard | ✅ | ✅ |
| L0 Kernel redirector (force capture) | — | ✅ |
| L2 Frida SSL hook (defeats pinning) | — | ✅ |
| L3b Electron preload injection | — | ✅ |
| L4b Accessibility bridge (macOS AX / Windows UIA) | — | ✅ |
| Capture Supervisor (auto scheduling / health / dedup) | — | ✅ |
| VS Code advanced features | — | ✅ |
| JetBrains plugin | — | ✅ |
| Advanced dashboard (search / replay / share / export) | — | ✅ |

Pro is developed in a separate private repository and distributed as signed binaries. The OSS edition is fully functional standalone — Pro never replaces OSS, only extends it.

## Architecture

PCE follows the **Universal Capture Stack (UCS)** — 10 canonical AI product forms × 5 capture layers × a central supervisor × one unified data contract (`CaptureEvent v2`).

See the design doc for the full picture:

- [`UNIVERSAL-CAPTURE-STACK-DESIGN.md`](Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md) — 13 chapters + 3 appendices
- [`ADR-009`](Docs/docs/engineering/adr/ADR-009-universal-capture-stack.md) — UCS adoption
- [`ADR-010`](Docs/docs/engineering/adr/ADR-010-open-core-module-boundary.md) — Open Core module boundary

Philosophy (see [`PROJECT.md`](Docs/docs/PROJECT.md)):

- **Local-first** — all data stays on your machine
- **Habit-preserving** — no change to how you use AI tools
- **Record-not-intervention** — PCE never modifies your requests or responses
- **Fail-open** — capture failure must not block your AI tools
- **User sovereignty** — pause, export, delete anytime

## Roadmap

| Phase | Version | Slice | Target |
|---|---|---|---|
| **P5.A** _(active)_ | **v1.0 Subscription Capture** | L1 finalization + L3a F2 expansion + CaptureEvent v2 + onboarding + pinning diagnostics | ~4 weeks |
| P5.B | v1.1 IDE & Electron | L3b Electron preload + L3c VS Code native hook | ~5 weeks |
| P6 | v1.2 Pinning-Proof (Pro) | L2 Frida SSL hook | ~8 weeks |
| P7 | v1.3 Force Capture + Fallback (Pro) | L0 Kernel + L4b Accessibility + JetBrains | ~10 weeks |
| P8 | v2.0 Full Supervisor (Pro) | Automatic scheduling / dedup / auto-degradation | ~6 weeks |

Current-phase task list: [`TASK-006`](Docs/tasks/TASK-006-P5A-subscription-capture.md).

## Contributing

We welcome contributions. Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) first. Critical rules:

- **OSS must never import Pro modules** (enforced by CI)
- **`CaptureEvent v2` schema is a public API** — only additive changes
- **New AI products must map to one of the 10 UCS forms** before a capture layer is added

Report security issues privately per [`SECURITY.md`](SECURITY.md). Community standards: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## Documentation

- Project scope and principles — [`Docs/docs/PROJECT.md`](Docs/docs/PROJECT.md)
- Architecture — [`Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`](Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md)
- Decision records — [`Docs/docs/engineering/adr/`](Docs/docs/engineering/adr/)
- Detailed dev guide (Chinese) — [`Docs/README.md`](Docs/README.md)

## License

[Apache-2.0](LICENSE) · Copyright 2026 PCE Contributors.

The Pro edition is proprietary and distributed under a separate commercial license.

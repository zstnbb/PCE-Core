# AI 产品全景与 PCE 捕获可达性分析

> 基于实际市场调研，非想象推演。调研时间：2026-04

---

## 一、当前 AI 产品分类全景

基于市场实际存在的产品形态，AI 产品可分为以下 **8 大品类**：

### 1. AI 对话/聊天产品（Chat）

用户直接与 AI 模型进行多轮对话。

| 产品 | 形态 | 通信域名 | 协议 |
|------|------|----------|------|
| ChatGPT | Web + Desktop (Win/Mac) + Mobile | `chatgpt.com`, `api.openai.com`, `cdn.oaistatic.com` | HTTPS (REST + SSE streaming) |
| Claude | Web + Desktop (Mac/Win) | `claude.ai`, `api.anthropic.com` | HTTPS (REST + SSE) |
| Gemini | Web + Mobile | `gemini.google.com`, `generativelanguage.googleapis.com` | HTTPS |
| Grok | Web (grok.com) + X 平台 | `grok.com`, `api.x.ai` | HTTPS |
| DeepSeek | Web + API | `chat.deepseek.com`, `api.deepseek.com` | HTTPS (OpenAI 兼容) |
| Perplexity | Web + Mobile + API | `www.perplexity.ai`, `api.perplexity.ai` | HTTPS |
| 豆包/文心一言/通义千问 | Web + Mobile + API | 各自域名 | HTTPS |

**交互模式**：用户 → 产品前端 → 产品后端 → (可能转发至) LLM Provider  
**关键特征**：Web 版通过浏览器访问；桌面版基于 Electron/Tauri；移动端为原生 App。

### 2. AI 编程助手 — IDE 集成类

嵌入 IDE 的 AI 编码辅助工具。

| 产品 | 类型 | 通信域名 | 网络路径 |
|------|------|----------|----------|
| **GitHub Copilot** | VS Code/JetBrains 插件 | `*.githubcopilot.com`, `copilot-proxy.githubusercontent.com`, `api.github.com/copilot_internal/*` | 客户端 → GitHub 中继服务器 → 后端模型（不直连 OpenAI） |
| **Cursor** | 独立 IDE (基于 VS Code) | `api2.cursor.sh` (gRPC + HTTP) | 客户端 → Cursor 自有服务器（代理转发多种模型） |
| **Windsurf** | 独立 IDE (基于 VS Code) | `api.codeium.com`, `auth.windsurf.com` | 客户端 → Codeium 服务器 → 后端模型 |
| **Cline** | VS Code 插件 | 用户自配 API endpoint（`api.openai.com` / `api.anthropic.com` 等） | 客户端 → **直连** LLM Provider |
| **Continue** | VS Code/JetBrains 插件 | 用户自配 API endpoint | 客户端 → **直连** LLM Provider |
| **Aider** | CLI 工具 | 用户自配 API endpoint | 终端 → **直连** LLM Provider |
| **Amazon Q** | VS Code/JetBrains/CLI | AWS 域名 | 客户端 → AWS 服务器 |
| **Augment Code** | VS Code/JetBrains 插件 | Augment 自有域名 | 客户端 → Augment 服务器 |
| **Claude Code** | CLI 工具 | `api.anthropic.com` | 终端 → **直连** Anthropic API |
| **OpenAI Codex** | CLI + VS Code | `api.openai.com` | 终端 → **直连** OpenAI API |

**关键发现**：
- **路径 A（直连 Provider）**：Cline、Continue、Aider、Claude Code、Codex — 用户自带 API Key，直接请求 `api.openai.com` / `api.anthropic.com`
- **路径 B（厂商中继）**：Copilot、Cursor、Windsurf、Amazon Q — 请求先到厂商自有服务器，再由厂商转发给模型提供商
- Cursor 使用 **gRPC + Protobuf**（非纯 HTTP JSON），域名 `api2.cursor.sh`
- Copilot 支持 **HTTP Proxy 配置**（官方文档明确支持）
- Windsurf 支持 proxy 设置，但建议对其域名 **禁用 SSL MITM 检测**

### 3. AI 编程助手 — 独立 IDE 类

独立 AI-native IDE，已在上表中覆盖（Cursor、Windsurf、Zed、Kiro 等）。
与插件类的区别：它们控制整个编辑器进程，网络行为完全由厂商决定。

### 4. AI 图像生成

| 产品 | 形态 | 通信方式 | API 可用性 |
|------|------|----------|-----------|
| DALL-E / GPT Image | API + ChatGPT 内嵌 | `api.openai.com/v1/images/generations` | ✅ 有官方 API |
| Midjourney | **Discord Bot**（无官方 API） | Discord WebSocket + CDN | ❌ 无官方 API，通过 Discord 交互 |
| Stable Diffusion | 本地部署 / API 服务 | localhost 或云端 API | ✅ 本地可完全控制 |
| Adobe Firefly | Web + Creative Cloud 内嵌 | Adobe 域名 | 有限 API |
| Flux / Ideogram | Web + API | 各自域名 | ✅ 有 API |

**关键发现**：
- Midjourney 是特殊案例：没有 HTTP API，用户通过 Discord Bot 命令交互，结果通过 Discord CDN 返回
- Stable Diffusion / ComfyUI 本地运行时走 `localhost`，完全可控
- DALL-E 通过标准 OpenAI API 调用

### 5. AI 写作/文档/效率工具（嵌入式 AI）

AI 功能嵌入 SaaS 产品内部，不作为独立 AI 产品暴露。

| 产品 | AI 功能 | 通信方式 |
|------|---------|----------|
| Notion AI | 文档生成/改写/总结 | Notion 自有后端 → OpenAI/Anthropic（服务端） |
| Grammarly | 写作改进/语法修正 | Grammarly 自有后端（浏览器插件 + 桌面客户端） |
| Jasper | 营销文案生成 | Jasper 后端 → 多模型 |
| Canva Magic Write | 设计+文案 | Canva 后端 |
| Microsoft 365 Copilot | Office 套件 AI | Microsoft Graph + Azure OpenAI |
| Google Workspace AI | Docs/Gmail/Sheets AI | Google 自有后端 |

**关键发现**：
- 这些产品的 AI 请求 **全部在服务端完成**，用户浏览器/客户端看到的只是产品自身的 API（如 `api.notion.so`），不会看到 `api.openai.com`
- 用户端网络流量中 **不包含 AI Provider 域名**
- 这是 PCE 网络代理最难触达的品类

### 6. AI 音频/语音

| 产品 | 形态 | 通信方式 |
|------|------|----------|
| ChatGPT Voice | ChatGPT 桌面/移动端 | OpenAI 域名 + WebRTC/特定 IP 段 |
| ElevenLabs | Web + API | `api.elevenlabs.io` |
| Siri / Alexa / Google Assistant | OS 内置 | OS 级系统调用 → 云端（完全封闭） |
| Whisper API | API | `api.openai.com` |

**关键发现**：
- 语音助手（Siri/Alexa）几乎不可能被拦截，OS 级深度集成
- ElevenLabs 等独立 TTS/STT API 走标准 HTTPS，可拦截
- ChatGPT Voice 使用特定 IP 段（`chatgpt-voice.json` 有定义）

### 7. AI 视频生成

| 产品 | 形态 | 通信方式 |
|------|------|----------|
| Sora | Web + API | `api.openai.com/v1/videos` |
| Runway Gen-4 | Web + API | Runway 域名 |
| Pika | Web + API | Pika 域名 |
| Kling | Web + API | 快手域名 |

**关键发现**：
- 大多数为 Web 产品 + 可选 API
- Sora 走 OpenAI 标准 API
- 其他走各自的云端 API

### 8. 本地/私有部署 AI

| 产品 | 形态 | 通信方式 |
|------|------|----------|
| Ollama | CLI + 本地 API | `localhost:11434` (OpenAI 兼容) |
| LM Studio | 桌面 GUI + 本地 API | `localhost:1234` (OpenAI 兼容) |
| llama.cpp | CLI + 本地 API | `localhost:8080` |
| vLLM | 服务端部署 | 自定义端口 (OpenAI 兼容) |
| LocalAI | 服务端部署 | 自定义端口 (OpenAI 兼容) |

**关键发现**：
- 全部走 **localhost**，请求格式多为 OpenAI 兼容
- 完全在用户控制范围内
- 网络代理可以通过 `localhost` 拦截，或直接在应用层 Hook

---

## 二、AI 交互的三种网络路径（实证）

根据实际抓包和文档验证，AI 产品的网络请求分为三条路径：

```
路径 A：直连 Provider
  用户设备 ──HTTPS──▶ api.openai.com / api.anthropic.com / ...
  代表：Cline, Continue, Aider, Claude Code, 直接 API 调用

路径 B：厂商中继（Vendor Relay）
  用户设备 ──HTTPS──▶ 厂商服务器 ──内部──▶ LLM Provider
  代表：Cursor (api2.cursor.sh), Copilot (*.githubcopilot.com), Windsurf (api.codeium.com)

路径 C：纯 SaaS 嵌入
  用户设备 ──HTTPS──▶ SaaS 产品后端 ──内部──▶ LLM Provider
  代表：Notion AI, Grammarly, Microsoft 365 Copilot, Google Workspace AI

路径 D：本地运行
  应用 ──HTTP──▶ localhost:PORT ──▶ 本地模型
  代表：Ollama, LM Studio, llama.cpp
```

---

## 三、所有可用的捕获/拦截方法

### 方法 1：网络代理（Network Proxy）— 已实现 PoC

- **原理**：mitmproxy 作为 HTTPS 中间人代理，拦截所有经过代理的流量
- **前提**：需要安装 mitmproxy CA 证书到系统信任存储
- **能力**：
  - ✅ 拦截标准 HTTPS REST/SSE 请求
  - ✅ 支持 HTTP Proxy 环境变量（大部分 CLI 工具和支持代理的应用会自动走代理）
  - ⚠️ gRPC/Protobuf 需要额外解析（Cursor）
  - ❌ 无法拦截 Certificate Pinning 的应用
  - ❌ 无法拦截不走系统代理的应用

### 方法 2：浏览器插件（Browser Extension）

- **原理**：Chrome/Firefox Extension 通过 `webRequest` / `declarativeNetRequest` API 或 Content Script 拦截/读取页面内容
- **能力**：
  - ✅ 拦截 Web 版 ChatGPT、Claude.ai、Gemini、Perplexity 等所有 Web AI 产品
  - ✅ 可以读取 DOM 中的对话内容（即使 API 请求不可见）
  - ✅ 可以拦截 SaaS 嵌入式 AI 的**用户界面层**（如 Notion AI 的对话框内容）
  - ✅ 已有先例：`ExportAIChat`、`AI Chat Exporter`、`ContextSwitchAI` 等 Chrome 插件
  - ⚠️ Manifest V3 限制了 `webRequest` 的阻塞能力，但 **读取** 仍可行
  - ❌ 无法覆盖桌面应用和 CLI 工具

### 方法 3：MCP Server（作为 Tool Proxy）

- **原理**：PCE 注册为 MCP Server，提供 tool/resource，MCP Client 调用时 PCE 记录交互
- **支持 MCP 的客户端**（实际已验证）：
  - Claude Desktop、Claude.ai、Claude Code
  - Cursor、Windsurf
  - Cline、Continue
  - VS Code (GitHub Copilot)
  - ChatGPT（支持 remote MCP servers）
  - Amazon Q CLI/IDE
  - Codex (OpenAI)
  - 50+ 其他 MCP 客户端（参见 modelcontextprotocol.io/clients）
- **能力**：
  - ✅ 能够记录 MCP tool 调用的输入/输出
  - ✅ 标准化协议，一次实现覆盖所有 MCP 客户端
  - ✅ 可以作为"采样"(Sampling) 中间层，记录 LLM 请求
  - ⚠️ 只能记录通过 MCP tool 触发的交互，不能记录主对话流
  - ⚠️ 需要用户主动配置 MCP server

### 方法 4：IDE 插件（IDE Extension）

- **原理**：开发 VS Code / JetBrains 插件，在 IDE 进程内部拦截或监听 AI 请求
- **能力**：
  - ✅ 可以监听 VS Code 扩展 API 的输出面板、AI 响应事件
  - ✅ 可以拦截其他插件的网络请求（在进程内部）
  - ✅ 直接覆盖 Copilot、Cline、Continue 等在 VS Code 内的交互
  - ⚠️ 需要针对每个 IDE 平台单独开发
  - ❌ 无法覆盖独立 IDE（Cursor、Windsurf 可能限制第三方插件权限）

### 方法 5：桌面应用 Hook / 辅助功能 API

- **原理**：利用 OS 级辅助功能 API（macOS Accessibility API / Windows UI Automation）或进程注入读取桌面应用的 AI 对话内容
- **能力**：
  - ✅ 理论上可以读取任何桌面应用的 UI 内容（ChatGPT Desktop、Claude Desktop 等）
  - ✅ macOS ChatGPT 桌面版已有先例：`Work with Apps` 功能就是通过辅助功能实现的
  - ⚠️ 高度平台相关，维护成本极高
  - ⚠️ 安全软件可能报警
  - ❌ 无法获取结构化数据，只能获取 UI 文本

### 方法 6：本地模型 Hook

- **原理**：对 Ollama/LM Studio 等本地模型的 API endpoint 做代理或直接 Hook
- **能力**：
  - ✅ localhost API 完全可以代理拦截（配置 `OPENAI_API_BASE` 指向 PCE proxy）
  - ✅ 请求格式标准（OpenAI 兼容），解析成本低
  - ✅ 完全本地，无隐私顾虑

### 方法 7：剪贴板/屏幕监听

- **原理**：监听系统剪贴板或定期截屏 + OCR
- **能力**：
  - ✅ 理论上覆盖一切（任何产品的任何内容都可以通过截屏获取）
  - ❌ 数据质量极差，非结构化
  - ❌ 隐私侵入性极强
  - ❌ 不推荐作为正式方案

---

## 四、逐品类可达性分析矩阵

### 图例
- ✅ **高可达**：技术成熟，可稳定实现
- ⚠️ **中等可达**：可实现但有限制条件
- ❌ **低/不可达**：技术上难以实现或不推荐

| 产品品类 | 网络代理 | 浏览器插件 | MCP Server | IDE 插件 | 本地 Hook | 综合可达性 |
|----------|---------|-----------|------------|---------|----------|-----------|
| **1. AI 对话 — Web 版** | ✅ | ✅✅ | ⚠️ | N/A | N/A | **🟢 高** |
| **2. AI 对话 — 桌面版** | ⚠️¹ | N/A | ⚠️ | N/A | ⚠️ | **🟡 中** |
| **3. AI 对话 — 移动端** | ❌² | N/A | ❌ | N/A | ❌ | **🔴 低** |
| **4. 编程助手 — 直连类** (Cline/Continue/Aider/Claude Code) | ✅✅ | N/A | ✅ | ✅ | N/A | **🟢 极高** |
| **5. 编程助手 — 中继类** (Copilot/Cursor/Windsurf) | ⚠️³ | N/A | ✅ | ⚠️ | N/A | **🟡 中高** |
| **6. AI 图像 — API 类** (DALL-E/SD API) | ✅ | N/A | N/A | N/A | N/A | **🟢 高** |
| **7. AI 图像 — Web 类** (Midjourney/Web UI) | ❌⁴ | ✅ | N/A | N/A | N/A | **🟡 中** |
| **8. AI 图像 — 本地** (SD/ComfyUI) | ✅ | N/A | N/A | N/A | ✅ | **🟢 高** |
| **9. AI 写作 — SaaS 嵌入** (Notion AI/Grammarly) | ❌⁵ | ⚠️⁶ | N/A | N/A | N/A | **🟡 中低** |
| **10. AI 语音 — API** (ElevenLabs/Whisper) | ✅ | N/A | N/A | N/A | N/A | **🟢 高** |
| **11. AI 语音 — OS 内置** (Siri/Alexa) | ❌ | N/A | N/A | N/A | ❌ | **🔴 极低** |
| **12. AI 视频 — API** (Sora/Runway API) | ✅ | N/A | N/A | N/A | N/A | **🟢 高** |
| **13. AI 视频 — Web** | ⚠️ | ✅ | N/A | N/A | N/A | **🟡 中高** |
| **14. 本地模型** (Ollama/LM Studio) | ✅✅ | N/A | N/A | N/A | ✅ | **🟢 极高** |
| **15. AI Agent 平台** (各类 Agent 框架) | ⚠️ | ⚠️ | ✅ | N/A | N/A | **🟡 中** |

**注释**：
1. ¹ 桌面版 Electron 应用通常尊重系统代理，但部分应用（ChatGPT Desktop）做了 SSL pinning 检测，会报错
2. ² 移动端 App 需要 root/越狱 + 证书安装，不实际
3. ³ Cursor 使用 gRPC (非纯 HTTP JSON)，需要 Protobuf 解码；Copilot 域名可拦截但内容是厂商封装过的
4. ⁴ Midjourney 通过 Discord Bot 交互，不走标准 HTTP AI API
5. ⁵ SaaS 嵌入式 AI 的模型请求在服务端完成，客户端流量中不包含 AI Provider 域名
6. ⁶ 浏览器插件可以通过 DOM 读取 AI 生成的内容，但不是 API 级别的结构化数据

---

## 五、各方法组合覆盖度评估

### 场景 1：仅网络代理（当前状态）

**覆盖**：直连 API 类工具 + 部分桌面应用 + 本地模型  
**覆盖率**：约 **30-40%** 的 AI 交互场景  
**盲区**：Web AI 产品（可选代理但用户习惯不配置）、厂商中继加密内容、SaaS 嵌入、移动端

### 场景 2：网络代理 + 浏览器插件

**新增覆盖**：所有 Web 版 AI 产品（ChatGPT Web、Claude.ai、Gemini、Perplexity、Midjourney 等）+ SaaS 嵌入的 UI 层  
**覆盖率**：约 **55-65%**  
**盲区**：厂商中继加密内容、纯桌面/CLI 不走代理的情况、移动端

### 场景 3：网络代理 + 浏览器插件 + MCP Server

**新增覆盖**：所有支持 MCP 的 IDE/桌面工具的工具调用记录  
**覆盖率**：约 **70-80%**  
**盲区**：不支持 MCP 的老旧工具、SaaS 纯服务端 AI、移动端、OS 内置语音助手

### 场景 4：全方法组合（代理 + 浏览器插件 + MCP + IDE 插件 + 本地 Hook）

**覆盖率**：约 **85-90%**  
**剩余盲区**：
- 移动端原生 App（~5%）
- OS 内置语音助手（~2%）
- 完全服务端的 SaaS AI（纯 UI 层可通过浏览器插件部分弥补）（~3-5%）

---

## 六、各方法的工程实现难度与优先级

| 方法 | 实现难度 | 覆盖增量 | ROI | 建议优先级 |
|------|---------|---------|-----|-----------|
| **网络代理** (已完成 PoC) | ★★★☆☆ | 30-40% | 高 | ✅ P0 — 已完成 |
| **浏览器插件** | ★★☆☆☆ | +20-25% | 极高 | 🔴 P1 — 最高优先 |
| **MCP Server** | ★★★☆☆ | +10-15% | 高 | 🟠 P2 — 紧随其后 |
| **本地模型 Hook** | ★☆☆☆☆ | +5-8% | 高 | 🟡 P3 — 简单且有价值 |
| **IDE 插件** (VS Code) | ★★★★☆ | +5-8% | 中 | 🟢 P4 — 可选增强 |
| **桌面 App Hook** | ★★★★★ | +3-5% | 低 | ⚪ P5 — 远期探索 |
| **移动端拦截** | ★★★★★ | +3-5% | 极低 | ⚪ 暂不考虑 |

---

## 七、关键域名清单（已验证）

PCE 网络代理 Allowlist 需要覆盖的核心域名：

### AI Model Provider API（路径 A 直连）
```
# OpenAI
api.openai.com

# Anthropic
api.anthropic.com

# Google
generativelanguage.googleapis.com
aiplatform.googleapis.com

# xAI
api.x.ai

# DeepSeek
api.deepseek.com

# Mistral
api.mistral.ai

# Cohere
api.cohere.com

# Groq
api.groq.com

# Together AI
api.together.xyz

# Fireworks
api.fireworks.ai

# Perplexity
api.perplexity.ai
```

### AI Tool Vendor Relay（路径 B 中继）
```
# GitHub Copilot
*.githubcopilot.com
copilot-proxy.githubusercontent.com

# Cursor
api2.cursor.sh

# Windsurf / Codeium
api.codeium.com

# ElevenLabs
api.elevenlabs.io

# Runway
api.runwayml.com

# OpenRouter (聚合服务)
openrouter.ai
```

### 本地模型（路径 D）
```
localhost:11434    # Ollama
localhost:1234     # LM Studio
localhost:8080     # llama.cpp server
```

---

## 八、结论与建议

### 核心发现

1. **AI 产品品类已经非常丰富**，覆盖对话、编程、图像、写作、语音、视频、本地模型等 8 大类，15+ 子品类
2. **网络路径不止一条**：直连 Provider 只占约 40%，大量产品走厂商中继或纯 SaaS 服务端
3. **没有单一方法能覆盖全部**，必须组合多种手段
4. **浏览器插件是 ROI 最高的下一步**：实现简单（Manifest V3 Content Script），一次覆盖所有 Web AI 产品
5. **MCP 是 IDE/Agent 领域的标准协议**，50+ 客户端已支持，一次实现广泛覆盖

### 推荐实施路径

```
Phase 1 (已完成): 网络代理 PoC ─────────────── 覆盖 ~35%
Phase 2 (下一步): 浏览器插件 ─────────────────── 覆盖 ~60%
Phase 3:         MCP Server + 本地模型 Hook ──── 覆盖 ~80%
Phase 4:         IDE 插件 (VS Code) ──────────── 覆盖 ~85%
Phase 5 (远期):  桌面 Hook + 其他 ────────────── 覆盖 ~90%
```

### 理论上限

考虑到移动端和 OS 级语音助手的不可达性，在 **桌面/开发者场景** 下，PCE 的理论覆盖上限约为 **90-95%**。如果只计算"用户主动使用的 AI 工具"（排除被动触发的 OS 级 AI），覆盖率可以逼近 **95%**。

### 不可达区域（明确放弃）

- 移动端原生 App 内的 AI 交互（需要 root/越狱）
- OS 内置语音助手（Siri/Alexa/Google Assistant 的系统级调用）
- 完全服务端的 AI 处理（如后端微服务间的 AI 调用，不经过用户设备）

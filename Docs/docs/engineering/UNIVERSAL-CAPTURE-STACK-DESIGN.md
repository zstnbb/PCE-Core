# Universal Capture Stack (UCS) · 工业级设计

> **核心哲学(沿用并加固)**:先抓再说。抓不到 = 永久丢失。
> 分层兜底;结构化优先;用户可见可控;隐私不打折。

| 元信息 | 值 |
|---|---|
| 版本 | v1 (Draft) |
| 日期 | 2026-04-18 |
| 作者 | Cascade(P4 落幕之夜起草) |
| 状态 | 待批 — ADR-009 决策后进入 P5 执行 |
| 关系 | 扩展并取代 `CAPTURE-MAXIMALIST-DESIGN.md`(三层 → 五层 + 10 形态) |
| 前置阅读 | `ARCHITECTURE.md`、ADR-001/002/003/004/006/007 |
| 后置 ADR | ADR-009(UCS 采纳 · 待批) |

---

## 目录

- [0 · 文档范围](#0--文档范围)
- [1 · 需求规格](#1--需求规格)
- [2 · 架构总览](#2--架构总览)
- [3 · 层级详细设计](#3--层级详细设计)
  - 3.1 L0 · Kernel Traffic Redirector
  - 3.2 L1 · TLS MITM
  - 3.3 L2 · Frida SSL Hook
  - 3.4 L3 · Application-Level Hook(6 子层)
  - 3.5 L4 · UI / Accessibility
- [4 · Capture Supervisor](#4--capture-supervisor)
- [5 · 统一数据契约(CaptureEvent v2)](#5--统一数据契约captureevent-v2)
- [6 · 跨切面:隐私 / 安全 / 性能 / 可观测](#6--跨切面)
- [7 · 代码物料地图](#7--代码物料地图)
- [8 · 分发和打包](#8--分发和打包)
- [9 · 分阶段落地(P5→P8)](#9--分阶段落地)
- [10 · 验收标准](#10--验收标准)
- [11 · 风险寄存簿](#11--风险寄存簿)
- [12 · 开放决策点](#12--开放决策点)
- [附录 A · 10 形态图谱](#附录-a--10-形态图谱)
- [附录 B · 捕获层 × 形态矩阵](#附录-b--捕获层--形态矩阵)
- [附录 C · 术语表](#附录-c--术语表)

---

## 0 · 文档范围

### 0.1 产品三层心智模型

PCE 由三层构成,每一层独立演进、独立产品化:

```
┌─────────────────────────────────────────────────┐
│  抓 Capture                                      │
│  把"用户与 AI 的每一次交互"变成可落地的数据       │
│  ← 本文档定义                                    │
├─────────────────────────────────────────────────┤
│  存 Store                                        │
│  归一化 · 索引 · 向量化 · 可搜索 · 可导出         │
│  ← ARCHITECTURE.md · ADR-004 · ADR-007           │
├─────────────────────────────────────────────────┤
│  渲染 Render                                     │
│  搜索 · 时间线 · 洞察 · 回放 · 分享 · 报告        │
│  ← 待设计(另开文档)                              │
└─────────────────────────────────────────────────┘
```

本文档**只**定义"抓"。涉及"存"仅限于数据契约(`CaptureEvent` schema 和 ingest API),涉及"渲染"仅在需要捕获被后续回放时提及。

### 0.2 渐进发布策略

**原则**:朝终极架构爬,爬到哪里稳定了就打包发布那一块切片。永不为了"产品化"阉割架构。

```
Release Track ·──┐
                 │
  P5.A  ──────→ ├── v1.0 "Subscription Capture"  = L1 + L3a 扩展全面
                │
  P5.B  ──────→ ├── v1.1 "IDE & Electron"        = v1.0 + L3b/L3c preload/扩展
                │
  P6    ──────→ ├── v1.2 "Pinning-Proof"         = v1.1 + L2 Frida
                │
  P7    ──────→ ├── v1.3 "Force Capture"         = v1.2 + L0 Kernel + L4b AX
                │
  P8    ──────→ └── v2.0 "Full Supervisor"       = v1.3 + 全自动调度/降级
```

每个切片都是**用户可装、可用、可卖**的完整产品。

### 0.3 读完此文档的工程师应该能

1. 理解 UCS 五层结构、职责边界、数据流向
2. 找到每个子系统应该落地在代码库的哪个位置
3. 看到每一阶段(P5.A→P8)的明确验收标准
4. 识别主要风险并按既定路径缓解
5. 独立写出第一条 MR 而不问"要放哪儿"

---

## 1 · 需求规格

### 1.1 功能需求

**FR-1 覆盖 10 种 AI 产品形态**(见附录 A)

| Tier | 形态数 | 目标 | 使用量占比估 |
|---|---|---|---|
| T1 可完整捕获 | 7 | 结构化 JSON 入库,保留 model/messages/tools/tokens | ~75% |
| T2 只能 UI 级 | 2 | 文本 + 时间戳 + session 归属 | ~15% |
| T3 不投资 | 1 | 明确告知用户"不支持",给替代建议 | ~10% |

**FR-2 并发多层捕获**:同一对话可被多层同时观测,Supervisor 按 `pair_id` + `fingerprint` 去重。

**FR-3 自动降级**:任一层健康探针失败 → Supervisor 在 ≤ 30s 内启动降级链,不依赖人工。

**FR-4 零数据丢失**:任一层入口失败时,事件写入 `raw_captures.fallback` 分区,等人工复核或自动重试。

**FR-5 增量可部署**:每层独立启停、独立升级,不阻塞其他层。

**FR-6 完全本地**:默认只写本地 SQLite;OTLP 导出是可选二级通道(ADR-007)。

**FR-7 可关闭**:每层都可独立关闭;最终可关停整个 UCS(降为纯手工导入)。

### 1.2 非功能需求

| ID | 名称 | 指标 |
|---|---|---|
| NFR-1 | 代理吞吐 | mitmproxy 端 ≥ 2Gbps 本地 loopback,≤ 5% 系统 CPU |
| NFR-2 | 入库延迟 | 单条 CaptureEvent P95 ≤ 300ms |
| NFR-3 | Supervisor 调度延迟 | 决策 P95 ≤ 50ms |
| NFR-4 | 浏览器扩展内存 | 每标签页额外内存 ≤ 4MB |
| NFR-5 | 跨平台 | Win 10+、macOS 12+、Ubuntu 22+/Debian 12+(移动 Android 12+/iOS 16+ 仅 PAC 兜底) |
| NFR-6 | 隐私 | 全部层强制调用统一 `redact_headers_json` + PII 感知脱敏 |
| NFR-7 | CA 安全 | 只接受 mitmproxy 自签 CA,拒绝第三方根 |
| NFR-8 | 脚本安全 | L2 Frida 脚本必须 Ed25519 签名,签名校验失败直接拒绝加载 |
| NFR-9 | 可观测 | 每层暴露 `capture.rate` / `capture.failure` / `capture.latency_p95` |
| NFR-10 | 测试 | 每层单元覆盖 ≥ 85%,每形态至少 1 个 e2e |

### 1.3 非目标(显式不做)

- 移动端 root/jailbreak 方案
- OS-integrated AI 的逆向(Apple Intelligence / Windows Copilot NPU)
- 渲染层产品化
- SaaS 云端(本地优先)
- AI 模型侧托管/评测(ADR-001 再强调:只记录,不改写)

---

## 2 · 架构总览

### 2.1 逻辑视图

```
┌────────────────────────────────────────────────────┐
│                Capture Supervisor                   │
│  · App Discovery  · Policy Table  · Health Probes   │
│  · Dynamic Degradation  · Dedup (pair_id+hash)      │
│  · Per-App Dashboard                                │
└──┬─────┬──────┬──────┬──────┬─────────────────────┘
   ▼     ▼      ▼      ▼      ▼
  ┌──┐ ┌───┐ ┌────┐ ┌────┐ ┌────┐
  │L0│ │L1 │ │L2  │ │L3  │ │L4  │
  └┬─┘ └─┬─┘ └─┬──┘ └─┬──┘ └─┬──┘
   │     │     │      │      │
   └─────┴─────┴──────┴──────┘
                ▼
       CaptureEvent v2 Envelope
                ▼
   [Ingest Gateway] /api/v1/captures/v2
                ▼
        [Normalizer Pipeline]
                ▼
   sessions + messages + spans
   (SQLite + FTS + OTLP + DuckDB + Vector)
```

### 2.2 组件职责 & 代码落点

| 组件 | 使命 | 代码位置 | 现状 |
|---|---|---|---|
| Supervisor | 发现/策略/健康/降级/去重 | `pce_core/capture_supervisor/`(新) | 🔴 0% |
| L0 Kernel | 内核流量导流 | `pce_agent_kernel/`(新,原生) | 🔴 0% |
| L1 MITM | mitmproxy + CA 向导 + 系统代理 | `pce_proxy/` + `pce_core/cert_wizard/` + `pce_core/proxy_toggle/` | 🟢 85% |
| L2 Frida | 进程内 TLS 明文 | `pce_agent_frida/`(新) | 🔴 0% |
| L3a 浏览器扩展 | Web 页面捕获 | `pce_browser_extension_wxt/` | 🟡 60% |
| L3b Electron preload | 桌面 Electron hook | `pce_agent_electron/`(新) | 🔴 0% |
| L3c IDE 扩展 | VS Code / JetBrains | `pce_ide_vscode/` + `pce_ide_jetbrains/`(新) | 🔴 0% |
| L3d CDP | Chromium 调试协议 | `pce_core/cdp/` | 🟢 80% |
| L3e LiteLLM gateway | SDK 反向代理 | `pce_core/sdk_capture_litellm.py` | 🟢 80% |
| L3f OTel auto-instr | Headless Agent | `pce_core/otel_exporter.py` | 🟢 80% |
| L4a 剪贴板 | 用户主动复制 | `pce_core/clipboard_monitor.py` | 🟢 70% |
| L4b Accessibility | macOS AX / Win UIA | `pce_agent_ax/`(新) | 🔴 0% |
| L4c 触发式 OCR | 屏幕 OCR | `pce_core/ocr_capture.py`(新) | 🔴 0% |
| CaptureEvent 契约 | 统一 envelope | `pce_core/capture_event.py`(新) | 🔴 0% |
| Ingest Gateway | `/api/v1/captures/v2` | `pce_core/server.py` | 🟢 90% |

### 2.3 数据流向

1. 任一层观测到 AI 交互 → 构造 `CaptureEvent v2`
2. 事件通过本地 HTTP(L1/L2/L3/L4)或进程内(L3 in-process)到 `/api/v1/captures/v2`
3. Gateway 写入 `raw_captures` + `raw_capture_meta`(UCS 扩展表)
4. Normalizer 消费 → `sessions` + `messages` + `spans`
5. Supervisor 订阅事件流 → 更新仪表板每应用健康卡 + 去重决策
6. 可选:OTLP 推送 Phoenix/Langfuse(ADR-007)

### 2.4 关键不变式

- **单源可认证**:所有事件带 `source ∈ {L0, L1, L2, L3a..f, L4a..c}`,Normalizer 按来源选解析器
- **双向可追溯**:`session_id` ↔ `message_id` ↔ `raw_capture_id` 永远互查
- **层无状态**:除 Supervisor 外,各层不跨事件保状态
- **降级不丢失**:层降级后事件流不中断,直到 Supervisor 主动停
- **Supervisor 无改写权**:只加 `deduped_by` / `quality_tier`,绝不动事件内容

---

## 3 · 层级详细设计

### 3.1 L0 · Kernel Traffic Redirector

**使命**:当应用**不**尊重系统代理 / 用自管 DNS / 走原生 socket / 绑定特定网卡时,从 OS 内核把包强制导到 L1 的 mitmproxy 监听端口。

**默认关闭**。仅在用户显式"强制捕获 X 应用"时启用。

#### 3.1.1 平台实现

| OS | 技术 | 实现位置 | 用户授权流程 |
|---|---|---|---|
| Windows | **WinDivert**(用户态 DLL+驱动)/ WFP(企业版) | `pce_agent_kernel/windows/pce_divert.dll + .sys` | 首次启用 UAC + 驱动签名安装 |
| macOS | **NetworkExtension/NEFilterPacketProvider** | `pce_agent_kernel/macos/PCEFilter.systemextension` | 用户在"安全性"手动批准 System Extension |
| Linux | **nftables DNAT by-cgroup** 或 **eBPF cgroup/skb** | `pce_agent_kernel/linux/pce_cgroup_redirect.service` | `sudo systemctl` 安装 |

#### 3.1.2 接口契约

```python
# pce_core/capture_supervisor/agents/kernel.py
class KernelRedirector(Protocol):
    def available(self) -> bool: ...
    def enable(self, pid_or_name: int | str, target: tuple[str, int]) -> OpResult: ...
    def disable(self, pid_or_name: int | str) -> OpResult: ...
    def status(self) -> dict: ...  # {pid: {"target": "127.0.0.1:8080", "since": ts}}
```

`OpResult` 带 `needs_elevation` / `dry_run` 字段,与 `cert_wizard.CertOpResult` 结构一致。

#### 3.1.3 安全 & 合规

- Windows 驱动强制 EV 代码签名(私钥线下金库)
- macOS System Extension 需要 Developer ID + notarization
- Linux systemd unit 装在 `/etc/systemd/system/`,卸载自动清理
- 维护黑名单 `L0_FORCE_BYPASS_LIST`:银行 app、支付 app、企业 VPN 进程,永不导流

#### 3.1.4 验收(P7)

- ✅ 对故意不尊重 `HTTPS_PROXY` 的测试进程(`curl --noproxy='*'`),启用后能被 L1 抓到
- ✅ 关闭后 5s 内恢复直连
- ✅ 误导流率 < 0.01%(黑名单回归测试)
- ✅ Windows Defender / macOS Gatekeeper 不报警

---

### 3.2 L1 · TLS MITM(现状主力,85% 就绪)

**使命**:解密 HTTPS,在协议层拿 JSON / SSE / gRPC 帧。

#### 3.2.1 构件(已有 85%)

| 构件 | 位置 | 状态 |
|---|---|---|
| mitmproxy addon | `pce_proxy/addon.py` | 🟢 |
| Allowlist + SMART 启发式 | `pce_proxy/heuristic.py` + `pce_core/config.py::ALLOWED_HOSTS` | 🟢 |
| CA 向导(Win/macOS/Linux) | `pce_core/cert_wizard/manager.py` | 🟢 |
| 系统代理开关 | `pce_core/proxy_toggle/` | 🟢 |
| PAC 文件端点 | `pce_core/server.py::/proxy.pac` | 🟢 |
| Electron launcher | `pce_core/electron_proxy.py` | 🟢(4/5 订阅应用) |
| 移动端向导 | `pce_core/mobile_wizard.py` | 🟢 |

#### 3.2.2 P5.A 要补的 UX 闭环

1. **Allowlist 补全** — `pce_core/config.py`:
   ```python
   ALLOWED_HOSTS |= {
       "api.githubcopilot.com",
       "api.individual.githubcopilot.com",
       "chatgpt.com",
       "ab.chatgpt.com",
       "claude.ai",
       "api3.cursor.sh",
   }
   ```

2. **KNOWN_APPS 补全** — `pce_core/electron_proxy.py::KNOWN_APPS` 加 `ChatGPT Desktop` + `Codex CLI` 两条

3. **首跑向导** — `pce_core/server.py` 新增 `/onboarding` 端点,一键串联:
   - `cert_install` → `proxy_enable` → Electron 应用重启提示
   - 结束后展示"捕获健康度"卡片

4. **Pinning 失败检测** — `pce_proxy/addon.py::tls_failed` hook 记录失败 `host`,仪表板打红点

5. **Per-App bypass UI** — `proxy_toggle.enable_system_proxy(bypass=...)` 已有,UI 暴露让用户勾选排除进程/域名

#### 3.2.3 已知死角 & 兜底路径

| 死角 | 兜底层 |
|---|---|
| 证书 pinning | L2 Frida |
| mTLS 客户端证书 | L3 app hook |
| gRPC/Protobuf body(如 Cursor) | L3b Electron preload |
| 非代理尊重应用 | L0 kernel |

#### 3.2.4 验收(P5.A 完成 = v1.0 发布)

- ✅ 新装用户 5 分钟内完成"装 → CA 装好 → 系统代理打开 → 第一条 ChatGPT Desktop 对话被抓并渲染到仪表板"
- ✅ 端到端失败率 < 1%(连接失败 / 证书拒绝 / pinning 报错 / 代理残留任一)
- ✅ Pinning 失败应用在仪表板显示红点 + 3 条可操作建议
- ✅ 所有现有 724 测试继续 pass

---

### 3.3 L2 · Process-Level TLS Unwrap(Frida SSL Hook)

**使命**:绕过应用级证书 pinning — 在应用进程内部,在 TLS 库加密**前**、解密**后**读明文。

#### 3.3.1 Runtime 矩阵

| Runtime | Hook 点 | 实现技术 |
|---|---|---|
| **Electron / Node.js** | `BoringSSL::SSL_read / SSL_write` | Frida JS,attach 到 `Electron Framework.dll` 或 `libnode.dylib` |
| **纯 OpenSSL 进程** | `SSL_read / SSL_write` | Frida + 符号扫描 |
| **Go 静态二进制** | `crypto/tls.(*Conn).Read` | Frida + 符号表(strip 时 fallback DWARF) |
| **.NET SChannel** | `EncryptMessage / DecryptMessage` | **EasyHook / Detours**(非 Frida) |
| **Linux 通用** | `libssl.so::SSL_*` | **eBPF uprobes**(无注入,CAP_BPF) |

#### 3.3.2 架构

```
          PCE (pce_agent_frida)
                 │
  ┌──────────────┼──────────────────┐
  ▼              ▼                  ▼
frida-server  ssl_log.js        reporter.js
(预编译到     (hook SSL_*,      (plaintext →
 bin/)        加 pid/host/sni)   /api/v1/captures/v2)
       │
       ▼
  目标进程(Cursor / ChatGPT Desktop / Claude Desktop / ...)
```

- **frida-server** 按平台预编译打包进 `pce_agent_frida/bin/{win,mac,linux}/`
- **ssl_log.js** hook 脚本,读到明文后加 context → 发给 reporter
- **reporter.js** 通过 localhost HTTP 把 CaptureEvent 发到 `/api/v1/captures/v2`

#### 3.3.3 脚本安全(关键)

- PCE 维护 **Ed25519 签名对**:公钥 `pce_agent_frida/public.pem`(随包分发),私钥线下
- 仓库中所有 `*.js` 都带 `.sig` 签名文件
- Frida agent 运行时先 `verify()` 再 `load()`,签名失败直接退出
- **目的**:防止第三方替换 `ssl_log.js` 借 PCE 进程偷应用明文

#### 3.3.4 AV / EDR 兼容

- 不用 ptrace 注入(Defender 高风险)
- macOS:`posix_spawn(..., POSIX_SPAWN_SETEXEC)` + frida gadget
- Windows:`QueueUserAPC` + PE section injection(已知过 Defender)
- Linux:eBPF uprobes 完全不注入,零风险
- 降级预案:任一平台 Frida 失效 → Supervisor 切到 L3 或 L4

#### 3.3.5 接口契约

```python
# pce_core/capture_supervisor/agents/frida.py
class FridaSSLAgent(Protocol):
    def available(self) -> bool: ...
    def supported_processes(self) -> list[ProcessHint]: ...
    def attach(self, pid: int, script_id: str = "ssl_log") -> OpResult: ...
    def detach(self, pid: int) -> OpResult: ...
    def status(self) -> dict: ...  # per-pid health
```

#### 3.3.6 验收(P6 完成 = v1.2 发布)

- ✅ ChatGPT Desktop 启用 L2 后 pinning 失败率从 >30% 降到 <1%
- ✅ 3 家主流 AV(Defender / Kaspersky / 360)误报率 < 5%
- ✅ Frida 崩溃时 Supervisor 15s 内标记 `L2_down`,L1 继续托底
- ✅ 脚本签名篡改测试 — 篡改后必须拒绝加载
- ✅ 目标进程崩溃率 < 0.1%(Frida 附加不伤害宿主)

---

### 3.4 L3 · Application-Level Hook(6 子层)

**使命**:在应用自己的 API 层截取,跳过协议编解码,拿最高质量的结构化对象。

**UCS 最丰富、PCE 最大资产**所在层。6 个子层并行,各自覆盖不同形态。

#### 3.4.a L3a · 浏览器扩展(PCE 核心资产)

**使命**:覆盖 F1 Web Chat + F2 Web-Embedded AI + F3 Browser Ext AI(≈ 55% 使用量)。

**已有**(`pce_browser_extension_wxt/`,60% 就绪):
- 13 个站点 TS 提取器(ChatGPT/Claude/Gemini/Perplexity/DeepSeek/Google AI Studio/Copilot Web/Poe/Grok/HuggingFace/Manus/Zhipu + generic)
- `interceptor-ai-patterns.ts` — fetch/XHR 拦截框架
- `detector.content.ts` + `universal-extractor.content.ts` — 未知站点启发式
- 双模式权限(sideload / webstore,ADR-003)

**P5.A 要补 — F2 SaaS-Embedded 覆盖**:

| 站点 | DOM 锚点提示 | 优先级 |
|---|---|---|
| Microsoft 365 Copilot(Office Web) | `[data-copilot-message]`, 侧边栏 | P1 |
| Notion AI | `.notion-ai-block`, `/api/v3/generate` | P1 |
| Figma AI | iframe → canvas + 模型侧边栏 | P2 |
| Gmail "Help me write" | `[role="dialog"][aria-label*="Help me write"]` | P2 |
| Slack AI(Slack Copilot) | `[data-qa="thread_summary"]` | P3 |
| Linear AI / Jira AI / Intercom AI | 公司策略各异,单独处理 | P4 |

**P5.A 附带 — F3 Browser Ext AI 生态兼顾**:

- Monica / MaxAI / Sider / Merlin 等 AI 浏览器扩展流量走 PCE 代理 → **L1 自动覆盖**,无需扩展改造

**契约**:`source=L3a_browser_ext`,事件通过 `POST /api/v1/captures/v2` 递交

#### 3.4.b L3b · Electron Preload Injector(P5.B 核心新建)

**使命**:覆盖 F4 Desktop Chat App + F5 IDE AI 中的 Electron 类(Cursor / Windsurf / VS Code / ChatGPT Desktop / Claude Desktop)。

**原理**:Electron 应用启动时注入 `preload.js`,劫持 `window.fetch` / `XMLHttpRequest` / `WebSocket`,所有 AI API 调用透传给 PCE。

**实现位置**:`pce_agent_electron/`(新建)

```
pce_agent_electron/
├── preload.js              # 注入脚本(通吃 Electron AI)
├── launcher.py             # 启动应用时注入(扩展 pce_core/electron_proxy.py)
├── detector.py             # 发现机器上的 Electron 应用
└── signatures/             # preload.js 签名文件
```

**注入手段**(按可靠性排序):

1. **`NODE_OPTIONS=--require <preload.js>`**(首选,环境变量,无需改参数)
2. **命令行 `--preload <path>`**(干净,但应用签名后改参数会失败)
3. **Electron Fuses + `runAsNode: true`**(部分应用强制关,失败降级)
4. **侧安装 debug build**(极端情况,不推荐)

**Cursor gRPC 特别处理**:Cursor 走 `api2.cursor.sh` gRPC-web,preload 拦到的是 `fetch()` 层 **已 protobuf-encoded 的 body**。方案:hook `window.protobuf` 或 Cursor 的 gRPC 客户端实例(需逆向 Cursor bundle,P5.B 后期)。

**契约**:preload.js 读取 `PCE_INGEST_URL`(由 launcher 注入),每次 fetch 拦截发 `source=L3b_electron_preload`

**验收(P5.B 完成 = v1.1 发布)**:

- ✅ Cursor `/StreamChat` 每条调用被记录,body 为 JSON(非 protobuf bytes)
- ✅ VS Code Copilot 插件内 chat 调用被记录
- ✅ Claude Desktop / ChatGPT Desktop 每条消息捕获率 ≥ 95%
- ✅ 注入失败时优雅退化到 L1 MITM

#### 3.4.c L3c · VS Code 扩展(P5.B 新建)

**使命**:覆盖 F5 的 VS Code + Copilot / Cline / Continue / Cody 等扩展生态。

**实现位置**:`pce_ide_vscode/`(新建,独立 npm 包 + TS 项目)

**Hook 点**:
- `vscode.lm.registerChatResponseProvider`(VS Code 1.88+ Language Model API)
- `vscode.chat.registerChatParticipant`(Chat Participant API)
- `vscode.window.registerTerminalProfileProvider`(CLI AI 辅助)

**契约**:扩展通过本地 HTTP `/api/v1/captures/v2` 递交,`source=L3c_vscode_ext`

**分发**:VS Code Marketplace,ID `pce.pce-vscode`,独立签名

**验收(P5.B)**:

- ✅ Copilot 新消息 1s 内被记录
- ✅ 关闭 PCE 时扩展静默不报错

#### 3.4.d L3d · Chrome DevTools Protocol(已有 80%)

**使命**:对用户已开启 `--remote-debugging-port` 的 Chromium 实例,绕过扩展直接读 Network 事件。

**已有**:`pce_core/cdp/driver.py`(P4 落地)

**P5 定位**:作为 L3a 浏览器扩展的 **替代**(扩展装不上的场景,如企业策略限制),**不是主路径**。

**契约**:`source=L3d_cdp`

#### 3.4.e L3e · LiteLLM Gateway(已有 80%)

**使命**:给 BYOK 用户(Codex API 模式 / 自写脚本 / Jupyter notebook)一个 drop-in 反向代理。

**已有**:`pce_core/sdk_capture_litellm.py`,支持 OpenAI/Anthropic/多 provider 通过 LiteLLM 转发。

**P5 补丁**:打包成独立 `pce-gateway` 二进制,零依赖启动,文档指向 F10 Headless Agent 用户。

**契约**:`source=L3e_litellm`

#### 3.4.f L3f · OTel Auto-Instrumentation(已有 80%)

**使命**:覆盖 F10 Headless Agent(LangChain / CrewAI / dify / n8n 等服务端部署)。

**已有**:`pce_core/otel_exporter.py` + ADR-007 OTLP 二级通道

**P5 补丁**:写文档《PCE as OpenTelemetry collector for AI》,提供 LangChain / LlamaIndex / Haystack 三家 `opentelemetry-instrumentation-*` 配置模板。

**契约**:`source=L3f_otel`(OTLP 直送,跳过 ingest gateway)

---

### 3.5 L4 · UI / Accessibility Fallback

**使命**:前 4 层都失败时退回读 UI,永远不让捕获"全部失败"。

#### 3.5.a L4a · 剪贴板监听(已有 70%)

**已有**:`pce_core/clipboard_monitor.py`

**P5 补丁**:
- 加 AI 对话特征识别(多行 + `"User:"`/`"Assistant:"` 模式)降低误报
- 与 L1/L3 的 session 做关联(时间窗口 + 文本指纹)

**契约**:`source=L4a_clipboard`

#### 3.5.b L4b · Accessibility API(P7 新建)

**使命**:对 T3 边缘上的 OS-integrated AI 或 pinning + 无 Frida 路径的应用兜底。

**实现**:`pce_agent_ax/`(新建,原生代码 + Python bindings)

| OS | API | Python 桥 |
|---|---|---|
| macOS | `AXUIElement` / `AXObserver` | `pyobjc` |
| Windows | `IUIAutomation` / `UIAutomationCore` | `pywinauto` + `comtypes` |
| Linux | AT-SPI2 | `python-atspi` |

**触发策略**:**不被动监听**(侵入感太强),只在用户快捷键 `Ctrl+Alt+P` 触发"捕获当前窗口 AI 对话"时一次性扫描。

**契约**:`source=L4b_accessibility`

#### 3.5.c L4c · 触发式 OCR(P7 新建)

**使命**:Accessibility 拿不到的情况(AI 在 canvas 绘制文字、或无 role 信息)最后兜底。

**实现**:`pce_core/ocr_capture.py`(新建),`tesseract` 或 `paddleocr`

**触发策略**:仅在用户快捷键显式触发时扫描屏幕矩形区域,**绝不**录屏 / 定期截屏

**契约**:`source=L4c_ocr`

---

## 4 · Capture Supervisor

### 4.1 职责

1. **App Discovery**:扫描安装的 AI 相关进程/应用,维护"活跃列表"
2. **Policy Table**:每应用的 [首选层, 降级顺序, 健康指标阈值]
3. **Health Probes**:每 10s 检查各层健康,写 `layer_health` 表
4. **Dynamic Degradation**:层失败 → 自动激活降级链
5. **Dedup**:多层同时捕获时按 `(pair_id, fingerprint)` 去重,标记 `primary_source`
6. **Dashboard API**:为前端提供 `/api/v1/supervisor/status` 每应用健康卡
7. **无改写权**:Supervisor **只**加 `deduped_by` / `quality_tier`,绝不动事件内容

### 4.2 策略表 Schema

```python
# pce_core/capture_supervisor/policy.py
@dataclass(frozen=True)
class CapturePolicy:
    form_id: str                    # F1..F10
    app_name: str                   # "cursor", "chatgpt-desktop", "vscode", ...
    primary_layer: str              # "L3b_electron_preload"
    fallback_chain: list[str]       # ["L2_frida", "L1_mitm", "L4b_ax"]
    health_checks: dict             # {"tls_handshake_failure_rate": 0.05, ...}
    min_quality_tier: Literal["T1", "T2", "T3"] = "T2"


# pce_core/capture_supervisor/policies.yaml(可热加载)
CURSOR = CapturePolicy(
    form_id="F5",
    app_name="cursor",
    primary_layer="L3b_electron_preload",
    fallback_chain=["L2_frida", "L1_mitm"],
    health_checks={"hook_alive": True, "event_rate_per_min": 1},
)

CHATGPT_DESKTOP = CapturePolicy(
    form_id="F4",
    app_name="chatgpt-desktop",
    primary_layer="L1_mitm",
    fallback_chain=["L2_frida", "L4b_ax"],
    health_checks={"tls_handshake_failure_rate": 0.1, "pinning_errors_per_min": 5},
)
```

### 4.3 健康探针

每层实现 `HealthProbe` 协议:

```python
class HealthProbe(Protocol):
    def check(self) -> HealthReport: ...

@dataclass
class HealthReport:
    layer: str                      # "L1_mitm", ...
    healthy: bool
    metrics: dict[str, float]       # {"event_rate": 3.2, "latency_p95_ms": 42}
    last_error: Optional[str]
    since: datetime
```

Supervisor 聚合结果写 `layer_health` 表,UI 订阅 WebSocket `/ws/supervisor`。

### 4.4 降级算法(伪码)

```
on_health_report(report):
    for app in policy_table.apps_using(report.layer):
        if app.current_layer == report.layer and not report.healthy:
            next_layer = pick_next(app.fallback_chain, healthy_layers)
            if next_layer:
                activate(app, next_layer)
                log_event("supervisor.degrade",
                          app=app.name,
                          from_layer=report.layer,
                          to_layer=next_layer)
            else:
                log_event("supervisor.no_fallback", app=app.name)
                notify_user(app)
```

升级路径(当更高层恢复健康):每 60s 检查一次,若 `primary_layer` 可用则回切。

### 4.5 去重算法

```
on_capture_event(event):
    fingerprint = hash(event.pair_id, event.normalized_body_prefix)
    existing = lookup(fingerprint, window=30s)
    if existing:
        event.deduped_by = existing.capture_id
        # 两条都留,只在视图层按 primary_source 聚合展示
    persist(event)
```

**双留**而不是丢:多层数据冗余有助于后续质量诊断和字段补全。

### 4.6 接口契约

```
GET  /api/v1/supervisor/status         → {apps: [{name, current_layer, health, ...}]}
POST /api/v1/supervisor/policy/reload  → 热加载 policies.yaml
POST /api/v1/supervisor/app/{name}/layer  body:{layer} → 手动指定层
POST /api/v1/supervisor/app/{name}/pause   → 暂停此应用捕获
WS   /ws/supervisor                    → 健康事件流
```

### 4.7 验收(P8 完成 = v2.0 发布)

- ✅ 模拟 L1 失败(关 mitmproxy),ChatGPT Desktop 在 30s 内切到 L2 或 L4
- ✅ Supervisor 崩溃时,各层继续独立工作(不单点)
- ✅ 仪表板每应用健康卡与后端状态 1s 内一致
- ✅ 手动指定层的 API 可用,UI 暴露

---

## 5 · 统一数据契约(CaptureEvent v2)

这是"抓"向"存"递交的**唯一契约**,所有层必须严格遵循。

### 5.1 设计原则

- **向前兼容**:v2 是 v1 的超集,老 ingest 端点 `/api/v1/captures` 保留,新端点是 `/api/v1/captures/v2`
- **扁平优先**:顶层字段为一等公民(Normalizer 不用深挖 meta)
- **来源可溯**:每条事件必须携带 `source` + `agent_version` + `capture_time_ns`
- **层可扩展**:`layer_meta` 字段允许各层自由附带不改 schema

### 5.2 Schema(Python / Pydantic v2)

```python
# pce_core/capture_event.py (新建)
from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import datetime

CaptureSource = Literal[
    "L0_kernel",
    "L1_mitm",
    "L2_frida",
    "L3a_browser_ext", "L3b_electron_preload", "L3c_vscode_ext",
    "L3d_cdp", "L3e_litellm", "L3f_otel",
    "L4a_clipboard", "L4b_accessibility", "L4c_ocr",
]

QualityTier = Literal["T1_structured", "T2_ui_text", "T3_metadata_only"]


class CaptureEventV2(BaseModel):
    # ── 身份(必填)───────────────────────────────
    capture_id: str = Field(..., description="全局唯一 ULID")
    pair_id: Optional[str] = Field(None, description="req+resp 关联 ID,新会话自动生成")
    session_hint: Optional[str] = Field(None, description="来源给的会话提示,Normalizer 决定如何归档")

    # ── 来源 ─────────────────────────────────────
    source: CaptureSource
    agent_name: str               # "pce_proxy", "pce_agent_electron", "pce_browser_ext", ...
    agent_version: str            # "1.0.0"
    capture_time_ns: int          # 纳秒时间戳(高精度去重)
    capture_host: str             # 产生此事件的机器 hostname / 进程标识

    # ── AI 语义(Normalizer 主要消费)─────────────
    provider: Optional[str] = None         # "openai", "anthropic", "google", ...
    model: Optional[str] = None            # "gpt-4o-2024-11", "claude-sonnet-4.5", ...
    endpoint: Optional[str] = None         # 完整 URL 或端点路径
    direction: Literal["request", "response", "pair"] = "pair"
    streaming: bool = False

    # ── 载荷 ─────────────────────────────────────
    request_headers: Optional[dict] = None  # 已调用 redact_headers_json
    request_body: Optional[dict] = None     # JSON 对象或序列化文本
    response_headers: Optional[dict] = None
    response_body: Optional[dict] = None    # SSE 已聚合为完整对象
    stream_chunks: Optional[list[dict]] = None  # 可选,保留原始 chunk 序列

    # ── 质量与去重 ───────────────────────────────
    quality_tier: QualityTier = "T1_structured"
    fingerprint: Optional[str] = None       # sha256(pair_id + body_prefix)
    deduped_by: Optional[str] = None        # 若是重复,指向 primary capture_id

    # ── 层自由扩展 ───────────────────────────────
    layer_meta: dict = Field(default_factory=dict)  # 各层附带,不改 schema

    # ── 形态归因(Supervisor 填)──────────────────
    form_id: Optional[str] = None           # "F1".."F10"
    app_name: Optional[str] = None          # "cursor", ...
```

### 5.3 每层填写规约

| 字段 | L0 | L1 | L2 | L3a | L3b | L3c | L3d | L3e | L3f | L4a | L4b | L4c |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| provider / model | 🟡* | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| endpoint | ✅ | ✅ | ✅ | ✅ | ✅ | 🟡 | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| request_body (struct) | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| stream_chunks | ❌ | ✅ | ✅ | 🟡 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| quality_tier | T3 | T1 | T1 | T1 | T1 | T1 | T1 | T1 | T1 | T2 | T2 | T2 |

*🟡 = 依赖 L1 的下游解析*

### 5.4 Ingest API

```
POST /api/v1/captures/v2
  Content-Type: application/json
  body: CaptureEventV2

Response:
  201 Created  {capture_id, ingested_at, normalized: bool}
  409 Conflict {capture_id, existing: capture_id}  # 去重命中
  422 Bad Request {errors: [...]}
```

向后兼容:`/api/v1/captures`(v1)继续工作,gateway 内部自动升级为 v2。

### 5.5 存储 Schema 变更(指向 "存"层)

`raw_captures` 表新增列(migration `0006_capture_event_v2.py`):

```sql
ALTER TABLE raw_captures ADD COLUMN source TEXT;        -- CaptureSource enum
ALTER TABLE raw_captures ADD COLUMN agent_name TEXT;
ALTER TABLE raw_captures ADD COLUMN agent_version TEXT;
ALTER TABLE raw_captures ADD COLUMN capture_time_ns INTEGER;
ALTER TABLE raw_captures ADD COLUMN quality_tier TEXT DEFAULT 'T1_structured';
ALTER TABLE raw_captures ADD COLUMN fingerprint TEXT;
ALTER TABLE raw_captures ADD COLUMN deduped_by TEXT;
ALTER TABLE raw_captures ADD COLUMN form_id TEXT;
ALTER TABLE raw_captures ADD COLUMN app_name TEXT;
ALTER TABLE raw_captures ADD COLUMN layer_meta_json TEXT;
CREATE INDEX idx_rc_fingerprint ON raw_captures(fingerprint);
CREATE INDEX idx_rc_source ON raw_captures(source);
CREATE INDEX idx_rc_app ON raw_captures(app_name);
```

### 5.6 Schema 演进策略

- v2 冻结后,新字段只能加 **optional**,不能改/删
- 字段语义变更 → 必须 v3(新路径 `/v3`,旧路径保留 12 个月)
- `layer_meta.*` 是逃生舱:新层可以先进 meta,稳定后再提升为顶层字段

---

## 6 · 跨切面

### 6.1 隐私 / 脱敏

**强制规则**(所有层必须遵守,由 `CaptureEvent` 入库前的中间件强制):

1. **头级脱敏**:`REDACT_HEADERS`(已有于 `pce_core/config.py`)由中间件统一调用,各层不得绕过
2. **URL token 脱敏**:query string 中的 `api_key` / `token` / `access_token` / `sessionKey` 一律替换为 `<redacted>`
3. **Body PII 感知**(P6 补齐):信用卡号 / 身份证号 / 电话号 / email 的正则 + luhn 校验,命中后替换为 `<redacted:type>`
4. **用户可配**:仪表板提供"隐私严格度"三档(off / standard / paranoid),paranoid 加 OCR/AX 文本也过 PII 过滤
5. **本地永不外传**:除非用户显式配置 OTLP endpoint,数据**不**离开本机

### 6.2 CA 与脚本安全

| 资产 | 管理方 | 限制 |
|---|---|---|
| mitmproxy CA(L1) | `cert_wizard` | 仅 install / uninstall / export / regenerate,永不覆盖第三方根 |
| L2 Frida 脚本签名私钥 | 线下金库 | 每次 release 单独签名,私钥永不入仓库 |
| L0 Windows 驱动签名证书 | EV 证书 | 线下 HSM,与 Microsoft Partner Center 绑定 |
| L0 macOS Developer ID | Apple Developer | notarization 对每个 release 执行 |

### 6.3 性能预算(每机器)

| 层 | CPU | RAM | 延迟 | 说明 |
|---|---|---|---|---|
| Supervisor | ≤ 2% | ≤ 200MB | 决策 P95 ≤ 50ms | 常驻 |
| L0 Kernel redirect | ≤ 1%/pid | ≤ 50MB | 每包 < 1ms | 只开启时 |
| L1 MITM | ≤ 5% | ≤ 400MB | 解密 P95 ≤ 20ms | 常驻 |
| L2 Frida | ≤ 3%/附加进程 | ≤ 100MB/附加 | Hook P95 ≤ 5ms | 按需 |
| L3a 浏览器扩展 | ≤ 1%/标签页 | ≤ 4MB/标签页 | 可忽略 | 常驻 |
| L3b Electron preload | ≤ 0.5%/注入进程 | ≤ 8MB/注入 | 可忽略 | 按需 |
| L3d-f | ≤ 2% | ≤ 200MB | 可忽略 | 已有 |
| L4 总和 | ≤ 1% | ≤ 100MB | 触发式 | 用户触发才跑 |

整机 UCS 全启用时:**CPU ≤ 12%,RAM ≤ 1.2GB**。达不到则触发性能回归 gate,不许发版。

### 6.4 可观测(自己监控自己)

每层强制 emit 3 个指标,通过 `log_event` 写入统一 logger `pce.capture`:

```
pce.capture.rate     counter   # 成功捕获事件数
pce.capture.failure  counter   # 失败事件数(带 reason label)
pce.capture.latency  histogram # 事件从发生到入库的延迟
```

附加事件:

```
pce.supervisor.degrade {from_layer, to_layer, app, reason}
pce.supervisor.upgrade {from_layer, to_layer, app}
pce.capture.pinning_detected {host, app}
pce.capture.dedup_hit {primary_source, secondary_source}
```

仪表板订阅 WebSocket `/ws/capture/metrics`,每 5s 聚合推给前端。

### 6.5 错误分类与用户提示

| 错误码 | 触发条件 | 用户消息(示例) |
|---|---|---|
| `E_CERT_NOT_TRUSTED` | L1 TLS 握手被拒 | "PCE 证书未被某应用信任,点这里一键修复" |
| `E_PINNING_DETECTED` | L1 `tls_failed` 连续 ≥ 3 次 | "该应用做了 pinning,建议开启 L2 高级捕获" |
| `E_PROXY_RESIDUAL` | 上次崩溃残留系统代理 | "检测到代理未清理,点这里还原" |
| `E_ELEVATION_REQUIRED` | 安装 CA / 装驱动需提权 | "PCE 需要管理员权限完成设置" |
| `E_FRIDA_BLOCKED` | AV 拦了 Frida | "Windows Defender 拦了 PCE 高级捕获,点这里查看排除指引" |
| `E_L0_REJECTED` | 内核模块被拒 | "强制捕获需要在系统扩展中授权 PCE" |

每错误码都有 **3 条可操作建议**(硬编码进前端 i18n),绝不抛"未知错误"。

---

## 7 · 代码物料地图

### 7.1 目录增量

```
pce_core/
├── capture_event.py                    [NEW]  CaptureEvent v2 schema
├── capture_supervisor/                 [NEW]
│   ├── __init__.py
│   ├── supervisor.py
│   ├── policy.py
│   ├── policies.yaml
│   ├── discovery.py                    APP 扫描
│   ├── dedup.py
│   └── agents/                         各层适配器
│       ├── kernel.py
│       ├── mitm.py
│       ├── frida.py
│       ├── electron.py
│       ├── vscode.py
│       ├── cdp.py
│       ├── litellm.py
│       ├── otel.py
│       ├── clipboard.py
│       ├── accessibility.py
│       └── ocr.py
├── cert_wizard/                        [已有]
├── proxy_toggle/                       [已有]
├── cdp/                                [已有]
├── sdk_capture_litellm.py              [已有]
├── clipboard_monitor.py                [已有]
├── otel_exporter.py                    [已有]
├── ocr_capture.py                      [NEW,P7]
└── server.py                           [EXT]  加 /v2 + supervisor API

pce_proxy/                              [已有]
pce_browser_extension_wxt/              [已有,扩展站点覆盖]

pce_agent_electron/                     [NEW,P5.B]
├── preload.js
├── launcher.py
├── detector.py
└── signatures/

pce_agent_frida/                        [NEW,P6]
├── bin/{win,mac,linux}/frida-server
├── scripts/ssl_log.js
├── scripts/ssl_log.js.sig
├── reporter.js
├── public.pem
└── agent.py

pce_agent_kernel/                       [NEW,P7]
├── windows/
├── macos/
└── linux/

pce_agent_ax/                           [NEW,P7]
├── macos/
├── windows/
└── common.py

pce_ide_vscode/                         [NEW,P5.B]
├── package.json
├── src/
└── README.md

pce_ide_jetbrains/                      [NEW,P5.B 后期]
```

### 7.2 代码复用清单

| 已有资产 | UCS 复用方式 |
|---|---|
| `pce_proxy/addon.py` | L1 主实现,加 tls_failed hook |
| `pce_core/cert_wizard/` | L1 依赖,零改动 |
| `pce_core/proxy_toggle/` | L1 依赖,加 per-app bypass UI |
| `pce_core/electron_proxy.py` | L1+L3b 共用 launcher(只新增 KNOWN_APPS) |
| `pce_browser_extension_wxt/` | L3a 主实现,加 SaaS 站点提取器 |
| `pce_core/cdp/driver.py` | L3d 原封使用 |
| `pce_core/sdk_capture_litellm.py` | L3e 打包成独立二进制 |
| `pce_core/otel_exporter.py` | L3f 出文档 |
| `pce_core/clipboard_monitor.py` | L4a 加 AI 特征识别 |
| `pce_core/supervisor.py`(P2) | 作为 Capture Supervisor 的进程监管子集 |
| `pce_core/server.py` | 加 `/v2` 端点和 supervisor API |
| `pce_core/logging_config.py` | 统一 `log_event` 所有新代码必用 |

---

## 8 · 分发和打包

### 8.1 发布产物矩阵

| 产物 | 包含层 | 平台 | 大小估 | 渠道 |
|---|---|---|---|---|
| **PCE Desktop**(主安装包) | Supervisor + L1 + L3b/L3d/L3e/L3f + L4a + 仪表板 | Win/macOS/Linux | ~120MB | 官网 + GitHub Releases |
| **PCE Browser Extension** | L3a | Chrome / Firefox / Edge | ~2MB | Chrome Web Store / Firefox AMO(ADR-003 双模式) |
| **PCE VS Code Extension** | L3c | VS Code | ~1MB | VS Code Marketplace |
| **PCE Frida Agent Pack** | L2 + Frida binaries | Win/macOS/Linux | ~60MB | 主安装包**可选组件**(首次启用时下载) |
| **PCE Kernel Agent Pack** | L0 | Win/macOS/Linux | ~20MB | 主安装包**可选组件**(首次启用时下载) |
| **PCE AX Agent Pack** | L4b | macOS / Windows | ~10MB | 主安装包**可选组件** |
| **pce-gateway**(CLI) | L3e | all | ~30MB | 独立 Python wheel / 二进制 |

### 8.2 更新通道

```
stable ──→ 仅 P 阶段结束、全部 NFR + 验收通过后发
beta   ──→ 每 2 周,尝鲜用户
canary ──→ 每 commit(CI),内部 / 测试机
```

签名策略:`stable` 和 `beta` 全平台签名;`canary` Linux 无签名(开发者自建)。

### 8.3 升级策略

- 主包:应用内自检(每启动 + 每天),自动下载 delta patch
- 浏览器扩展:Chrome Web Store 自动,sideload 模式用户手动
- VS Code 扩展:Marketplace 自动
- 可选组件(Frida / Kernel / AX):独立升级通道,Supervisor 控制兼容性矩阵

### 8.4 卸载

`pce uninstall` 必须:
1. 调 `cert_wizard.uninstall_ca()`
2. 调 `proxy_toggle.disable_system_proxy()`
3. 调 L0/L2 各自 `detach / disable`
4. 清 `~/.pce/data/`(可选,提示用户)
5. 删 Service / LaunchDaemon / systemd unit

卸载**绝不**能留下:污染的系统代理 / 未回收的 CA / 残留驱动。

---

## 9 · 分阶段落地

每个阶段都是**可发布的产品切片**,不是内部里程碑。按 0.3 节的渐进发布策略切。

### 9.1 P5.A — v1.0 "Subscription Capture"(预计 3-4 周)

**目标形态**:F1 全面 + F2 主力 + F4 + F6 = **~55% 使用量**

**交付清单**:

| 编号 | 任务 | 位置 | 预估 |
|---|---|---|---|
| P5.A-1 | Allowlist 补 6 个订阅域 | `pce_core/config.py` | 0.5h |
| P5.A-2 | KNOWN_APPS 补 ChatGPT Desktop + Codex CLI | `pce_core/electron_proxy.py` | 1h |
| P5.A-3 | CaptureEvent v2 schema + `/api/v1/captures/v2` | `pce_core/capture_event.py` + `server.py` | 2d |
| P5.A-4 | 数据库 migration `0006` | `pce_core/migrations/0006_capture_event_v2.py` | 0.5d |
| P5.A-5 | 首跑向导(`/onboarding`):串 cert + proxy + Electron 重启 | `pce_core/server.py` + 前端 | 3d |
| P5.A-6 | Pinning 失败检测 + UI 红点 + 3 条建议 | `pce_proxy/addon.py` + 仪表板 | 2d |
| P5.A-7 | Per-app bypass UI | 仪表板 | 1d |
| P5.A-8 | 浏览器扩展 F2 站点 P1(M365 Copilot + Notion AI) | `pce_browser_extension_wxt/entrypoints/` | 4d |
| P5.A-9 | 浏览器扩展 F2 站点 P2(Figma AI + Gmail) | 同上 | 4d |
| P5.A-10 | 隐私中间件:统一脱敏调用 | `pce_core/server.py::captures_ingest` | 1d |
| P5.A-11 | 发布打包:Win/macOS/Linux 主包 + 扩展商店提交 | `pce.spec` + CI | 3d |

**依赖**:无前置(全部可立即开工)

**验收**(10.1 节)通过 → v1.0 stable 发布

### 9.2 P5.B — v1.1 "IDE & Electron"(预计 4-5 周)

**目标形态**:F5 全面 + F4 Electron hook(≈ 75% 累计)

**交付清单**:

| 编号 | 任务 | 位置 | 预估 |
|---|---|---|---|
| P5.B-1 | Electron preload.js 基础框架 | `pce_agent_electron/preload.js` | 3d |
| P5.B-2 | Electron launcher(NODE_OPTIONS 注入) | `pce_agent_electron/launcher.py` | 2d |
| P5.B-3 | Electron app detector(扫描 Cursor/Windsurf/ChatGPT/Claude Desktop) | `pce_agent_electron/detector.py` | 1d |
| P5.B-4 | Preload.js fetch/XHR/WebSocket hook + reporter | 同上 | 3d |
| P5.B-5 | Preload.js 签名机制 | `pce_agent_electron/signatures/` + agent | 2d |
| P5.B-6 | Cursor gRPC-web 特殊处理 | `pce_agent_electron/preload.js` | 4d(逆向耗时) |
| P5.B-7 | VS Code 扩展骨架 | `pce_ide_vscode/` | 3d |
| P5.B-8 | VS Code Language Model API hook | `pce_ide_vscode/src/` | 3d |
| P5.B-9 | Chat Participant API hook | 同上 | 2d |
| P5.B-10 | 发布:主包加 L3b + VS Code Marketplace 提交 | CI | 2d |

**依赖**:P5.A 完成(CaptureEvent v2 已冻结)

**验收**(10.2 节)通过 → v1.1 stable 发布

### 9.3 P6 — v1.2 "Pinning-Proof"(预计 6-8 周)

**目标形态**:所有 Tier 1 结构化率 ≥ 95%(当前 ~80%)

**交付清单**:

| 编号 | 任务 | 位置 | 预估 |
|---|---|---|---|
| P6-1 | Frida SSL hook PoC(Electron BoringSSL) | `pce_agent_frida/scripts/ssl_log.js` | 1w |
| P6-2 | Frida agent.py(attach/detach + health) | `pce_agent_frida/agent.py` | 1w |
| P6-3 | Frida 脚本签名体系 | `pce_agent_frida/public.pem` + CI 签名流水线 | 3d |
| P6-4 | Windows SChannel(EasyHook/Detours 备用路径) | `pce_agent_frida/windows/` | 1w |
| P6-5 | Linux eBPF uprobe 实现 | `pce_agent_frida/linux/ebpf/` | 1w |
| P6-6 | AV 兼容测试(Defender / Kaspersky / 360) | `tests/e2e/av_compat/` | 1w |
| P6-7 | Supervisor:L2 adapter + 与 L1 联动降级 | `pce_core/capture_supervisor/agents/frida.py` | 1w |
| P6-8 | PII 感知脱敏(Body 级) | `pce_core/redact.py`(新) | 4d |
| P6-9 | 发布:主包加 L2 作为可选下载组件 | CI | 3d |

**依赖**:P5.B 完成(Supervisor 已就位)

**验收**(10.3 节)通过 → v1.2 stable 发布

### 9.4 P7 — v1.3 "Force Capture + Fallback"(预计 8-10 周)

**目标形态**:Tier 2 完全落地 + 边缘 Tier 1 兜底

**交付清单**:

| 编号 | 任务 | 位置 | 预估 |
|---|---|---|---|
| P7-1 | Windows WinDivert 用户态实现 | `pce_agent_kernel/windows/` | 2w |
| P7-2 | Windows WFP 驱动(企业版) | 同上 | 3w |
| P7-3 | macOS NetworkExtension 实现 | `pce_agent_kernel/macos/` | 3w |
| P7-4 | Linux nftables + eBPF 脚本 | `pce_agent_kernel/linux/` | 1w |
| P7-5 | 驱动 / System Ext 签名和 notarization | CI + 金库 | 1w |
| P7-6 | macOS AXUIElement 桥 | `pce_agent_ax/macos/` | 1w |
| P7-7 | Windows IUIAutomation 桥 | `pce_agent_ax/windows/` | 1w |
| P7-8 | 触发式 OCR | `pce_core/ocr_capture.py` | 4d |
| P7-9 | JetBrains 扩展(Copilot 覆盖) | `pce_ide_jetbrains/` | 2w |
| P7-10 | 发布:主包加 L0 + L4b + L4c 作为可选组件 | CI | 3d |

**依赖**:P6 完成

**验收**(10.4 节)通过 → v1.3 stable 发布

### 9.5 P8 — v2.0 "Full Supervisor"(预计 4-6 周)

**目标形态**:完整自动化调度、用户零介入

**交付清单**:

| 编号 | 任务 | 位置 | 预估 |
|---|---|---|---|
| P8-1 | Supervisor 核心:discovery + policy + health | `pce_core/capture_supervisor/` | 2w |
| P8-2 | 自动降级决策引擎 | 同上 `dedup.py` + `supervisor.py` | 1w |
| P8-3 | 多层去重算法 | `pce_core/capture_supervisor/dedup.py` | 1w |
| P8-4 | Supervisor API + WS | `pce_core/server.py` | 1w |
| P8-5 | 仪表板"每应用健康卡"UI | 前端 | 2w |
| P8-6 | 策略热加载 + 用户手动覆盖 | `policies.yaml` | 3d |
| P8-7 | 全量 e2e:模拟层失效 + 降级回归 | `tests/e2e/supervisor/` | 1w |

**依赖**:P7 完成(所有层就绪)

**验收**(10.5 节)通过 → v2.0 stable 发布 → UCS 首期完成

---

## 10 · 验收标准

每阶段的硬门槛。达不到则阻止发版,不允许"差不多就发"。

### 10.1 P5.A(v1.0)验收

- ✅ ChatGPT 网页(L3a)单条捕获成功率 ≥ 98%
- ✅ ChatGPT Desktop(L1)单条捕获成功率 ≥ 95%
- ✅ Copilot VS Code(L1)单条捕获成功率 ≥ 95%
- ✅ Codex CLI(L1)单条捕获成功率 ≥ 95%
- ✅ 新用户"装 → 拿到第一条"时长 ≤ 5 分钟(100 次测试 P95)
- ✅ 首跑向导失败率 < 1%
- ✅ 浏览器扩展 M365/Notion AI 文本捕获成功率 ≥ 90%
- ✅ 所有现有 724 pytest + ~220 vitest 继续 pass
- ✅ 新增 ≥ 30 个单元测试覆盖 CaptureEvent v2 + 中间件
- ✅ Pinning 失败时仪表板显示红点 + 3 条建议
- ✅ 性能:整机 CPU ≤ 6%, RAM ≤ 600MB(仅 L1 + L3a + Supervisor)

### 10.2 P5.B(v1.1)验收

- ✅ Cursor `/StreamChat` 捕获成功率 ≥ 95%,body JSON 解码率 ≥ 90%
- ✅ VS Code Copilot Chat 每条消息 1s 内入库
- ✅ Claude Desktop 每条消息 ≥ 95% 被 L3b 捕获
- ✅ ChatGPT Desktop 无 pinning 版本 ≥ 95% 被 L1 + L3b 双捕获
- ✅ Preload.js 签名篡改后必须拒绝加载(2 个 case 测试)
- ✅ VS Code 扩展在 VS Code 1.88+ 各版本可用
- ✅ 性能:整机 CPU ≤ 8%, RAM ≤ 800MB

### 10.3 P6(v1.2)验收

- ✅ ChatGPT Desktop pinning 失败率降到 < 1%
- ✅ Frida 脚本通过 Defender / Kaspersky / 360 免误报
- ✅ Body PII 脱敏误杀率 < 5%(50 个 golden case)
- ✅ Frida 附加崩溃率 < 0.1%
- ✅ 性能:整机 CPU ≤ 10%, RAM ≤ 1GB

### 10.4 P7(v1.3)验收

- ✅ L0 Kernel:对故意不尊重代理的进程,强制导流成功率 ≥ 95%
- ✅ L0 误导流率 < 0.01%(黑名单测试)
- ✅ Windows 驱动通过 Microsoft WHQL 认证
- ✅ macOS System Extension 通过 notarization
- ✅ Accessibility 桥:macOS AX 读 ChatGPT Desktop UI 成功率 ≥ 80%
- ✅ OCR 触发式延迟 < 2s
- ✅ JetBrains 扩展覆盖 Copilot

### 10.5 P8(v2.0)验收

- ✅ 关 mitmproxy 模拟失败,30s 内切到 L2 / L4,捕获不中断
- ✅ 全量 e2e:10 种形态每种 ≥ 1 条端到端测试通过
- ✅ Supervisor 崩溃时,各层独立工作 ≥ 1h 不错乱
- ✅ 仪表板健康卡与后端状态一致性 P95 ≤ 1s
- ✅ 策略热加载:修改 policies.yaml 后 5s 内生效
- ✅ 多层去重:同一对话不出现在多条 messages 记录(双留但聚合展示)
- ✅ 完整 UCS CPU ≤ 12%, RAM ≤ 1.2GB(NFR-1~3 全达标)
- ✅ **10 形态覆盖率**:T1 ≥ 75%,T2 ≥ 90% UI 级,T3 明确标记"不支持"

---

## 11 · 风险寄存簿

按"可能性 × 影响"排序。每条都要有**触发条件**和**缓解方案**。

| # | 风险 | 可能 | 影响 | 触发条件 | 缓解 |
|---|---|---|---|---|---|
| R-01 | ChatGPT Desktop 升级增加 pinning 强度 | 高 | 高 | 下次 OpenAI 桌面客户端更新 | L2 Frida 必须在 P5.B 前完成 PoC;v1.1 前完整可用 |
| R-02 | Windows Defender / 国产 AV 把 Frida 当木马 | 高 | 高 | Release 后 7 天内必有用户反馈 | 代码签名 + Microsoft Partner Center 提交 + 与 3 家 AV 厂商白名单沟通 |
| R-03 | Chrome Web Store 审核拒绝 `<all_urls>` | 中 | 高 | 首次提交商店版 | 双模式(ADR-003)已经设计,webstore 模式可用 |
| R-04 | VS Code Language Model API 行为变化 | 中 | 中 | 每 2 个 VS Code 版本 | 扩展 CI 每周跑 Insiders 版本回归 |
| R-05 | Cursor bundle 混淆加强使 gRPC hook 失效 | 中 | 中 | Cursor 每次大版本更新 | L1 MITM + Frida 双保险;即使 L3b 挂了 L1 仍能拿元数据 |
| R-06 | macOS System Extension 审批链变化 | 低 | 高 | Apple 新系统要求 | 与 Apple Developer 联系人保持沟通,P7 前确认 |
| R-07 | mitmproxy 性能瓶颈(大文件/长会话) | 低 | 中 | 用户传大上下文 | 已有 body 大小限制(`PCE_PROXY_BODY_MAX`),P5.A 验证 |
| R-08 | 数据库膨胀(百万级 raw_captures) | 中 | 中 | 日活用户使用 90 天后 | 已有 RETENTION 策略(P1 TASK-003),仪表板加"使用量告警" |
| R-09 | CaptureEvent v2 字段设计缺陷导致后期返工 | 低 | 高 | P5.B 之后发现 | §5.6 Schema 演进策略预留 `layer_meta` 逃生舱;P5.A 关门前 review |
| R-10 | L0 驱动导致蓝屏 / kernel panic | 低 | 极高 | 用户启用 L0 后 | 驱动签名 + WHQL 测试 + 出厂默认关闭 + 灰度 |
| R-11 | 用户拒绝装 CA,L1 完全失败 | 中 | 中 | 首次使用场景 | 向导"跳过装 CA"后降级到 L3 层(浏览器扩展仍能工作) |
| R-12 | 浏览器扩展被 SaaS 宿主检测并屏蔽 | 中 | 中 | 部分站点有反扩展逻辑 | 扩展尽量 passive 读 DOM,不改页面;被屏蔽时降级到 L4a 剪贴板提示用户 |
| R-13 | Frida 签名私钥泄露 | 低 | 极高 | 开发者设备被攻破 | 线下硬件金库;每 6 个月轮换;泄露后 kill-switch(远程 revoke) |
| R-14 | 订阅 API 出现 bot 风控(Cloudflare Turnstile 等) | 中 | 低 | 对 ChatGPT/Claude 越来越常见 | 只记录、不改写(ADR-001),绝不触发二次请求,不会引起风控 |
| R-15 | 用户企业环境禁止装驱动 / System Ext | 高 | 低 | 企业客户 | L0 和 L4b 都是**可选组件**,禁用后 L1+L3 仍覆盖 75%+ 形态 |
| R-16 | 10 形态覆盖率被新形态打破 | 高 | 低 | 每 6 个月会出现新形态 | policy.yaml 热加载 + `layer_meta` 字段兜底;每季度回顾形态清单 |

---

## 12 · 开放决策点

需要人拍板,不是工程师能独立决定的。

### 12.1 发布路径(strong need)

**D-1 · 首个 stable 版本的最小集** — ✅ **Decided (2026-04-18): Option A — 小步快发**
- 选项 A:只发 L1 + L3a(最保守,1.0 = P5.A)← **采纳**
- 选项 B:等 L3b 完成再发(稍晚,1.0 = P5.A + P5.B)
- 4 周后发 v1.0,每切片都能上市,市场反馈驱动后续。详见 `docs/decisions/2026-04-18-ucs-and-release-strategy.md`。

**D-2 · 开源 vs 商业边界** — ✅ **Decided (2026-04-18): Option B — Open Core**
- 选项 A:全开源
- 选项 B:Core 开源(L1/L3a/L3d/L3e/L3f/L4a/L4c + CaptureEvent 契约 + ingest gateway),Supervisor + L0 + L2 + L3b + L4b 闭源商业 ← **采纳**
- 选项 C:全闭源
- 完整边界定义见 ADR-010。License:OSS = Apache-2.0,Pro = 专有。仓库拆分:`github.com/zstnbb/pce`(公开) + `github.com/zstnbb/pce-pro`(私有)

**D-3 · 目标用户优先级** — ✅ **Decided (2026-04-18): Option A — 开发者先行**
- 选项 A:开发者先行(Cursor/Copilot/Codex 用户)→ 产品反馈直接,技术门槛匹配 ← **采纳**
- 选项 B:AI 重度用户先行(ChatGPT Plus 用户)→ 市场最大,但 v1.x 安装门槛不匹配大众用户
- PCE v1.x 需要装 CA + 代理 + 扩展,只适合开发者种子用户;Pro 变现点(L0+L2 反 pinning)正好是开发者痛点。详见决议记录。

### 12.2 工程路径(medium need)

**D-4 · L2 Frida 脚本分发模式**
- 选项 A:随主包静态打包(+60MB)
- 选项 B:首次启用 L2 时按需下载(主包 -60MB)
- **推荐**:B。绝大多数用户只用 L1,减轻主包负担

**D-5 · 移动端投资**
- 选项 A:完全放弃(F7 Tier 3)
- 选项 B:只做 PAC + CA 配置向导(有限兜底,不触碰 pinning)
- **推荐**:B。`mobile_wizard` 已有基础,零额外投资

**D-6 · OTel 二级通道默认行为**
- 选项 A:默认关(当前 ADR-007)
- 选项 B:默认开,无 endpoint 时跳过
- **推荐**:A。隐私第一,用户主动开启

### 12.3 合规 / 法务(low urgency but must-do before v1.0)

**D-7 · CA 安装合规声明**
- 需要法务起草"用户授权安装根证书"条款
- 需要在首跑向导明确展示并要求勾选

**D-8 · L2 Frida 的法律性质**
- Frida 注入应用进程是否符合目标应用的 ToS?
- 需要法务针对 5 大订阅应用的 ToS 逐条审视

**D-9 · 数据所有权**
- 用户捕获的 AI 对话,若 AI 提供方 ToS 限制第三方工具记录,PCE 用户的风险?
- 需要在 README 和首跑向导中明确免责

---

## 附录 A · 10 形态图谱

按"用户坐在哪 × LLM 在哪算"两个坐标收敛的**规范形态**。新产品涌现时,先归类到最接近的形态,若真的都不匹配再考虑扩展清单。

| # | 形态名 | 用户位置 | LLM 位置 | 代表产品(2025-2026) | 传输 | 占比估 | Tier |
|---|---|---|---|---|---|---|---|
| **F1** | Web Chat UI | Browser | 公有 API / 厂商中继 | ChatGPT web, Claude.ai, Gemini, Perplexity, Kimi, Poe, DeepSeek Chat, Z.ai, 豆包, Mistral Le Chat, Grok | HTTPS+SSE | ~40% | **T1** |
| **F2** | Web-Embedded AI | Browser(SaaS 宿主) | SaaS 服务端 | Notion AI, Microsoft 365 Copilot (web), Figma AI, Jira AI, Linear AI, Grammarly, Intercom AI, Duolingo Max, Replit Ghostwriter | HTTPS(SaaS 自域) | ~7% | **T2** |
| **F3** | Browser Extension AI | Browser | 混合 | Monica, MaxAI, Merlin, Glasp, Wiseone, Sider, ChatGPT-for-Google, WebChatGPT | HTTPS+SSE | ~2% | **T1** |
| **F4** | Desktop Chat App | Desktop(Electron/Tauri) | 公有 API | ChatGPT Desktop, Claude Desktop, Poe Desktop, DeepSeek Desktop, Kimi Desktop, 豆包 Desktop | HTTPS+SSE | ~8% | **T1 / T2**(含 pinning) |
| **F5** | IDE AI Assistant | IDE(插件或 fork IDE) | 厂商中继 / 公有 API | Copilot (VS Code/JetBrains/Neovim), Cursor, Windsurf, Cline, Continue, Roo Cline, Aider IDE, Codeium, Tabnine, Cody, Amazon Q | HTTPS+gRPC | ~15% | **T1** |
| **F6** | CLI AI Agent | Terminal | 公有 API / 厂商中继 | Codex CLI, Aider CLI, Claude Code, OpenCode, GPT-Engineer, Warp AI, Fig, sgpt | HTTPS+SSE | ~1% | **T1** |
| **F7** | Mobile AI App | Mobile | 公有 API / 端侧 NPU | ChatGPT iOS/Android, Claude mobile, Gemini, Character.AI, DeepSeek, Kimi, 豆包 | HTTPS(pinned) | ~25% | **T3 不投资** |
| **F8** | OS-Integrated AI | OS 整机 | 端侧 NPU + 公有 API | Apple Intelligence, Windows Copilot, Siri, Google Assistant, Samsung Galaxy AI | 私有 IPC + HTTPS | <1% | **T3 不投资** |
| **F9** | Local / Self-Hosted Model | Desktop | localhost | Ollama, LM Studio, llama.cpp, LocalAI, jan.ai, GPT4All, vLLM, Text Generation WebUI, ComfyUI | HTTP 明文 loopback | ~1% | **T1** |
| **F10** | Headless Agent / Framework | Server(无人在环) | 公有/私有/本地 | LangChain / LlamaIndex / CrewAI / AutoGPT 部署、n8n AI、dify.ai、Flowise、Zapier AI | HTTPS | ~2% | **T1** |

**备注**:
- 媒体生成(DALL-E / Midjourney / Runway / Pika / Kling / Suno / ElevenLabs)在传输层上与 F1/F4/F10 同构,作为子类处理;Midjourney 通过 Discord 走 F1(网页)或 F10(Bot)
- 占比为 **AI 交互次数**估算(基于各家 DAU/MAU + 平均对话频次),非绝对数
- Tier 分级见 1.1 FR-1

---

## 附录 B · 捕获层 × 形态路由矩阵

横轴 10 形态,纵轴捕获层。✅ = 首选,🟢 = 兜底,❌ = 不适用。

| 层 \ 形态 | F1 | F2 | F3 | F4 | F5 | F6 | F7 | F8 | F9 | F10 |
|---|---|---|---|---|---|---|---|---|---|---|
| L0 Kernel | 🟢 | ❌ | 🟢 | 🟢 | 🟢 | 🟢 | ❌ | ❌ | 🟢 | 🟢 |
| L1 MITM | 🟢 | ❌ | ✅ | ✅ | 🟢 | ✅ | ❌ | ❌ | ✅ | 🟢 |
| L2 Frida | ❌ | ❌ | 🟢 | 🟢 pin | 🟢 | 🟢 | ❌ | ❌ | ❌ | 🟢 |
| L3a 浏览器扩展 | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | 🟢 | ❌ |
| L3b Electron preload | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | 🟢 | ❌ |
| L3c IDE 扩展 | ❌ | ❌ | ❌ | ❌ | ✅ Copilot | ❌ | ❌ | ❌ | ❌ | ❌ |
| L3d CDP | 🟢 alt | 🟢 | 🟢 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| L3e LiteLLM | ❌ | ❌ | ❌ | ❌ | 🟢 BYOK | ✅ BYOK | ❌ | ❌ | ✅ | ✅ |
| L3f OTel | ❌ | ❌ | ❌ | ❌ | ❌ | 🟢 | ❌ | ❌ | 🟢 | ✅ |
| L4a Clipboard | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | ❌ | ❌ | 🟢 | ❌ | ❌ |
| L4b Accessibility | ❌ | 🟢 | ❌ | 🟢 | ❌ | ❌ | ❌ | 🟢 | ❌ | ❌ |
| L4c OCR | 🟢 | 🟢 | 🟢 | 🟢 | ❌ | ❌ | ❌ | 🟢 | ❌ | ❌ |

**覆盖密度直觉**:

- F1/F3/F5/F6/F9/F10 — 至少有 2 个 ✅ 或 1 个 ✅ + 2 个 🟢,非常稳
- F2/F4 — 各有 1 个 ✅ 为主,多个 🟢 兜底
- F7/F8 — 几乎全 ❌,物理不可及(T3)

---

## 附录 C · 术语表

| 术语 | 英文 / 缩写 | 含义 |
|---|---|---|
| UCS | Universal Capture Stack | 本文档定义的完整捕获架构 |
| 形态 | Form | AI 产品按"用户位置 × LLM 位置"收敛出的 10 种类型 |
| 层 | Layer (L0~L4) | 捕获技术栈的 5 个层级 |
| Tier | Tier (T1~T3) | 形态的可捕获等级:T1 结构化 / T2 UI 级 / T3 不可捕获 |
| CaptureEvent | CaptureEvent v2 | 统一的事件 envelope,所有层向 ingest gateway 递交的唯一格式 |
| pair_id | — | 一次 req+resp 对的关联 ID |
| fingerprint | — | `sha256(pair_id + body_prefix)`,去重主键 |
| Supervisor | Capture Supervisor | 调度、发现、健康、降级、去重的中枢组件 |
| Policy | CapturePolicy | 某应用使用哪层、降级到哪层的配置 |
| Pinning | Certificate Pinning | 应用只信任自己硬编码的证书,不信任系统根;L1 的主要死角 |
| Frida | — | 动态二进制插桩框架,PCE 用其实现 L2 |
| Preload | Electron Preload | Electron 应用加载页面前执行的脚本,PCE 用其实现 L3b |
| CDP | Chrome DevTools Protocol | Chromium 调试协议,PCE 已用其实现 L3d |
| WFP | Windows Filtering Platform | Windows 内核包过滤框架,L0 Windows 实现 |
| WinDivert | — | Windows 用户态包拦截库,L0 Windows 备用实现 |
| NetworkExtension | NE / NEFilter | macOS 系统级包过滤 API,L0 macOS 实现 |
| eBPF | Extended BPF | Linux 内核虚拟机,L0 Linux + L2 Linux 实现 |
| AX / UIA | Accessibility / UI Automation | macOS / Windows 的辅助功能 API,L4b 实现 |
| 抓-存-渲染 | Capture / Store / Render | PCE 产品的三层心智模型 |

---

## 结尾

本文档是 UCS 工业化落地的 **蓝本**,不是圣经。新形态、新客户端、新合规要求出现时都必须回来修订。

**版本历史** - v1 (2026-04-18):首版,P4 落幕当日起草

**下一步**:

1. 人工 review → ADR-009 决策记录
2. P5.A 启动 → 3 周后 review 第一轮 KPI
3. 每 P 阶段关门时在此文档留一条 "postmortem" 链接,记录偏差原因

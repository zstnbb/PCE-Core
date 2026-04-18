# ADR-010: Open Core 模块边界 与 License 策略

- Status: Accepted
- Date: 2026-04-18
- Related: ADR-009 (Universal Capture Stack), ADR-001 (record-not-intervention), ADR-002 (local-proxy-as-core), ADR-003 (dual-install-modes)
- Resolves: UCS 设计文档 §12 D-2

## Context

ADR-009 采纳 UCS(Universal Capture Stack)五层架构后,必须回答:**哪些模块开源,哪些闭源?**

此问题直接影响:
- 仓库物理结构(1 个 vs 2 个)
- 许可证选择和商业变现路径
- CaptureEvent v2 作为"抓-存"桥梁的公开性
- 新模块该写在哪里(每一次 MR 都会问)
- CI/CD 和 release pipeline 的组织方式

晚决定比早决定昂贵数倍:现在代码量还小,边界划清楚半天的工作;等 P6 完成后(6 个月后)再划会是 2 周的混乱 + 大量 import 重构。

## Decision

采纳 **Open Core** 模型:核心捕获与存储能力开源吸引信任和社区,高级自动化与反 pinning 能力闭源作为付费版差异化。

### 模块边界

**OSS Edition(Apache-2.0)**——`github.com/zstnbb/pce`(当前仓库)

| 模块 | 职责 | 理由 |
|---|---|---|
| `pce_core/`(大部分) | 归一化、存储、FTS、向量化、DB migrations | 信任根基,必须可审计 |
| `pce_core/capture_event.py` | CaptureEvent v2 schema | **必须开源** — 它是 Pro 的公开 API 契约 |
| `pce_core/server.py` | Ingest Gateway + 仪表板基础版 | 同上 |
| `pce_proxy/` | L1 mitmproxy addon | 最基础能力,开源最能建立信任 |
| `pce_core/cert_wizard/` | CA 向导 | L1 依赖 |
| `pce_core/proxy_toggle/` | 系统代理切换 | L1 依赖 |
| `pce_core/electron_proxy.py` | L1 Electron launcher | L1 依赖 |
| `pce_core/mobile_wizard.py` | 移动端 PAC/CA 向导 | L1 依赖 |
| `pce_browser_extension_wxt/` | L3a 浏览器扩展(13+ 站点) | Chrome Web Store 对开源扩展审核更友好(ADR-003) |
| `pce_core/cdp/` | L3d Chromium DevTools | 简单协议接入,无差异化价值 |
| `pce_core/sdk_capture_litellm.py` | L3e LiteLLM gateway | SDK 工具,开源更易被生态采用 |
| `pce_core/otel_exporter.py` | L3f OTel 二级通道 | 标准协议,闭源无意义 |
| `pce_core/clipboard_monitor.py` | L4a 剪贴板 | 技术简单,闭源无价值 |
| `pce_core/ocr_capture.py` | L4c 触发式 OCR | 同上 |
| `pce_ide_vscode/` | L3c VS Code 扩展(基础) | VS Code Marketplace 政策友好,开源更易过审 |
| `pce_app/` | 桌面 shell(基础版) | 基础体验 |
| `tests/`(现有) + 基础 e2e | 质量保障 | 信任根基 |

**Pro Edition(Proprietary)**——`github.com/zstnbb/pce-pro`(新建私有仓库)

| 模块 | 职责 | 理由 |
|---|---|---|
| `pce_agent_kernel/` | L0 内核驱动(Win WFP / macOS NE / Linux eBPF) | 需要 EV 证书 + Apple Developer ID + 企业级签名保护 |
| `pce_agent_frida/` | L2 Frida SSL hook + 签名脚本 | 签名密钥必须闭源;AV 兼容性是长期对抗性工作 |
| `pce_agent_electron/` | L3b Electron preload 注入 | 签名保护(防止第三方替换劫持);Cursor gRPC-web 逆向是高价值差异化 |
| `pce_agent_ax/` | L4b macOS AX / Windows UIA / Linux AT-SPI 桥 | 跨平台原生代码,持续维护成本高,作为 Pro 差异化 |
| `pce_core/capture_supervisor/` | 自动调度、健康探针、动态降级、多层去重 | 自动化是 Pro 的核心卖点(OSS 版用户手动启停各层) |
| `pce_ide_vscode_pro/` | VS Code 扩展高级功能(搜索/回放/分享) | 增值功能,基础 hook 已在 OSS 版 |
| `pce_ide_jetbrains/` | JetBrains 扩展 | 企业级 IDE 用户愿意付费 |
| `pce_dashboard_pro/` | 仪表板高级版:搜索 / 时间线 / 回放 / 分享 / 导出 / 洞察 | 渲染层高级功能(下一份设计文档覆盖) |

### 依赖方向(关键不变式)

```
  pce-pro  ─────import──────▶  pce
     ▲                          │
     │                          │
     └──┬───── 不能反向依赖 ─────┘
        │
        │  违反此约束 = CI 自动拒绝
```

**规则**:
- Pro 可以 `import pce_core.capture_event` / `from pce_core.server import ingest_client`
- OSS **永远**不能 `import pce_agent_*` 或 `from pce_core.capture_supervisor`
- 运行期通信:Pro 模块通过本地 HTTP `POST /api/v1/captures/v2` 往 OSS ingest gateway 递事件(不是进程内调用)

### CaptureEvent v2 作为 Open Core 的唯一公共契约

这是 Open Core 模型的核心设计:

- **契约定义开源**(`pce_core/capture_event.py`)
- **Ingest gateway 开源**(`pce_core/server.py::/api/v1/captures/v2`)
- **所有 Pro 层都只通过这个契约与 OSS 交互**
- 第三方开发者可以**自己写**符合 CaptureEvent v2 的捕获层(等效于自己做一个 Pro 替代品)— 这是开源该给的自由

这保证了:
1. OSS 版永远是功能闭环的完整产品(只是没自动化/没反 pinning)
2. 第三方贡献的新捕获层可以进 OSS 生态
3. Pro 版的任何模块都能被独立替换(不存在隐秘耦合)

### License 选择

**OSS Edition — Apache-2.0**

理由:
1. **专利 grant** 保护 PCE 和贡献者免受专利诉讼
2. **允许闭源商业化** — Pro 版可以基于 OSS 扩展且不用开放 Pro 代码
3. **主流 marketplace 友好** — Chrome Web Store / VS Code Marketplace / GitHub 均无摩擦
4. **社区 contributor 接受度高** — 不像 GPL 那样"传染",公司 contributor 愿意合作

**不选 AGPLv3** 的原因:虽然能防止云厂商白嫖(PostHog / Grafana 曾因此转换),但 PCE 本身 local-first(ADR-002),云厂商复制价值不大;而且 AGPL 对商业贡献者更劝退。

**Pro Edition — 专有许可证**

模板:参考 Cursor / Sentry Business Source License 2.0 或纯 EULA。许可证条款:
- 授权个人/企业终端使用,按订阅付费
- 禁止反向工程、重新分发、二次开发
- 保留 3 年后降级为 OSS 的选项(BSL 1.1 模式,可选)

### 仓库结构

**现状**:`github.com/zstnbb/PCE-Core`(当前工作区)
**目标**:

```
github.com/zstnbb/pce                  (公开,Apache-2.0)
├── pce_core/         ← 去掉 capture_supervisor/
├── pce_proxy/
├── pce_browser_extension_wxt/
├── pce_ide_vscode/
├── pce_app/
├── tests/
├── Docs/
├── LICENSE(Apache-2.0)
└── CONTRIBUTING.md

github.com/zstnbb/pce-pro              (私有,专有许可)
├── pce_agent_kernel/
├── pce_agent_frida/
├── pce_agent_electron/
├── pce_agent_ax/
├── pce_core/
│   └── capture_supervisor/
├── pce_ide_vscode_pro/
├── pce_ide_jetbrains/
├── pce_dashboard_pro/
├── tests_pro/
├── LICENSE(proprietary)
└── requirements.txt  ← pce>=1.0.0
```

### 迁移路径(一次性,P5.A 开工前完成)

1. 当前仓库重命名:`PCE-Core` → `pce`,仓库 description 加 "Apache-2.0"
2. 创建私有仓库 `pce-pro`
3. 添加 `LICENSE`(Apache-2.0) + `NOTICE` + `CONTRIBUTING.md` + `SECURITY.md` 到 `pce`
4. `pce_core/__init__.py` 版本号碰 `1.0.0-dev`,发布到私有 PyPI(或 git+https 依赖)
5. **新建的 Pro 模块从 Day-1 放 `pce-pro`**,不要先写在 `pce` 再搬
6. 更新 `README.md` 说明 OSS 与 Pro 功能矩阵

### CI/CD 协调

- `pce` 使用 GitHub Actions 免费额度,公开日志
- `pce-pro` 使用 GitHub Actions Teams 或自托管 runner
- Release 流水线通过 trigger 同步:`pce` 打 tag 后自动触发 `pce-pro` 的 CI 拉新版
- 签名密钥(Apple Developer / EV 证书 / Frida 脚本签名私钥)只在 `pce-pro` runner 上可用

## Rationale

1. **信任根基在 OSS 那侧**。CA 安装 + MITM + 存储路径 — 这些是用户最怕"被偷数据"的地方,必须开源让人看得到。
2. **商业差异化在 Pro 那侧**。L0 内核驱动需要企业级签名;L2 Frida 需要对抗 AV 持续投入;Supervisor 是自动化的卖点。这些都是"做一次收长期费"的工作。
3. **契约前置是 Open Core 的正确姿势**。CaptureEvent v2 开源意味着第三方可以写 Pro 的替代品 — 这是开源承诺的自由,也是防止"伪开源"的唯一办法。
4. **Apache-2.0 的工程可操作性最强**。专利 grant + 商业友好 + marketplace 无阻力,PCE 阶段的所有场景都能覆盖。
5. **Day-1 拆分比事后迁移便宜两个数量级**。`pce_agent_*` 目录还不存在,现在钉死 Pro 归属,后面每次 MR 都能无脑放对仓库。

## Consequences

### Positive

- OSS 版装完立刻能用(抓 Web Chat + Copilot + Codex CLI + ChatGPT 网页,覆盖 55%+ 使用量),信任度拉满
- Pro 版的付费点清晰(反 pinning + 自动化 + 强制捕获),用户愿意为明确价值付费
- 第三方生态有扩展空间(新站点提取器 / 新形态 detector → OSS PR)
- Chrome Web Store / VS Code Marketplace 审核更顺利
- 与 ADR-001(只记录)和 ADR-002(本地代理)精神一致,local-first 叙事更有说服力

### Negative

- 多维护一个私有仓库,开发流程比单仓库复杂
- Pro 模块新人入职 onboarding 成本高(双仓库 setup)
- CaptureEvent v2 schema 变更需要双仓库同步(有摩擦)
- 发版需要 release 脚本同步两个仓库,CI 复杂度翻倍
- 社区 contributor 可能要求"这个功能为什么在 Pro 不在 OSS"(需要清晰的沟通策略)

### Neutral

- License 选择可以在 v1.0 发布前微调(Apache-2.0 是起点,不是终点)
- Pro 模块边界可以在后续 ADR 里微调(例如 L4c OCR 现在划 OSS,未来若变复杂可重新评估)
- 未来若某天判断"PCE 应该全开源"(走 Mitmproxy 路线),OSS 仓库本来就完整,Pro 仓库可以直接开源合并

## Guardrails

- **CI 必须跑依赖方向检查**:`pce` 仓库每次 PR 自动扫 import,出现 `pce_agent_*` 或 `pce_core.capture_supervisor` 则 fail
- **CaptureEvent v2 schema 冻结后变更需要双仓库同步 review**:任一仓库 PR 改 schema 必须同时在对方仓库开配套 PR
- **`pce_core.capture_event` 禁止引入 Pro-only 依赖**:它是 OSS 根契约,不能有 Pro 才能安装的包
- **Pro 模块之间可以互相依赖,但都依赖于 `pce`**:`pce_agent_frida` 可以 `from pce_core.capture_supervisor import ...`(两者都在 pce-pro),但都必须 `from pce_core.capture_event import CaptureEventV2`
- **所有 Pro 模块发事件必须走本地 HTTP**:不允许进程内直接调 `insert_capture()`,即便物理上能做到
- **License header**:Apache-2.0 的文件顶部带 SPDX identifier,Pro 文件顶部带 proprietary 声明
- **敏感资产**:Frida 脚本签名私钥、Apple Developer ID、Windows EV 证书,**只能在 `pce-pro` runner secrets 中**,永远不进入 `pce` 仓库

## Review Trigger

以下情况必须回来重新评估:

- 决定走全开源路线(类似 Mitmproxy / Bitwarden),Pro 仓库整体合并进 OSS
- Pro 某模块因合规/法务原因必须开源(例如某国法规要求流量监控软件必须公开源码)
- OSS 版出现"有人拿来做 SaaS 不回馈"的严重竞品(届时考虑转 AGPLv3 或 BSL)
- Apache-2.0 被某主流 marketplace 弃用(目前无此迹象)
- Pro 订阅模式失败需要转向其他变现(如托管云 PCE Cloud),边界需要再考虑

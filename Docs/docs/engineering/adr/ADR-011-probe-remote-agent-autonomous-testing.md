# ADR-011: PCE Probe 远程化 与 Agent 自治测试通道（VPS 部署形态）

- Status: Proposed (deferred to P5.B, not implemented)
- Date: 2026-05-07
- Related: ADR-009 (Universal Capture Stack), ADR-010 (Open Core 模块边界), ADR-006 (WXT 扩展框架)
- Spec touched: `Docs/docs/engineering/PCE-PROBE-API.md` §3 / §7 / §8, `Docs/testing/PCE-PROBE-USAGE.md` §7 / §8
- Follow-up: 待 P5.B "Server deployment" 立项时把本 ADR 翻成 Accepted + 拆 ticket

## Context

P5.A 阶段 `pce_probe/` + `tests/e2e_probe/` 已经把"agent 友好的站点测试通道"做出 70%：
WS 动词层、矩阵跑测、`summary.json` / `triage --json` 机器可读输出、失败时富化的 `dom_excerpt` + `agent_hint`、14 站 × 21 case 的笛卡尔积、AGENT-LOOP runbook 等等都已落地（详见 `@f:\INVENTION\You.Inc\PCE Core\Docs\testing\PCE-PROBE-AGENT-LOOP.md`）。

但 2026-05-07 的需求评审暴露了一个新的产品形态诉求：

> **用户场景**：在一台 VPS 上跑一个 Claude-Code-like agent，由它通过插件配套的"统一外部接口"自主调用测试 + 深度调查站点变更，做到**官方站点更新前 / 更新中**就发现回归，而不是等用户用插件失败之后再被动反应。

把这个诉求和现状对账，发现 9 个 gap（编号 **G1–G9**），可分为三类：

1. **接口可远程**（G1–G2）：probe server 硬编码 loopback、无 HMAC/TLS、无 HTTP facade；当前 agent 必须和 Chrome 同机
2. **主动发现而非被动等失败**（G3）：现状只有"跑测试时才暴露 selector 变更"，没有 DOM baseline + 定时 diff 的 watcher
3. **VPS 化基础设施**（G4–G9）：headless + Xvfb 没打包、登录态不可移植、调度器不存在、多租户/auth 缺失

如果 P5.A 收尾就把这 9 项一次做完，会抢占核心捕获能力的工期；如果完全不写明，6 个月后做 P5.B "Server deployment" 时（spec §8 已经预留）会重新踩一遍这些坑。所以本 ADR 的目的是**记录而非实现**，锁住未来设计约束。

## Decision

P5.A 阶段**不立项实现** G1–G9，但本 ADR 把它们全部登记成 known-gaps，同时锁定下面三条铁律，任何未来 VPS-agent 通道的实现都必须满足：

### 铁律 1 — 远程化必须配套 auth + TLS，不能裸开 0.0.0.0

`pce_probe/server.py` 的 `DEFAULT_HOST="127.0.0.1"` 是 spec §7 的核心安全假设，不能被一句"加个环境变量"绕过。任何让 server 监听非 loopback 的实现必须**同 commit**带上：

- 强制 `PCE_PROBE_HMAC_KEY` 环境变量校验（spec §7 已预留），缺失即拒绝绑定非 loopback
- HTTP facade（如有）走反向代理（nginx / Caddy）终结 TLS，源站只接 unix socket 或 loopback
- `Origin` 头白名单从 `chrome-extension://` / `null` 扩展到具名 agent host

### 铁律 2 — 站点变更主动发现机制必须独立于矩阵 run

G3 watcher 必须满足：

- **不依赖发 prompt**：以 T00 + `dom.query` 为基础，零 quota 消耗、零计费风险
- **DOM baseline 存档在仓**：`tests/e2e_probe/baselines/<site>.json` 走 git，不是运行时副产物，这样 agent 可以做 PR-级 diff
- **告警通道与失败现场分离**：watcher 触发的是 "selector_changed_pending_review"，不是 fail；不污染 `summary.json` 的 by_status

### 铁律 3 — 登录态可移植性优先于自动登录

- 不投资"用户名密码 + 2FA 自动登录"（合规/反爬都不友好）
- 投资"profile 导出 / 导入"链路：本地 logged-in profile 打包 → VPS 上解包 → 共用同一 cookie；过期后 agent 主动告警，由人 1 分钟内补充
- 14 个 AI 站的 cookie TTL 经验值（30–90 天）写入 `Docs/testing/PCE-PROBE-VPS-DEPLOY.md`

### 已登记的 9 个 gap

为方便未来 sprint planning，按优先级登记：

| ID | Gap | 阻塞场景 | 预估工作量 |
|----|-----|---------|----------|
| **G1** | `pce_probe/server.py` 解锁 `PCE_PROBE_HOST` + HMAC 校验 | 远程调用 | 0.5d + 0.5d |
| **G2** | `pce_core/server.py` 加 `/api/v1/probe/run` `/matrix/run` `/triage/latest` 三个 HTTP 路由 | 非 Python agent | 1d |
| **G3** | `pce_probe/watcher.py` + `tests/e2e_probe/baselines/` DOM baseline 体系 | 主动发现 | 3d |
| **G4** | `deploy/probe-vps/Dockerfile` + `docker-compose.yml`（Chrome + Xvfb + 扩展 + Python） | VPS 部署 | 1.5d |
| **G5** | `pce_probe/login_state.py` 的 export/import 子命令 | 无人值守登录 | 1d |
| **G6** | `pce_probe/login_health.py` 周期 T00 + login_wall 告警 | 登录态自愈 | 0.5d |
| **G7** | watcher 的 cron / systemd timer 调度 | 全自动 | 0.5d (依赖 G3) |
| **G8** | 多 agent 并发 / 多 Chrome profile 隔离 | 多租户 | 不在 v1.x 投资 |
| **G9** | Selector 变更 → adapter patch 的 LLM auto-fix proposal | 完全无人 | agent 侧实现，插件不投资 |

## Rationale

1. **现有 70% 已经够本机 agent 用**：`PCE-PROBE-AGENT-LOOP.md` 描述的 `triage → decide → edit → replay → commit` 循环已经在 P5.A 跑通过，本机 + LLM 协作的场景**不用补任何东西**。要补的只是"agent 不在本机"这个变量。
2. **9 个 gap 的工作量集中在基建，不在协议**：G1–G9 没有任何一个需要改 22 个动词、改矩阵 schema、改 case 结构。所以延后做不会让现有代码"变陈旧"，更不会引发兼容断点。
3. **spec §8 已经预留了 Stage 2**：`PCE-PROBE-USAGE.md` §8 原话 "lifts this whole vertical onto a dedicated Linux server with Xvfb + headed Chrome + the PCE extension + the probe server, fronted by an HTTPS proxy with a shared HMAC. **That's not in this repo yet.**" 本 ADR 是把这句话从口述升级成可审计的 known-gaps 清单。
4. **P5.A 抢工期不划算**：核心捕获（L1+L3a）才是 v1.0 的 release 闸门（ADR-009 §渐进发布策略）。把 9 天工期挪到 P5.B 不影响 v1.0；先做完反而因为没用户验证、没有真实 agent 调用 trace，拍脑袋做出来的接口大概率要重写。
5. **"主动发现"诉求被独立锁住（铁律 2）**：用户原话最关键的一句是"不希望等更新后我们才开始反应"。即便 G1/G2/G4-G7 全延后，G3 watcher 的契约必须先冻结，否则未来做出来又是一个被动机制（比如 watcher 自己只调矩阵 run，那只是把人手敲 pytest 换成 cron 敲 pytest，没解决"主动"问题）。
6. **不投资 G8 多租户和 G9 auto-fix**：G8 走"多端口多 profile"已经够，没必要在协议层做并发；G9 是 agent 侧的 prompt 工程，不是工具侧的事。

## Consequences

### Positive

- P5.A 工期不被稀释，v1.0 release 闸门不动
- 9 个 gap 的设计约束被铁律锁死，未来实现只能填空不能跑偏
- spec §8 不再是口述，可被 PR review 引用
- watcher 的语义提前定义（DOM baseline + git 存档），让未来实现者不会做出"伪主动"方案
- 给 P5.B planning 留下了清晰的 ticket 起点（每个 G 直接对应一个 issue）

### Negative

- 用户描述的 VPS-agent 自治场景在本 ADR 落地后**仍然不可运行**，需等 P5.B 真实立项
- 14 站点持续保持"被动反应"模式，期间任何 selector breakage 仍要等 e2e_probe run 触发才能发现（best-of 历史最长间隔 2 周）
- ADR 数量增加但代码不动，新人 onboarding 需要额外读这一篇才能理解 "为什么 spec 写了 §8 但仓库里没东西"

### Neutral

- 9 个 gap 的工作量估算（合计 ~9 天）只是粗估，真实立项时需要重新拆 ticket
- 部分 gap（如 G6 login_health）可以独立于其他 gap 提前做，不必等 P5.B 整体立项
- 本 ADR 不阻止任何人在自己 fork 里实现 G1–G9；只是说**主仓在 P5.A 阶段不接受相关 PR**

## Guardrails

任何未来引入 VPS-agent 通道的 PR 必须：

- 引用本 ADR 编号 `ADR-011`
- 同时更新 `PCE-PROBE-API.md §7 / §8` 和 `PCE-PROBE-USAGE.md §7 / §8`，使其与代码一致
- 通过现有 `tests/test_pce_probe.py` 24/24 GREEN（loopback 路径不能因为新增远程路径而退化）
- 新增的远程绑定路径必须有 `tests/test_pce_probe_remote_auth.py`，覆盖：
  - 缺少 `PCE_PROBE_HMAC_KEY` 时拒绝绑定非 loopback
  - 错误 HMAC 头返回 401
  - `Origin` 不在白名单时拒绝 WS upgrade
- watcher 的 baseline 文件改动必须走 PR review，不允许 CI 自动写 git
- watcher 的告警事件必须独立 schema，不能复用 `summary.json` 的 `by_status: fail`，避免污染矩阵 pass-rate

## Review Trigger

以下任一情况发生时，本 ADR 必须重新评估并升级为 Accepted（开始实施 G1–G7）：

- P5.B 阶段正式立项 "Server deployment"（spec §8 的目标态）
- 出现一次因被动反应延迟导致的真实生产事故（例如某站点改 DOM 后 PCE Core 7 天没人跑 e2e_probe，用户先于我们发现）
- 14 站中任何一个站点的 selector 变更频率从月级降到周级 — 此时纯人工 triage 不可持续
- 团队规模扩张到 ≥ 3 名工程师全职做 PCE — 边际成本足以承担一条独立基建链路
- Chrome 扩展平台政策变化使得 sideload 流程更困难（ADR-003 兜底失效），需要把测试链路上 VPS 隔离

如果以上 trigger 都未触发但 6 个月后（2026-11-07）仍未立项，**复审本 ADR**：要么续期，要么把 9 个 gap 拆细到当时认为合理的颗粒度，要么彻底放弃 VPS-agent 形态（如果届时本机 agent loop 已经被证明够用）。

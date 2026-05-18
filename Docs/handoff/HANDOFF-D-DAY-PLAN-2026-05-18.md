---
title: "D-Day — 桌面端 8 P0 一日攻坚计划"
status: PLAN_LOCKED
date: 2026-05-18
session: planned 09:00–20:30 local (~11.5h, owner ~3.5h at keyboard)
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md
audit_basis: REDUNDANCY-AUDIT-MATRIX.md (post-B0 reset)
scope:
  tier_alpha_close_today: [f4_p1_claude_desktop, f5_p4_windsurf, f6_p6_claude_code_cli, f6_p7_codex_cli, f6_p8_gemini_cli]
  tier_beta_advance_today: [f4_p2_chatgpt_desktop, f5_p3_cursor, f5_p5_github_copilot]
  out_of_scope_today: [web 5, Phase B 真工程 ~420h]
deliverable_targets:
  strict_v_green_ge3: 5/8 desktop
  v_green_ge2: 8/8 desktop
  v_green_clean_ge2: 8/8 desktop
  adr_adopted: [ADR-022, ADR-023]
  evidence_committed: Docs/handoff/_evidence_D_DAY/, Docs/handoff/_evidence_A2_app_sweep/
---

# D-Day Plan — 桌面端 8 个 P0 场景一日攻坚

> 本计划由 2026-05-18 审计触发（见 REDUNDANCY-AUDIT-MATRIX 与 nightly
> SVG 之间 0/13 vs 9/13 的诚信差）。执行 owner 已确认 scope, 按本文档
> 11 个 block 顺序推进。每个 block 闭合后回写本文档 §6 Run Log.

---

## §0 物理边界声明

仓库自己签字记录的 Phase B 真工程量：

| Track | 场景 | 估时 |
|---|---|:---:|
| W6 | GitHub Copilot 全套（含 VS Code 扩展 L3c） | ~80h |
| W7 | ChatGPT Desktop UIA daemon | ~140h |
| W8 | Cursor protobuf decoder | ~200h |
| **合计** | | **~420h** |

单日（~12h productive）做不完 8/8 真 V-GREEN ≥3。本计划的"全量"语义：

- **Tier-α 5 个场景**（P1 / P4 / P6 / P7 / P8）→ 真 V-GREEN ≥3，**闭环今日**
- **Tier-β 3 个场景**（P2 / P3 / P5）→ 推到 **2/3** + Phase B 三 track 的
  **RECON 数据 + ADR Adopted + 骨架代码**今日落
- 剩余约 1/3 的真实工程留 6 周 wall-clock 不变（W6/W7/W8 各 track）

如果后续判定需要 8/8 强行今日，必须放宽 V-GREEN 定义（如把 L4a 剪贴板
重新算 leg）— **本计划不接受这种放宽**，因为它把先前 §1.1 V-AUX 拆分
回退到误导口径。

---

## §1 时间表

| Block | 时段 | 角色 | 主题 | 闭合标志 |
|:-:|---|---|---|---|
| B0  | 09:00–09:30 | agent solo | 矩阵诚信归零 | §3.3-3.6 V-GREEN 标记重置完, commit pushed |
| B1  | 09:30–11:00 | agent solo | Codex / Gemini CLI L1 旁路修复 | mitmdump.log 见到两 CLI 流量 |
| B2  | 11:00–11:30 | owner | A2 daemon 健康检查 + Clash TUN 验证 | `stats --limit 10` 见 3+ iface 落行 |
| B3  | 11:30–12:30 | owner + agent | F6 三 CLI live（L1 + L3g + L3h） | 3 个 .db snapshot 落 _evidence_D_DAY/ |
| B4  | 12:30–13:30 | — | 午休 / daemon 后台抓 | — |
| B5  | 13:30–14:30 | owner + agent | F4 P1 Claude Desktop .mcpb live | W1-T2 handoff 真写完 |
| B6  | 14:30–15:30 | owner + agent | F5 P4 Windsurf MCP live | W1-T7 handoff 真写完 |
| B7  | 15:30–16:30 | owner + agent | A2 per-app sweep（5 桌面 app × 60s） | 5 个 app-specific .db |
| B8  | 16:30–17:30 | owner + agent | F5 P5 Copilot L1 live + MCP install | 2-3/3 落地 |
| B9  | 17:30–18:30 | agent solo | F4 P2 ChatGPT Desktop closure + ADR-022 Adopted + UIA RECON | 2/3 + ADR + RECON dump |
| B10 | 18:30–19:30 | agent solo | F5 P3 Cursor closure + ADR-023 Adopted + protobuf RECON | 2/3 + ADR + .pcapng |
| B11 | 19:30–20:30 | agent solo | Wave 9 acceptance + nightly SVG re-render | HANDOFF-D-DAY-RESULT.md + green cards |

总：~11.5h，owner 键盘前合计 **~3.5h**，分 5 个 block（B2/B3/B5/B6/B7/B8）。

---

## §2 Block 详细执行

### B0 · 矩阵诚信归零（agent solo · 30 min）

把 `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §3 改回真实状态。规则：

- 引用的 handoff 文件 grep 不到 → 标 **V-HERMETIC**（不是 V-GREEN）
- "host equivalence" 论证 → 标 **V-PARTIAL**（等 B7 sweep 后再升）
- W1-T1 引用是 TODO 模板 → 标 **V-DOC-ONLY**

预期 §4.1 汇总跌到 ≥3 V-GREEN: **1/13**（仅 F6 P6 真有 [HANDOFF-P1-N-L1-VALIDATION](Docs/handoff/HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md) 这种 live 证据）。

提交：`chore(redundancy): reset matrix to verifiable state pre-D-day`

### B1 · Codex / Gemini L1 旁路修复（agent solo · 1.5h）

两个 CLI 都不读 `HTTPS_PROXY`。**不走 Rust/undici 改造**（耗时大），改走
**Clash TUN 透明转发**（owner 已 ambient 运行 Clash）：

1. `pce_proxy/run_proxy.py` 加 `--mode transparent` 启动模式
2. Clash 配置加 `rule: DOMAIN-SUFFIX,api.openai.com,Proxy-To-Mitm-8081`
   （同理 `generativelanguage.googleapis.com` / `cloudcode-pa.googleapis.com`）
3. mitmproxy 透明模式监听 8081，证书走 OS root CA
4. 单测：`tests/test_transparent_proxy_mode.py` 4 个 case
5. live 烟测：git bash 跑 `codex exec 'say four'` 与
   `gemini --skip-trust -p 'say four'`，断言 mitmdump.log 收到对应请求

**回退**：如果 Clash 路由跟现有规则冲突 → 在 `pce_cli_wrapper` shim 里
`set HTTPS_PROXY` 后 `--exec` 给目标 binary。Rust binary 可能仍不读，
但值得 5 分钟试。

### B2 · A2 daemon 健康检查（owner · 30 min）

```powershell
# 1. 确认 W2.3 service install 还在
Get-ScheduledTask PCE-SSLKEYLOG-Capture | Select State

# 2. 确认 keylog 文件在写入
Get-Item $env:SSLKEYLOGFILE | Select Length, LastWriteTime

# 3. 确认 multi-iface daemon 抓到 Loopback + WLAN + Clash TUN
python -m pce_sslkeylog stats --limit 10
```

如果 0 行 / iface 列表缺 Clash → owner 重启 daemon：

```powershell
python -m pce_sslkeylog daemon stop
python -m pce_sslkeylog daemon start --auto-iface
```

### B3 · F6 三 CLI live（owner + agent · 1h）

mitmproxy 透明模式（B1）开起来后，**单一终端跑完三件**：

```powershell
$env:HTTPS_PROXY = $null  # 透明模式不需要 env
$env:NODE_OPTIONS = "--tls-keylog=$env:SSLKEYLOGFILE"

# 1. Claude Code CLI (L1 + L3g + L3h 同时打)
claude --print "say four for L1+L3g+L3h test"

# 2. Codex CLI (走 Clash transparent)
codex exec "say four for L1+L3g+L3h test"

# 3. Gemini CLI (走 transparent + NODE_OPTIONS 写 keylog → A2 第四条)
gemini --skip-trust -p "say four"

# 4. agent: scan ~/.claude/projects + ~/.codex/sessions + ~/.gemini/sessions
python -m pce_persistence_watcher scan --emit-beacon
python -m pce_persistence_watcher.ide_scanner scan --emit-beacon

# 5. agent: 截库 + 三个 handoff 写实数
python tools/dump_evidence.py --scenarios f6_p6,f6_p7,f6_p8 \
    --out Docs/handoff/_evidence_D_DAY/
```

需要新写 `tools/dump_evidence.py`（30 min 事）：给定 scenario id 输出
`.db` 截快照 + 自动填模板成 handoff（行号 / pair_id / model_name /
source_id histogram）。

**EOD F6 三件预期状态**：

| Scenario | Legs | clean |
|---|:-:|:-:|
| P6 Claude Code CLI | L1 + L3g + L3h = **3/3** ✅ | 2 |
| P7 Codex CLI | L1 + L3g + L3h = **3/3** ✅（假设 B1 成功） | 2 |
| P8 Gemini CLI | L1 + L3g + L3h + A2 = **4/3** ✅✅ | 3 |

### B5 · F4 P1 Claude Desktop（owner + agent · 1h）

```powershell
# 1. 装 .mcpb（双击安装到真 Claude Desktop）
explorer.exe pce_mcp\dist\pce-mcp-0.1.0.mcpb
# owner UI 操作：Settings → MCP → 添加 pce-mcp → enable

# 2. 重启 Claude Desktop
Stop-Process -Name "Claude" -Force -ErrorAction SilentlyContinue
Start-Sleep 2
Start-Process "shell:appsFolder\Claude_pzs8sxrjxfjjc!Claude"

# 3. owner 在 Claude UI 开新对话发：
#    "Use the pce_capture tool to log this with provider=test model=p1-d-day"
# Claude 直接 invoke 工具，落 source_id='mcp-default' 一行

# 4. 同窗口再发一句 "What is 2+2?" → L1 mitm 抓 + L3g scan 后落

# 5. agent: 截库 + 填 W1-T2 handoff
python tools/dump_evidence.py --scenarios f4_p1_claude_desktop
```

**P1 状态**：L1c + L3g + L3f .mcpb = **3/3** ✅（2 clean: L3g + L3f）。

**回退**：.mcpb 装不上 → 用 `pce_mcp_proxy` posture B（耗时 +30 min,
走 stdio 注入到 claude_desktop_config.json）。

### B6 · F5 P4 Windsurf（owner + agent · 1h）

```powershell
# 1. 写 mcp_config.json (PYTHONPATH 指向 repo, 避免 Windsurf 重写丢 cwd)
$repo = (Resolve-Path .).Path
$cfg = @{
  mcpServers = @{
    pce = @{
      command = "python"
      args = @("-m", "pce_mcp.server")
      env = @{ PYTHONPATH = $repo }
    }
  }
} | ConvertTo-Json -Depth 4
$cfg | Set-Content "$env:USERPROFILE\.codeium\windsurf\mcp_config.json"

# 2. owner 开 Windsurf → Cascade 面板 → 见 "pce: Enabled, 6/6 tools"

# 3. owner 发 prompt:
#    "Use the pce_capture tool with provider=test model=p4-d-day"

# 4. owner 在另一个 Cascade chat 发 "What is 2+2?" → L1 codeium gRPC 抓

# 5. agent: 截库 + 填 W1-T7 handoff
python tools/dump_evidence.py --scenarios f5_p4_windsurf
```

**P4 状态**（B7 之后）：L1c + L3f MCP + A2 = **3/3** ✅。

### B7 · A2 per-app sweep（owner + agent · 1h）

对 5 个 Chromium 桌面应用各跑 60s 实测，把"host equivalence"升级成
"app-specific live"：

```powershell
foreach ($app in @("Claude","ChatGPT","Cursor","Windsurf","Code")) {
    Write-Host "=== $app sweep ==="
    Read-Host "Press Enter when $app is active and you've sent 1 message"
    python -m pce_sslkeylog capture --duration 60 `
        --tag "p5d1-app-sweep-$app" `
        --out Docs/handoff/_evidence_A2_app_sweep/$app.db
}

# 后处理：agent 验证每个库都有该 app 的 PID/process 名匹配 +
# 至少 1 个 HTTP/2 frame 解 plaintext
python tools/verify_a2_app_sweep.py
```

每个桌面 app 自此都有专属 A2 V-GREEN-clean 第二条腿。

### B8 · F5 P5 GitHub Copilot 推到 2/3（owner + agent · 1h）

```powershell
# 1. owner: VS Code 开起来，确认 GitHub Copilot 扩展登录

# 2. agent: 启动 mitmproxy (W6-T1 normalizer 已在线)
mitmdump -s run_proxy.py -p 8080 --set stream_large_bodies=1m

# 3. owner: VS Code → Ctrl+Shift+I → Copilot Chat 发 "say four"

# 4. agent: 截库验证 api.githubcopilot.com 行
python tools/dump_evidence.py --scenarios f5_p5_github_copilot

# 5. agent: pce_mcp_proxy install → VS Code mcp.json 注入
python -m pce_mcp_proxy install --target vscode
# owner: VS Code MCP UI 启用 pce → 调一次 tool
```

**P5 状态**：L1c (W6-T1) + L3f MCP + A2 = **3/3** ✅（如果 MCP install
通）；如果 MCP 失败 → **2/3**（L1c + A2），L3c VS Code 扩展真工程留 W6。

### B9 · F4 P2 ChatGPT Desktop 推到 2/3 + ADR-022（agent · 1h）

- **A2 sweep**（B7 已包含 ChatGPT app sweep，复用证据）
- 矩阵 §3.4 P2 行升级：L1c + A2 = **2/3** ✅
- **ADR-022 起草 → Adopted**：
  `Docs/docs/engineering/adr/ADR-022-uia-accessibility-oss-boundary.md`
  已有 draft → 写完 §3 边界 / §4 privacy 章节 → 改 status: Adopted
- **UIA RECON dump**：

  ```powershell
  python tests/e2e_desktop_ui/scripts/dump_uia.py `
      --app ChatGPT `
      --out Docs/research/2026-d-day-chatgpt-uia-tree.json
  ```

  （owner 帮 alt-tab 切到 ChatGPT，agent 触发 dump）

**不**实现 `pce_core/uia_driver/` — 那是 W7 ~140h 工程，留 6 周 wall-clock。

### B10 · F5 P3 Cursor 推到 2/3 + ADR-023 + protobuf RECON（agent · 1h）

- `pce_mcp_proxy install --target cursor` → Cursor MCP UI 验证 → 调一次
  → L3f MCP 真签
- A2 在 B7 已抓 Cursor app sweep → A2 clean
- **ADR-023 Adopted**：
  `Docs/docs/engineering/adr/ADR-023-grpc-web-protobuf-decoder.md`
  draft 写完 → Adopted
- **protobuf RECON**：

  ```powershell
  # owner active Cursor 同时跑一次 chat
  mitmdump -s tools/record_pcap.py -p 8081 --listen-host 0.0.0.0 `
      --save-stream-file Docs/research/2026-d-day-cursor-grpcweb.pcapng
  ```

**P3 状态**：L3f MCP + A2 = **2/3**（V-GREEN-clean）。L1 protobuf decoder
真工程留 W8。

### B11 · Wave 9 acceptance（agent solo · 1h）

```powershell
# 1. 重建 SVG（拉 24h 内 beacon + raw_captures 真行号）
python -m tools.render_redundancy_matrix --force-refresh

# 2. 重写矩阵 §3 / §4 到真状态
python tools/regenerate_audit_matrix.py --from-evidence Docs/handoff/_evidence_*/

# 3. supervisor /status 跑一次断言
curl http://127.0.0.1:9800/api/v1/supervisor/status > Docs/handoff/_evidence_D_DAY/status.json

# 4. CI guard
python -m tools.check_redundancy_targets --threshold strict --expected-pass 5

# 5. 写 HANDOFF-D-DAY-RESULT-2026-05-18.md：本日 11 个 block 全部签字
# 6. CHANGELOG.md 写一句 milestone
#    (不打 v1.1.6 tag — 缺 W6 L3c + W7 L4b + W8 L1)
```

---

## §3 EOD 预期状态

| Scenario | Color | Legs | clean | 缺口（留多久） |
|---|:-:|:-:|:-:|---|
| f4_p1_claude_desktop | green | 3/3 | 2 | — |
| f4_p2_chatgpt_desktop | yellow | 2/3 | 1 | L4b UIA (W7 ~3-4 周) |
| f5_p3_cursor | yellow | 2/3 | 2 | L1 protobuf (W8 ~4-6 周) |
| f5_p4_windsurf | green | 3/3 | 2 | — |
| f5_p5_github_copilot | green/yellow | 2-3/3 | 1-2 | L3c VS Code 扩展 (W6 ~2 周) |
| f6_p6_claude_code_cli | green | 3/3 | 2 | — |
| f6_p7_codex_cli | green | 3/3 | 2 | — |
| f6_p8_gemini_cli | green | 4/3 | 3 | — |

桌面端汇总：
- **≥3 V-GREEN: 5-6 / 8**
- **≥2 V-GREEN-clean: 8/8**
- **≥3 V-GREEN-clean: 3/8**

---

## §4 风险 + 回退路径

| 风险 | 触发 | 回退 |
|---|---|---|
| B1 Clash TUN 转发失败 | mitmdump.log 看不到 codex/gemini 流量 | Codex 跳 L1，认 2/3（L3g + L3h）今日；Gemini NODE_OPTIONS A2 已保 3/3 |
| B5 .mcpb 装不上 | Claude Desktop 拒载 | 回退 `pce_mcp_proxy` posture B（+30 min, stdio 注入 claude_desktop_config.json） |
| B6 Windsurf mcp_config 被重写 | Cascade 重启后 cfg 丢字段 | 已知 W1-T7 workaround：env PYTHONPATH；失败则 owner 手编 json |
| B7 A2 sweep 抓不到目标 app PID | 进程名不匹配 | `pce_sslkeylog capture --filter "process_name LIKE '%Claude%'"` 重抓 60s |
| B8 mcp_proxy 装 vscode 失败 | mcp.json schema 变 | 跳 MCP，P5 落 2/3（L1c + A2），L3c 真工程留 W6 |
| B10 Cursor MCP 不支持 install | Cursor 0.x 不读 ~/.cursor/mcp.json | 手编 settings.json 注入；失败则 P3 落 1/3（仅 A2） |
| 总工时超 12h | 单 block 超 1.5h | 砍 B9/B10 的 RECON dump → 保 B0-B8 五件 Tier-α 闭环 |

---

## §5 今日交付清单（必须）

- [ ] 矩阵 §3 / §4 重写到真状态（B0 + B11）
- [ ] 5 件 Tier-α handoff 真写完，每个含 `raw_captures` 行号 + pair_id +
      model_name + .db 路径（B3 + B5 + B6）
- [ ] 5 个 `.db` snapshot 提交 `Docs/handoff/_evidence_D_DAY/`
      （commit message 含 sha256）
- [ ] A2 5-app sweep 5 个 `.db` 提交 `Docs/handoff/_evidence_A2_app_sweep/`
- [ ] ADR-022 + ADR-023 status: **Adopted**
- [ ] UIA RECON dump（`Docs/research/2026-d-day-chatgpt-uia-tree.json`）
      + Cursor protobuf `.pcapng` 留档
- [ ] `HANDOFF-D-DAY-RESULT-2026-05-18.md` 总结 handoff
- [ ] nightly SVG 重渲，至少 5 张桌面卡片是 green
- [ ] `CHANGELOG.md` 一行 milestone（**不打 v1.1.6 tag** — Phase B 三件
      真工程仍未闭）

---

## §6 Run Log（执行中回填）

| Block | 起 | 终 | 结果 | commit | 备注 |
|:-:|---|---|:-:|---|---|
| B0  | 09:00 | 09:25 | ✅ PASS | a9814d9 | §0.1 banner + 13 verdict 行 reset + §4.1.0 真状态表 + §4.1.1 历史表归档. ≥3 strict: 9-10/13 → 0/13 (matches nightly SVG). |
| B1  | 09:30 | 10:50 | ✅ PASS | (this commit) | 实测发现 3 CLI 全 Node-installed (Rust 假设不成立). 改用 env 注入: `_proxy_env.py` + `_undici_proxy_inject.js` + relay.py 两 Popen env wire-in. 14 新单测 GREEN, 94/94 e2e_cli 无回归. **不写 transparent proxy 了**; HTTPS_PROXY + NODE_OPTIONS 两路足够. 见 HANDOFF-D-DAY-B1-CLI-PROXY-INJECTION-2026-05-18.md. |
| B2  | — | — | — | — | — |
| B3  | — | — | — | — | — |
| B5  | — | — | — | — | — |
| B6  | — | — | — | — | — |
| B7  | — | — | — | — | — |
| B8  | — | — | — | — | — |
| B9  | — | — | — | — | — |
| B10 | — | — | — | — | — |
| B11 | — | — | — | — | — |

---

## §7 引用

- 上游 scope：`Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md`
- 上游标准：`Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §1（V-GREEN 定义）
- 直接前继：`Docs/handoff/HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md`
- Phase B 三 track kickoff:
  - `Docs/handoff/HANDOFF-W6-COPILOT-KICKOFF-2026-05-19.md`
  - `Docs/handoff/HANDOFF-W7-CHATGPT-DESKTOP-UIA-KICKOFF-2026-05-19.md`
  - `Docs/handoff/HANDOFF-W8-CURSOR-PROTOBUF-KICKOFF-2026-05-19.md`
- 当前状态客观源：`Docs/stability/REDUNDANCY-MATRIX.svg`
  （2026-05-17 05:44 UTC：0/13 redundant）

---

## §8 一句话锚点

**Tier-α 5 件闭环 + Tier-β 3 件推到 2/3 + 2 个 ADR Adopted + 2 份 RECON
留档 + 矩阵诚信归零。EOD 桌面端 ≥3 V-GREEN: 5-6/8；剩 W6/W7/W8 三 track
6 周 wall-clock 工程留 Phase B 真闭关。**

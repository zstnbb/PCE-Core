# P4 Windsurf (Cascade) — RECON Findings

> **Date**: 2026-05-13
> **Status**: empirical (post-RECON)
> **Supersedes**: `2026-05-13-windsurf-desk-research.md` §2.1 (cert-pinning hypothesis CONFIRMED + bypass WORKS)
> **Next**: 阶段 (3) write contract — WINDSURF-PRODUCT-MATRIX case grid

---

## 1. 验证 / 推翻的假说

| 假说 (from desk §2) | 实测结果 | 证据 |
|---|---|---|
| Cascade 聊天面 cert-pinned (L1 blocked) | **CONFIRMED** — 但 NODE_EXTRA_CA_CERTS **完全解除** | 0 TLS failures after injection; 200+ captures |
| 本地 cascade/*.pb 是加密的 | **CONFIRMED** — 高熵数据, 无可读字符串 | hex dump shows random bytes |
| L3g 不可行 | **CONFIRMED** — 加密存储, 无法提取聊天内容 | — |
| NODE_EXTRA_CA_CERTS 能解锁 L1 | **CONFIRMED ✅** | 完整对话明文捕获 |
| 域名是 server.codeium.com | **SUPERSEDED** — 实际域名 `server.self-serve.windsurf.com` | Cognition 收购后迁移 |
| gRPC protobuf 格式 | **CONFIRMED** — 但聊天内容在 protobuf 中以明文字符串存在 | 可直接 regex 提取 |

---

## 2. 实测数据流

### 2.1 网络面 (N) — 主路线 ✅

**域名**: `server.self-serve.windsurf.com` (替代了旧的 `server.codeium.com`)

**协议**: gRPC over HTTPS (HTTP/2, protobuf wire format)

**解锁方式**: `NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem` + `https_proxy=http://127.0.0.1:8080`

**19 个 gRPC endpoint 被发现**:

| Service | Method | 内容 | 对 PCE 的价值 |
|---|---|---|---|
| `AnalyticsService` | `RecordCortexTrajectoryStep` | **完整对话** (user prompt + assistant thinking + response) | ⭐⭐⭐ 主要数据源 |
| `ApiServerService` | `RecordCortexGeneratorMetadata` | 完整 prompt 模板 (`<user_request>...</user_request>`) | ⭐⭐⭐ |
| `ApiServerService` | `RecordTrajectorySegmentAnalytics` | 对话摘要 (model + messages 列表) | ⭐⭐ |
| `ApiServerService` | `RecordStateInitializationData` | 上下文初始化 (包含对话历史) | ⭐⭐ |
| `ApiServerService` | `GetChatMessage` | 聊天消息 (加密 body, 但 response 有 session ID) | ⭐ |
| `ApiServerService` | `RecordCortexExecutionMetadata` | 执行元数据 | ⭐ |
| `ApiServerService` | `CheckUserMessageRateLimit` | 速率限制检查 | 辅助 |
| `ApiServerService` | `GetModelStatuses` | 模型可用性 | 辅助 |
| `ApiServerService` | `GetCliModelConfigs` | 模型配置列表 (52+ 模型) | 辅助 |
| `ApiServerService` | `GetDeepWiki` | DeepWiki 查询 | 辅助 |
| `ApiServerService` | `Ping` | 心跳 | 无 |
| `ApiServerService` | `RecordAsyncTelemetry` | 异步遥测 | 无 |
| `AnalyticsService` | `RecordCortexTrajectory` | 轨迹元数据 | 辅助 |
| `SeatManagementService` | `GetUserStatus` | 用户身份 + 计划 | 辅助 |
| `SeatManagementService` | `GetCliTeamSettings` | 团队设置 + 可用模型列表 | 辅助 |
| `SeatManagementService` | `GetPlanStatus` | 订阅状态 | 辅助 |
| `ProductAnalyticsService` | `RecordAnalyticsEvent` | 产品分析事件 (step type/status) | 辅助 |
| `ProductAnalyticsService` | `BatchRecordAnalyticsEvents` | 批量分析 (CODING_AGENTS_DETECTED 等) | 辅助 |
| `AuthService` | `GetUserJwt` | JWT 刷新 | 无 |

### 2.2 聊天内容提取路径

**最佳数据源**: `RecordCortexTrajectoryStep` (request body)

实测内容示例 (pair_id `81361671c4bb4a7c`):
```
...swe-1-6-slow......4...The user is asking "What is 2+2?" - this is a very 
simple arithmetic question. The answer is 4...This is a trivial question that...
```

**字段映射** (从 protobuf wire format 中可提取):
- **model_name**: `swe-1-6-slow` (SWE-1.6 Slow)
- **user_prompt**: `What is 2+2?`
- **assistant_thinking**: `The user is asking "What is 2+2?" - this is a very simple arithmetic question. The answer is 4`
- **trajectory_id**: UUID (e.g. `f236fb36-147e-41f3-bc22-cd9867e3b29a`)
- **step_index**: 数字 (对话轮次)

**辅助数据源**: `RecordCortexGeneratorMetadata` (request body)
```xml
<user_request>What is 2+2?</user_request>
```

### 2.3 WebSocket 面 (app.devin.ai)

- Windsurf 与 `app.devin.ai` 保持 WebSocket 长连接 (`/api/acp/live?token=...`)
- 这是 Devin 集成的 ACP (Agent Communication Protocol) 通道
- 当前 PCE 的 `direction` CHECK 约束不支持 WebSocket 方向值 → 需要 schema 扩展
- **暂不阻塞 v1.x** — 主要聊天数据已通过 gRPC 捕获

### 2.4 本地持久化 (H / L3g) — 不可行

- `~/.codeium/windsurf/cascade/*.pb` — 17 个文件, 全部加密
- `~/.codeium/windsurf/memories/*.pb` — 加密
- `~/.codeium/windsurf/code_tracker/active/` — 明文文件快照 (代码变更, 非聊天)
- **结论**: L3g 对 Windsurf 聊天面不可行

### 2.5 用户身份信息 (从 GetUserStatus response)

```
username: nbb zst
email: zstnbb@gmail.com
team: devin-team$account-12185097d7ba486b8735896e4961653a0
plan: Free
```

### 2.6 可用模型列表 (从 GetCliTeamSettings response)

```
claude-opus-4-7-medium, claude-opus-4-6-thinking, gpt-5-5-low,
claude-sonnet-4-6-thinking, kimi-k2-6, swe-1-6, ...
```

---

## 3. 与 UCS / plane / lane 的真实映射

| 面 | 层 | 状态 | 备注 |
|---|---|---|---|
| **N (网络)** | **L1 + NODE_EXTRA_CA_CERTS** | ✅ 主路线 | 完整对话捕获 |
| H (本地) | L3g | ❌ 不可行 | 加密存储 |
| M (MCP) | L3f | 🟡 备选 | 官方支持但覆盖面有限 |
| U (UI) | L3d/L4b | 未验证 | 不需要 (L1 已足够) |

---

## 4. 推荐的 case-as-data grid

| Case ID | Title | Trigger |
|---|---|---|
| W01 | vanilla user→assistant | 发送简单 prompt, 验证 user + assistant messages 入库 |
| W02 | model name extraction | 验证 model_name 从 protobuf 正确提取 (swe-1-6-slow 等) |
| W03 | multi-turn conversation | 连续 2-3 轮对话, 验证 session 关联 |
| W04 | trajectory_id session key | 验证同一 trajectory 的所有 step 归入同一 session |
| W05 | thinking/planning capture | 验证 assistant thinking 内容被捕获 |
| W06 | code generation | 发送代码生成 prompt, 验证代码块捕获 |
| W07 | tool use (file edit) | Cascade Write mode 编辑文件, 验证 tool call 捕获 |
| W08 | error handling | 触发 rate limit / error, 验证 error message 捕获 |
| W09 | management plane metadata | 验证 GetUserStatus / GetCliTeamSettings 正确解析 |
| W10 | cancel mid-stream | 中途取消 Cascade 响应, 验证 partial capture |

---

## 5. Open Questions (carry-forward)

- **Q1**: `GetChatMessage` 的 request/response body 是加密的 (不同于其他 endpoint 的明文 protobuf) — 这是否是实际的聊天流式传输通道? 还是只是一个 ID 查询? **不卡 ship** — `RecordCortexTrajectoryStep` 已经包含完整对话。
- **Q2**: WebSocket (`app.devin.ai/api/acp/live`) 是否携带额外的聊天数据? **不卡 ship** — gRPC 已覆盖。
- **Q3**: Windsurf 的 Autocomplete (Tab) 流量走哪个 endpoint? **不卡 v1.x** — 聊天优先。
- **Q4**: `swe-1-6-slow` 是 Windsurf 的默认模型吗? 用户切换模型后 model_name 字段是否正确更新? **阶段 4 验证**。
- **Q5**: Cognition 收购后域名是否会继续迁移? (当前 `server.self-serve.windsurf.com`) **监控项**。

---

## 6. Normalizer 设计建议

### 主 normalizer: `WindsurfCascadeNormalizer`

**输入**: `RecordCortexTrajectoryStep` request body (protobuf wire format)

**提取策略**: 由于没有 .proto schema, 使用 **字符串提取 + 结构化 heuristic**:
1. 提取 model_name: 匹配已知模型名称模式 (`swe-*`, `claude-*`, `gpt-*`, `kimi-*`)
2. 提取 user_prompt: 在 protobuf 中查找 user 文本 (出现在 `<user_request>` 标签内或作为重复字符串)
3. 提取 assistant_response: 查找 thinking/response 文本块
4. 提取 trajectory_id: UUID 格式匹配
5. session_key: `trajectory_id` (同一 trajectory 的所有 step 属于同一 session)

**辅助 normalizer**: 更新现有 `WindsurfManagementNormalizer` 以支持新域名 `server.self-serve.windsurf.com`。

### 配置变更

- `pce_core/config.py` ALLOWED_HOSTS: 添加 `server.self-serve.windsurf.com` + `app.devin.ai` ✅ (已完成)
- `pce_core/normalizer/windsurf_management.py`: 更新 `_HOST` 常量

---

## 7. 安装流程 (用户侧)

用户需要做的事 (将写入 `Docs/install/PCE_WINDSURF_INSTALL.md`):

1. 安装 PCE + mitmproxy CA 证书 (标准 PCE 安装流程)
2. 关闭 Windsurf
3. 设置环境变量:
   - `NODE_EXTRA_CA_CERTS=<path-to-mitmproxy-ca-cert.pem>`
   - `https_proxy=http://127.0.0.1:8080`
4. 启动 PCE proxy (`python -m pce_proxy`)
5. 启动 Windsurf (从设置了环境变量的终端, 或通过 PCE launcher)
6. 正常使用 Cascade — 所有对话自动捕获

**可选**: PCE launcher 可以自动化步骤 2-5 (类似 Cursor launcher)。

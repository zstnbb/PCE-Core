# PCE 工程实施 Spec
**项目名**：Personal Continuity Engine（PCE，个人连续性引擎）  
**文档类型**：工程实施 Spec  
**版本**：v0.1  
**语言**：中文  
**状态**：用于工程化落地与实现对齐

---

## 1. 目标

本 Spec 定义 PCE 的最小可实现版本（Minimum Viable Core）：

> **一个本地、私有、持久化的个人连续性内核 + 一组通用、受限、可回写的外部接口。**

该版本用于验证以下工程命题：

1. 能否在本地稳定维护用户连续性结构；
2. 能否被任意外部模型通过统一接口调用；
3. 能否通过阶段性回写持续改进帮助质量；
4. 能否在不同场景下保持同一用户结构的一致性与私有性。

---

## 2. 范围

### 2.1 In Scope
- 本地 PCE Core
- 本地持久化存储
- 统一接口层（优先 MCP server 形态）
- 观测写入 / 推理调用 / 阶段更新
- 最小信任边界与访问控制
- 一到两个接入适配器（建议：CLI + IDE 或 CLI + Chat interface）

### 2.2 Out of Scope
- 自建社交产品
- 自建角色产品
- 自建复杂前端
- 多用户云同步
- 大规模训练平台
- 深度心理学解释系统
- 多模态长期存储（初版可不做）

---

## 3. 名词定义

### 3.1 PCE
本地私有的个人连续性引擎。

### 3.2 External Model
任何外部 AI 模型或 AI 产品中的模型前端。

### 3.3 Adapter
场景适配层，将某个具体使用场景（聊天、IDE、角色入口）转换为标准事件与标准调用。

### 3.4 Observation
一次结构化观测，来自外部场景中的痕迹整理结果。

### 3.5 Continuity Model
PCE 内部维护的动态个体连续性结构。

### 3.6 Help Posture
某一时刻最合适的帮助姿态。

### 3.7 Probe
一个用来进一步显影结构的轻量探针。

### 3.8 Transition
从当前状态走向更可住状态的过渡建议。

---

## 4. 总体架构

```text
+---------------------------+
| External Scene / Product |
|  (chat / IDE / role /    |
|   any AI front-end)      |
+------------+-------------+
             |
             | Adapter / Skill / Client
             v
+---------------------------+
| Transport / Interface     |
|  - MCP tools              |
|  - request validation     |
|  - response shaping       |
+------------+-------------+
             |
             v
+---------------------------+
| PCE Core                  |
|  - inference engine       |
|  - update engine          |
|  - trust policy           |
|  - continuity model       |
+------------+-------------+
             |
             v
+---------------------------+
| Local Persistent Store    |
|  - events                 |
|  - sessions               |
|  - model snapshots        |
|  - update proposals       |
|  - audit log              |
+---------------------------+
```

---

## 5. 系统分层

### 5.1 Adapter Layer（适配层）
职责：
- 接收不同外部场景的原始输入；
- 规范化为标准 Observation；
- 将外部模型的请求映射为 PCE 接口调用；
- 将 PCE 输出整理成宿主可消费格式。

### 5.2 Interface Layer（接口层）
职责：
- 暴露统一工具接口；
- 做参数校验与权限控制；
- 控制最小暴露；
- 记录调用审计。

推荐形态：
- 本地 MCP server（优先）
- 必要时增加 CLI / HTTP 本地 wrapper

### 5.3 Core Layer（内核层）
职责：
- 持续维护 Continuity Model；
- 进行帮助姿态推理；
- 生成 Probe 与 Transition；
- 对外部回写进行保守更新。

### 5.4 Persistence Layer（持久层）
职责：
- 存 Observation 事件
- 存 Session 摘要
- 存 Continuity Model 快照
- 存 Update Proposal 与应用记录
- 存审计日志

---

## 6. 核心工程原则

### 6.1 Local-first
所有长期结构默认落在本地。  
外部模型不拥有用户模型。

### 6.2 Minimal Exposure
对外暴露最小必要结构，不返回整包私有模型。

### 6.3 Conservative Update
长期结构只接受跨阶段稳定信号的改写。

### 6.4 Separation of Runtime vs Long-term
会话期推理结果 != 长期结构事实。

### 6.5 Explainable Internal State
PCE 内部状态尽量可读、可审计、可回滚。

---

## 7. Continuity Model 数据结构

初版建议采用显式结构 + 分值/置信度混合模型，而非黑盒 embedding-only。

```json
{
  "user_id": "local-user",
  "version": 1,
  "updated_at": "ISO8601",
  "boundaries": [],
  "rhythms": [],
  "anchors": [],
  "load_capacity": {},
  "recovery_modes": [],
  "openness": {},
  "response_patterns": [],
  "confidence": {},
  "model_notes": []
}
```

### 7.1 Boundaries
表示“不能轻易碰”的区域。

字段建议：
- `id`
- `label`
- `description`
- `type`（identity / control / relation / commitment / uncertainty）
- `strength`（0-1）
- `confidence`（0-1）
- `evidence_refs`（Observation IDs）
- `last_confirmed_at`

### 7.2 Rhythms
表示最可住的推进节律。

字段建议：
- `cadence_type`（high-frequency-light / low-frequency-deep / burst-with-recovery / other）
- `notes`
- `confidence`
- `evidence_refs`

### 7.3 Anchors
表示身份连续性的支点。

字段建议：
- `label`
- `description`
- `importance`
- `confidence`
- `evidence_refs`

### 7.4 Load Capacity
表示可承受负荷的估计。

字段建议：
- `uncertainty_tolerance`
- `complexity_tolerance`
- `social_tension_tolerance`
- `commitment_tolerance`
- `cognitive_load_tolerance`
- `confidence`

### 7.5 Recovery Modes
表示恢复方式。

字段建议：
- `mode`
- `description`
- `trigger_context`
- `effectiveness`
- `confidence`

### 7.6 Openness
表示未来开放性。

字段建议：
- `currently_open_paths`
- `currently_false_open_paths`
- `locked_paths`
- `notes`
- `confidence`

### 7.7 Response Patterns
表示不同帮助姿态的真实响应。

字段建议：
- `posture_type`
- `context`
- `observed_effect`
- `effect_strength`
- `confidence`
- `evidence_refs`

---

## 8. Observation 结构

Observation 是写入 PCE 的最小单位之一。  
它不是原始全文，而是结构化痕迹。

```json
{
  "observation_id": "obs_xxx",
  "session_id": "sess_xxx",
  "timestamp": "ISO8601",
  "source_type": "chat|ide|role|cli|other",
  "source_adapter": "string",
  "raw_ref": "optional pointer",
  "signals": {
    "language": [],
    "choices": [],
    "timing": [],
    "reactions": [],
    "meta": []
  },
  "summary": "one-paragraph summary",
  "confidence": 0.0
}
```

### 8.1 language signals
示例：
- 重复出现的自我叙事
- 某种主题的回避
- 某种词汇密集出现
- 强烈但短暂的立场

### 8.2 choice signals
示例：
- 多次删除某类未来
- 倾向低承诺过渡而非直接跳跃
- 总回到某个叙事分支

### 8.3 timing signals
示例：
- 高压场景后才来求助
- 固定节律进入
- 连续多日中断后返回

### 8.4 reaction signals
示例：
- 对收束型建议明显打开
- 对强指令型建议明显僵硬
- 对探索型探针反应更稳定

---

## 9. Session 结构

```json
{
  "session_id": "sess_xxx",
  "started_at": "ISO8601",
  "ended_at": "ISO8601",
  "context_type": "chat|ide|role|other",
  "observations": ["obs_1", "obs_2"],
  "temporary_inferences": [],
  "outcome_summary": "string",
  "update_candidate": true
}
```

Session 是阶段性更新的基本窗口。

---

## 10. 输出结构

### 10.1 Help Posture

```json
{
  "posture_type": "contain|loosen|boundary_protect|transition_design|clarify|stabilize",
  "reasoning_summary": "brief explanation",
  "confidence": 0.0,
  "constraints": [],
  "do_not_do": []
}
```

### 10.2 Probe

```json
{
  "probe_id": "probe_xxx",
  "probe_type": "scenario|choice|split|timeline|reframe",
  "goal": "what this probe tries to surface",
  "prompt_shape": "brief natural-language spec",
  "risk_level": "low|medium|high",
  "expected_signal_types": []
}
```

### 10.3 Transition

```json
{
  "transition_id": "trans_xxx",
  "target_state_label": "string",
  "time_horizon": "short|1-2 weeks|other",
  "bridge_description": "string",
  "why_more_livable": "string",
  "risks": [],
  "stabilizers": []
}
```

---

## 11. 外部接口设计

初版建议提供 4 个核心接口。

### 11.1 `observe`
写入结构化观测。

**用途**：
- 由 Adapter 在会话过程中或会话结束后调用；
- 将原始场景整理为 Observation。

**输入**：
- `session_context`
- `source_type`
- `structured_signals`
- `summary`

**输出**：
- `observation_id`
- `accepted: bool`
- `notes`

---

### 11.2 `infer`
向 PCE 请求当前帮助推理。

**用途**：
- 外部模型需要帮助时调用；
- 不修改长期结构。

**输入**：
- `context_label`
- `current_user_request_summary`
- `optional_recent_signals`
- `desired_outputs`（posture / probe / transition）

**输出**：
- `help_posture`
- `candidate_probes`
- `candidate_transition`
- `minimal_context_constraints`

---

### 11.3 `propose_update`
提交阶段性更新提案，而不是直接改长期结构。

**用途**：
- 会话结束或阶段结束时调用；
- 将临时推理转成待审核更新提案。

**输入**：
- `session_id`
- `candidate_changes`
- `evidence_refs`
- `confidence`
- `reasoning`

**输出**：
- `proposal_id`
- `status`（pending / accepted / rejected / needs_more_evidence）

---

### 11.4 `commit_update`
由更新策略器或人工批准应用提案。

**用途**：
- 改写长期结构；
- 生成模型新版本。

**输入**：
- `proposal_id`
- `policy_mode`（auto_conservative / human_review / scheduled_batch）

**输出**：
- `new_model_version`
- `applied_changes`
- `rejected_changes`

---

## 12. 接口暴露策略

### 12.1 绝不直接返回完整 Continuity Model
原因：
- 过度暴露私有结构
- 容易被外部模型“当真”
- 增加提示注入与操控风险

### 12.2 仅返回当前帮助所需的最小摘要
例如：
- 当前最重要边界
- 当前建议的帮助姿态
- 当前避免触发的方式
- 一个建议的探针方向

### 12.3 外部模型默认无写权限
只能通过 `propose_update` 提交提案。  
真正写入由 PCE 更新策略决定。

---

## 13. 更新机制

PCE 必须分层更新。

### 13.1 Runtime Layer（运行时）
- 针对当前会话的临时推理；
- 不改长期结构；
- 生命周期 = 当前会话。

### 13.2 Session Layer（阶段层）
- 一个完整会话结束后的摘要与提案；
- 可以进入 `propose_update`。

### 13.3 Long-term Layer（长期层）
- 只有经过保守更新流程，才进入长期结构；
- 必须带证据与置信度；
- 应支持版本化和回滚。

---

## 14. 更新策略（保守模式）

初版建议采用规则优先，而非纯模型自动改写。

### 14.1 自动接受的条件
- 同类信号在多个 session 中重复出现；
- 不与当前高置信结构冲突；
- 提案置信度超过阈值；
- 信号来自不同场景而非单一源。

### 14.2 自动拒绝的条件
- 单次情绪高峰
- 明显与当前高置信结构冲突
- 证据不足
- 推理链条不清

### 14.3 需要更多证据
- 中等置信但影响较大的改写；
- 会影响边界、锚点、开放性判断的提案。

---

## 15. 持久化方案

### 15.1 初版建议
- SQLite：结构化元数据与状态快照
- JSON 文件：大型可读对象快照（可选）
- 本地文件目录：审计日志 / adapter 原始引用

### 15.2 建议目录结构

```text
pce/
  config/
    settings.yaml
  data/
    pce.db
    snapshots/
    audit/
  adapters/
  logs/
  exports/
```

### 15.3 SQLite 建议表

- `observations`
- `sessions`
- `continuity_models`
- `update_proposals`
- `applied_updates`
- `tool_calls_audit`
- `adapter_sources`

---

## 16. SQLite 表草案

### 16.1 observations

| field | type |
|---|---|
| observation_id | text pk |
| session_id | text |
| timestamp | text |
| source_type | text |
| source_adapter | text |
| summary | text |
| signals_json | text |
| confidence | real |

### 16.2 sessions

| field | type |
|---|---|
| session_id | text pk |
| started_at | text |
| ended_at | text |
| context_type | text |
| outcome_summary | text |
| update_candidate | integer |

### 16.3 continuity_models

| field | type |
|---|---|
| version_id | integer pk |
| created_at | text |
| model_json | text |
| derived_from_version | integer |
| notes | text |

### 16.4 update_proposals

| field | type |
|---|---|
| proposal_id | text pk |
| session_id | text |
| created_at | text |
| proposal_json | text |
| status | text |
| confidence | real |

### 16.5 tool_calls_audit

| field | type |
|---|---|
| call_id | text pk |
| timestamp | text |
| tool_name | text |
| request_json | text |
| response_summary | text |
| source_adapter | text |

---

## 17. MCP Server 实现建议

### 17.1 推荐工具列表
- `pce_observe`
- `pce_infer`
- `pce_propose_update`
- `pce_commit_update`
- `pce_get_status`
- `pce_export_snapshot`（仅本地诊断）

### 17.2 Tool 设计原则
- 参数显式
- 返回结构稳定
- 默认最小暴露
- 带版本号
- 带安全注释

### 17.3 Host Integration
外部 AI 产品或本地客户端可将 PCE 当作工具来调用，而非将其当作普通知识库。

---

## 18. 适配器设计

初版建议做 2 个 adapter。

### 18.1 CLI Adapter
目的：
- 快速验证端到端流程；
- 手动输入 observation；
- 手动调用 infer / update；
- 便于调试。

### 18.2 IDE / Chat Adapter（二选一先做）
#### IDE Adapter
适合验证：
- coding scene 中的连续性帮助
- 用户在代码节律和求助方式中的痕迹

#### Chat Adapter
适合验证：
- 日常叙事与结构化帮助
- 更接近 Character 壳层的轻入口

---

## 19. 调用流程

### 19.1 Runtime 帮助流程

```text
External Model
   -> summarize current context
   -> call pce_infer
   -> receive posture + probes + transition
   -> generate final help in host product
```

### 19.2 会话结束回写流程

```text
Adapter
   -> collect structured signals
   -> call pce_observe
   -> optionally call pce_propose_update
   -> proposal enters pending or auto-conservative review
```

### 19.3 定时更新流程

```text
Scheduler / Manual Trigger
   -> scan proposals
   -> apply conservative policy
   -> commit accepted changes
   -> create new continuity model version
```

---

## 20. 版本管理

### 20.1 Model Versioning
每次长期结构修改必须产生新版本。

### 20.2 Rollback
必须支持回滚到任意旧版本。

### 20.3 Diffability
应能查看两个版本之间改了什么：
- 新增边界
- 调整节律
- 锚点置信度变化
- 帮助姿态偏好变化

---

## 21. 审计与可解释性

### 21.1 必须审计
- 谁调用了哪些接口
- 返回了什么级别的结构
- 哪些提案改了长期模型
- 改写依据是什么

### 21.2 用户可查看（未来）
未来版本建议给用户一个“查看自己模型”的只读摘要，但不在初版范围内。

---

## 22. 安全与隐私

### 22.1 本地优先
默认不做云存储。

### 22.2 最小出站
任何发送给外部模型的数据必须最小化。

### 22.3 敏感字段保护
内部完整模型默认不出站。

### 22.4 Prompt Injection 风险控制
外部模型不得直接拿 prompt 要求 PCE “暴露全部用户结构”。

### 22.5 数据删除
应支持本地彻底删除：
- 全量删除
- 仅删 observation
- 重置 continuity model

---

## 23. 失败模式与防护

### 23.1 单轮污染
风险：一次高波动会话污染长期结构。  
防护：分层更新 + 提案制 + 保守阈值。

### 23.2 过度暴露
风险：外部模型获得完整私有结构。  
防护：最小暴露原则 + 接口白名单。

### 23.3 假精确
风险：PCE 过早给出“此人的真相”。  
防护：结构中保留置信度与证据引用，不输出终极判断。

### 23.4 过拟合单一场景
风险：coding scene 的信号主导全部结构。  
防护：source diversity 加权；不同场景独立标注。

---

## 24. 评估建议

### 24.1 工程评估
- 接口是否稳定
- 数据是否正确持久化
- 版本与回滚是否可用
- 调用审计是否完整

### 24.2 模型评估
- `infer` 输出是否比无 PCE 更稳定
- 回写是否提升后续帮助质量
- 长期结构是否保持跨场景一致性

### 24.3 用户体验评估（后续）
- 用户是否更少重新解释自己
- 帮助是否更少“对但无用”
- 是否更容易进入可住的下一状态

---

## 25. 初版里程碑

### Milestone 0：Schema 固化
- 完成数据结构定义
- 完成接口参数定义
- 完成更新规则定义

### Milestone 1：本地内核
- SQLite + JSON 持久化
- Continuity Model 读写
- 基础规则引擎

### Milestone 2：MCP Server
- 4 个核心接口可调
- 审计日志可用
- CLI 调试通过

### Milestone 3：一个真实适配器
- Chat 或 IDE 接入一条
- 完成 observe -> infer -> propose_update -> commit_update 全链路

### Milestone 4：阶段性验证
- 累积多 session 数据
- 做首次结构稳定性检查
- 比较带 PCE / 不带 PCE 的帮助差异

---

## 26. 推荐技术栈（初版）

- Python 3.11+
- SQLite
- Pydantic / dataclasses
- FastMCP 或任意 MCP Python SDK
- Typer / Click（CLI）
- pytest
- 本地日志：structlog 或标准 logging

---

## 27. 参考模块划分

```text
pce_core/
  models/
    continuity.py
    observation.py
    posture.py
    probe.py
    transition.py
    proposal.py
  storage/
    sqlite_store.py
    snapshot_store.py
  engine/
    infer_engine.py
    update_engine.py
    policy_engine.py
  adapters/
    cli_adapter.py
    ide_adapter.py
    chat_adapter.py
  interface/
    mcp_server.py
    validators.py
  utils/
    ids.py
    time.py
    audit.py
```

---

## 28. 伪代码示例

### 28.1 infer

```python
def infer(current_context, desired_outputs):
    model = load_latest_continuity_model()

    constraints = extract_minimal_constraints(model, current_context)
    posture = choose_help_posture(model, current_context)
    probes = generate_candidate_probes(model, current_context)
    transition = design_transition(model, current_context)

    return {
        "help_posture": posture,
        "candidate_probes": probes if "probe" in desired_outputs else [],
        "candidate_transition": transition if "transition" in desired_outputs else None,
        "minimal_context_constraints": constraints,
    }
```

### 28.2 propose update

```python
def propose_update(session_id, candidate_changes, evidence_refs, confidence):
    proposal = {
        "proposal_id": new_id(),
        "session_id": session_id,
        "candidate_changes": candidate_changes,
        "evidence_refs": evidence_refs,
        "confidence": confidence,
        "status": "pending"
    }
    save_proposal(proposal)
    return proposal
```

### 28.3 commit update

```python
def commit_update(proposal_id):
    proposal = load_proposal(proposal_id)
    current_model = load_latest_continuity_model()

    decision = conservative_policy(current_model, proposal)

    if not decision.accepted:
        mark_rejected(proposal_id, decision.reason)
        return decision

    new_model = apply_changes(current_model, proposal.candidate_changes)
    save_new_model_version(new_model)
    mark_accepted(proposal_id)

    return {
        "accepted": True,
        "new_version": new_model["version"]
    }
```

---

## 29. 最小验证闭环

初版只需要验证这一条链成立：

```text
真实使用场景
 -> 适配器整理痕迹
 -> observe 写入
 -> infer 提供帮助姿态
 -> 外部模型按姿态帮助用户
 -> 会话结束回写
 -> propose_update / commit_update
 -> 下次帮助更贴合
```

如果这条链成立，体系就成立。

---

## 30. 最终收束

工程上，不要把 PCE 做成“更会聊天的记忆系统”。  
PCE 必须被实现为：

> **一个本地、私有、版本化、保守更新的个人连续性内核。**

它的外部世界可以变化，但它内部维护的始终是同一件事：

> **这个人如何持续成为自己。**

# 第 5 章：Multi-Agent 与 Agents

> 本章导读：Codex CLI 不仅能“一个模型会话完成任务”，还支持一个 root agent 根据需要 spawn 多个 sub-agent，让它们并行或协作完成复杂任务。读完本章，你会理解 Codex 中 Agent 与 Thread 的关系、`AgentControl` 如何管理整个 agent 树、Agent 之间如何通信、V1 与 V2 多 Agent 模式的差异，以及并发与预算控制机制。

## 5.1 本章要解决的问题

当用户交给 Codex 一个复杂任务时，单线程单模型的执行方式会遇到瓶颈：

- 一个 Agent 同时只能做一件事，无法并行探索多个方案；
- 长任务容易让上下文窗口爆炸；
- 不同子任务需要不同角色（如“安全审查员”、“测试工程师”、“文档作者”）；
- 子任务失败不应该拖垮整个主任务。

Multi-Agent 系统要解决：

1. **如何表示 Agent**：Agent 是独立进程还是线程？如何寻址？
2. **如何创建 Agent**：spawn 时如何继承配置、历史、权限？
3. **Agent 如何通信**：消息、任务、结果的传递机制。
4. **如何限制资源**：并发数、层级深度、token budget。
5. **不同版本协议**：V1 与 V2 有什么区别？

## 5.2 核心设计：Agent ≈ Thread

在 Codex CLI 中，**一个 Agent 本质上就是一个 `CodexThread` / `Session`**。它与普通用户会话的区别在于：

- `SessionSource` 是 `SubAgent(ThreadSpawn { depth, ... })` 而不是 `Cli` / `Vscode`；
- 它通过 `AgentControl` 注册到同一个 session 树中；
- 它可以被父 Agent 通过工具调用 `spawn_agent`、`send_message`、`wait_agent` 等管理。

这种设计的好处是：

- **复用现有基础设施**：Agent 拥有与普通会话完全相同的 Session、Turn、Tool 循环；
- **统一持久化**：Agent 的历史同样走 `codex-rollout` 和 `codex-thread-store`；
- **统一沙箱**：Agent 的工具执行同样受 `PermissionProfile` 和 `exec-server` 约束。

## 5.3 Agent 树与 AgentPath

Codex 用 `AgentPath` 给每个 Agent 一个类文件系统的路径，便于寻址和显示：

```rust
pub struct AgentPath(String);

impl AgentPath {
    pub const ROOT: &str = "/root";
    pub const MORPHEUS: &str = "/morpheus";
    // ...
}
```

- `/root`：用户直接交互的根 Agent；
- `/root/sub_1`：root  spawn 的第一个子 Agent；
- `/root/sub_1/sub_2`：子 Agent 再 spawn 的孙 Agent。

`AgentPath` 支持 `resolve("../sub_2")` 等相对引用，让模型可以用类似路径的方式指定通信目标。

## 5.4 AgentControl：多 Agent 控制中心

`AgentControl` 在 `codex-rs/core/src/agent/control.rs` 中，是 Multi-Agent 系统的核心控制面。每个 `Session` 的 `SessionServices` 都持有一个 `AgentControl` 句柄，同一个根 session 树下的所有 Agent 共享同一个 `AgentControl`。

```rust
pub(crate) struct AgentControl {
    session_id: SessionId,
    manager: Weak<ThreadManagerState>,
    state: Arc<AgentRegistry>,
    v2_residency: Arc<V2Residency>,
    agent_execution_limiter: Arc<AgentExecutionLimiter>,
    rollout_budget: Arc<RolloutBudget>,
}
```

`AgentControl` 提供的关键能力：

| 方法 | 作用 |
|------|------|
| `spawn_agent` / `spawn_agent_with_metadata` | 创建子 Agent |
| `send_input` | 向指定 Agent 发送用户输入 |
| `send_inter_agent_communication` | 向指定 Agent 发送消息/任务/结果 |
| `interrupt_agent` | 中断指定 Agent |
| `get_status` / `subscribe_status` | 查询/订阅 Agent 状态 |
| `resolve_agent_reference` | 把路径引用解析为 `ThreadId` |
| `list_live_agent_subtree_thread_ids` | 列出某 Agent 的子树 |
| `register_session_root` | 注册当前线程为 root |

`manager` 用 `Weak<ThreadManagerState>` 是为了避免循环引用：`ThreadManagerState → CodexThread → Session → SessionServices → AgentControl → ThreadManagerState`。

## 5.5 AgentRegistry：Agent 树状态

`AgentRegistry` 在 `codex-rs/core/src/agent/registry.rs` 中，维护当前 session 树中所有 Agent 的元数据：

```rust
pub(crate) struct AgentRegistry {
    active_agents: Mutex<ActiveAgents>,
    total_count: AtomicUsize,
}

struct ActiveAgents {
    agent_tree: HashMap<String, AgentMetadata>,
    used_agent_nicknames: HashSet<String>,
    nickname_reset_count: usize,
}
```

`AgentMetadata` 包含：

- `agent_id`: `ThreadId`
- `agent_path`: `AgentPath`
- `agent_nickname`: 显示用的昵称（如 "coder"、"coder the 2nd"）
- `agent_role`: 角色名

`AgentRegistry` 负责：

1. **注册 root thread**：`register_root_thread`
2. **分配 agent path**：`reserve_agent_path`
3. **分配 agent nickname**：`reserve_agent_nickname`
4. **注册 spawned thread**：`register_spawned_thread`
5. **释放线程**：`release_spawned_thread`
6. **限制总数**：`reserve_spawn_slot` / `try_increment_spawned`

### 5.5.1 Agent 数量与层级限制

```rust
pub(crate) fn exceeds_thread_spawn_depth_limit(depth: i32, max_depth: i32) -> bool {
    depth > max_depth
}
```

每次 spawn 时，depth 加 1；如果超过 `max_depth`，则拒绝。Agent 总数也受 `agent_max_threads` 限制。

## 5.6 Agent 状态机

Agent 的状态由 `AgentStatus` 定义（位于 `codex-rs/protocol/src/protocol.rs`）：

```rust
pub enum AgentStatus {
    PendingInit,
    Running,
    Interrupted,
    Completed(Option<String>),
    Errored(String),
    Shutdown,
    NotFound,
}
```

状态转换由 `codex-rs/core/src/agent/status.rs` 中的 `agent_status_from_event` 从 Session 发出的事件推导：

| 事件 | 状态 |
|------|------|
| `TurnStarted` | `Running` |
| `TurnComplete` | `Completed(last_agent_message)` |
| `TurnAborted(Interrupted / BudgetLimited)` | `Interrupted` |
| `TurnAborted(其他原因)` | `Errored(...)` |
| `Error` | `Errored(message)` |
| `ShutdownComplete` | `Shutdown` |

`is_final` 函数判断状态是否为终态（非 PendingInit/Running/Interrupted）。

## 5.7 Agent 角色：Agent Roles

Agent 在 spawn 时可以指定 `agent_type`（角色），系统会加载对应的 role config 并叠加到当前配置上。角色定义在：

- 内置角色：`codex-rs/core/src/agent/role.rs` 中的 `built_in` 模块
- 用户自定义：`config.toml` 中的 `agent_roles`

`apply_role_to_config`（`codex-rs/core/src/agent/role.rs`）会：

1. 解析角色配置文件（TOML）；
2. 把角色层以高优先级插入 `ConfigLayerStack`；
3. 保留当前模型的 provider、service_tier、model、reasoning_effort（除非角色显式覆盖）；
4. 重新构建 `Config`。

角色让同一个底层模型在不同 Agent 中表现出不同行为：有的专注于代码，有的专注于审查，有的专注于测试。

## 5.8 Spawn 一个 Agent

`spawn_agent_internal`（`codex-rs/core/src/agent/control/spawn.rs`）是创建子 Agent 的核心：

1. **确定 MultiAgentVersion**：根据配置、session source、父线程等决定用 V1 还是 V2。
2. **检查执行容量**：V2 子 Agent 受 `agent_execution_limiter` 限制。
3. **预留 V2 residency slot**（如果是 V2 常驻模式）。
4. **构建 `SessionSpawnArgs`**：
   - 继承父 Agent 的 config；
   - 应用 role；
   - 设置 `parent_thread_id`、`session_source = SubAgent(...)`、`agent_control` 共享；
   - 选择历史 fork 模式（full history / last N turns / none）。
5. **调用 `ThreadManagerState::spawn_thread`**：创建新的 `CodexThread` / `Session`。
6. **注册到 AgentRegistry**：分配 path、nickname、metadata。
7. **发送初始输入**：
   - V1：通常是 `UserInput`；
   - V2：通常是 `InterAgentCommunication`（包含任务描述）。

### 5.8.1 历史继承

子 Agent 可以选择继承父 Agent 的历史：

```rust
pub(crate) enum SpawnAgentForkMode {
    FullHistory,
    LastNTurns(usize),
}
```

- `FullHistory`：子 Agent 看到完整上下文；
- `LastNTurns(n)`：只看最近 n 轮；
- 不传则不看历史（V2 常用）。

## 5.9 Agent 间通信

Agent 间通信通过 `InterAgentCommunication` 协议消息实现（`codex-rs/protocol/src/protocol.rs`）：

```rust
pub struct InterAgentCommunication {
    pub id: Option<ResponseItemId>,
    pub author: AgentPath,
    pub recipient: AgentPath,
    pub other_recipients: Vec<AgentPath>,
    pub content: String,
    pub encrypted_content: Option<String>,
    pub trigger_turn: bool,
}
```

### 5.9.1 通信路径

```
父 Agent 调用 send_message / followup_task / spawn_agent
    │
    ▼
multi_agents_v2 handler 构造 InterAgentCommunication
    │
    ▼
AgentControl::send_inter_agent_communication
    │
    ▼
ThreadManagerState::send_op(agent_id, Op::InterAgentCommunication { communication })
    │
    ▼
目标 Session 的 submission_loop 收到 Op::InterAgentCommunication
    │
    ▼
inter_agent_communication(sess, sub_id, communication)
    │
    ├─ 把 communication 放入 input_queue
    └─ 如果 trigger_turn == true，启动新 turn
```

### 5.9.2 通信类型

从 `AgentCommunicationKind` 看，Codex 区分四种通信语义：

- `Spawn`：创建 Agent 时携带的初始任务；
- `Message`：不触发新 turn 的消息（或 trigger_turn=false 时）；
- `Followup`：给已有 Agent 新任务，触发新 turn；
- `Result`：子 Agent 返回给父 Agent 的最终结果。

## 5.10 V1 与 V2 多 Agent 模式

Codex 实现了两套多 Agent 工具集：

### 5.10.1 V1（`agents` namespace）

位于 `codex-rs/core/src/tools/handlers/multi_agents/`，工具名带 `agents.` namespace：

- `agents.spawn_agent`
- `agents.send_input`
- `agents.wait_agent`
- `agents.resume_agent`
- `agents.close_agent`

V1 的特点是：

- 工具在 `agents` namespace 下；
- 通信语义相对简单；
- 子 Agent 默认更像“独立线程”。

### 5.10.2 V2（扁平命名空间）

位于 `codex-rs/core/src/tools/handlers/multi_agents_v2/`，工具名是扁平的：

- `spawn_agent`
- `send_message`
- `followup_task`
- `wait_agent`
- `list_agents`
- `interrupt_agent`

V2 的特点是：

- 工具名无 namespace，更自然；
- 支持 `followup_task`：给已有 Agent 追加任务并触发 turn；
- 支持 `send_message`：不触发 turn 的轻量通信；
- 支持 `list_agents`：列出当前活跃 Agent；
- 有更强的并发控制（`AgentExecutionLimiter`）；
- 支持 V2 residency：子 Agent 可以是“常驻”的，父 Agent 离开后仍可被后续会话加载。

### 5.10.3 版本选择

`effective_multi_agent_version_for_spawn` 根据配置中的 `multi_agent_version`、feature flag、父 Agent 版本等决定使用 V1 还是 V2。

## 5.11 并发与预算控制

### 5.11.1 并发限制

`AgentExecutionLimiter`（`codex-rs/core/src/agent/control/execution.rs`）控制 V2 子 Agent 的并发数：

```rust
pub(super) struct AgentExecutionLimiter {
    active: AtomicUsize,
    max_threads: OnceLock<usize>,
}
```

每次子 Agent 开始一个 turn 时，会检查是否还有容量；执行时持有 `AgentExecutionGuard`，Drop 时释放。

### 5.11.2 Rollout Budget

`AgentControl` 持有 `Arc<RolloutBudget>`，用于控制整个 session 树的 token/turn 预算。所有子 Agent 共享同一个 budget。

### 5.11.3 深度限制

`next_thread_spawn_depth` 和 `exceeds_thread_spawn_depth_limit` 控制 Agent 树的最大深度，避免无限递归 spawn。

## 5.12 工具层面的 Multi-Agent

Multi-Agent 能力最终通过工具暴露给模型。在 `spec_plan.rs` 中：

```rust
fn add_collaboration_tools(context: &CoreToolPlanContext<'_>, planned_tools: &mut PlannedTools) {
    // 根据 multi_agent_version 注册 V1 或 V2 工具
}
```

模型像调用普通工具一样调用 `spawn_agent`、`send_message`、`wait_agent` 等，handler 内部再调用 `AgentControl`。

## 5.13 设计取舍

| 取舍 | 选择 | 替代方案 | 原因 |
|------|------|----------|------|
| Agent = Thread | 复用 Session/CodexThread | 独立进程 | 复用基础设施，统一持久化/沙箱 |
| 共享 AgentControl | 同 session 树共享 | 每个 Agent 独立控制 | 统一 budget、并发、注册表 |
| Weak 引用 manager | 避免循环引用 | Arc 强引用 | 防止内存泄漏和影子持久化 |
| AgentPath 寻址 | 类文件系统路径 | 仅用 ThreadId | 对模型更直观，支持相对引用 |
| V1 + V2 并存 | 两套工具集 | 只保留一套 | 平滑演进，兼容旧配置 |
| 执行容量限制只对 V2 | V2 子 Agent 限并发 | 所有版本都限 | V2 更强调大规模协作，需要限制 |
| 角色叠加 config | role 作为高优先级 config 层 | 硬编码角色行为 | 灵活，用户可自定义 |

## 5.14 关键实现入口

| 概念 | 主要文件 | 说明 |
|------|----------|------|
| AgentControl | `codex-rs/core/src/agent/control.rs` | 多 Agent 控制面 |
| Agent spawn | `codex-rs/core/src/agent/control/spawn.rs` | `spawn_agent_internal` |
| Agent 执行限制 | `codex-rs/core/src/agent/control/execution.rs` | `AgentExecutionLimiter` |
| V2 Residency | `codex-rs/core/src/agent/control/residency.rs` | V2 常驻 Agent 管理 |
| AgentRegistry | `codex-rs/core/src/agent/registry.rs` | Agent 树元数据 |
| AgentPath | `codex-rs/protocol/src/agent_path.rs` | Agent 路径 |
| AgentStatus | `codex-rs/protocol/src/protocol.rs` | Agent 状态枚举 |
| 状态推导 | `codex-rs/core/src/agent/status.rs` | `agent_status_from_event` |
| Agent 角色 | `codex-rs/core/src/agent/role.rs` | `apply_role_to_config` |
| 内置角色 | `codex-rs/core/src/agent/role.rs` | 内置 agent role 配置 |
| Agent 间通信 | `codex-rs/core/src/agent_communication.rs` | 通信日志与上下文 |
| InterAgentCommunication | `codex-rs/protocol/src/protocol.rs` | 通信协议消息 |
| V1 多 Agent 工具 | `codex-rs/core/src/tools/handlers/multi_agents/` | spawn/send/wait/resume/close |
| V2 多 Agent 工具 | `codex-rs/core/src/tools/handlers/multi_agents_v2.rs` | spawn/send_message/followup_task/wait/list/interrupt |
| 工具注册 | `codex-rs/core/src/tools/spec_plan.rs` | `add_collaboration_tools` |
| Session 处理通信 | `codex-rs/core/src/session/handlers.rs` | `inter_agent_communication` |

## 5.15 小结

Codex CLI 的 Multi-Agent 系统建立在“**Agent ≈ Thread**”的简洁抽象之上：

- `AgentControl` 是同 session 树所有 Agent 的共享控制面；
- `AgentRegistry` 维护 Agent 树、路径、昵称、数量限制；
- `AgentPath` 提供直观的寻址方式；
- `AgentStatus` 从 Session 事件推导，统一状态视图；
- Agent 角色通过 config layer 叠加实现；
- `InterAgentCommunication` 是 Agent 间通信的协议原语；
- V1 和 V2 两套工具集满足不同协作语义；
- 并发、深度、budget 三层限制保证系统不会失控。

这种设计让 Codex 既能作为单 Agent 工具使用，也能在复杂任务中展开成一棵协作的 Agent 树，而核心 Agent Loop 几乎不需要感知 Multi-Agent 的存在。

下一章（执行沙箱与权限）会深入讨论 Agent 执行命令和文件操作时的安全隔离机制。

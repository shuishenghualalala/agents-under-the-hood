# 第 5 章：Multi-Agent 与 Agents

> 本章导读：Codex CLI 不仅能“一个模型会话完成任务”，还支持一个 root agent 根据需要 spawn 多个 sub-agent，让它们并行或协作完成复杂任务。读完本章，你会理解 Codex 中 Agent 与 Thread 的关系、`AgentControl` 如何管理整个 agent 树、子 Agent 的 spawn 全链路（配置继承、历史 fork、昵称分配）、Agent 之间如何通信与等待结果、V1 与 V2 两套多 Agent 协议的差异，以及并发、深度、驻留（residency）三层资源控制机制。

## 5.1 本章要解决的问题

当用户交给 Codex 一个复杂任务时，单线程单模型的执行方式会遇到瓶颈：

- 一个 Agent 同时只能做一件事，无法并行探索多个方案；
- 长任务容易让上下文窗口爆炸；
- 不同子任务需要不同角色（如“安全审查员”、“测试工程师”、“文档作者”）；
- 子任务失败不应该拖垮整个主任务。

Multi-Agent 系统要解决：

1. **如何表示 Agent**：Agent 是独立进程还是线程？如何寻址？
2. **如何创建 Agent**：spawn 时如何继承配置、历史、权限？
3. **Agent 如何通信**：消息、任务、结果的传递机制；父 Agent 如何等待/收集结果。
4. **如何限制资源**：并发数、层级深度、token budget、常驻内存。
5. **不同版本协议**：V1 与 V2 有什么区别？

## 5.2 核心设计：Agent ≈ Thread

在 Codex CLI 中，**一个 Agent 本质上就是一个 `CodexThread` / `Session`**。它与普通用户会话的区别在于：

- `SessionSource` 是 `SubAgent(ThreadSpawn { parent_thread_id, depth, agent_path, agent_nickname, agent_role })` 而不是 `Cli` / `Vscode`；
- 它通过共享的 `AgentControl` 注册到同一个 session 树中；
- 它可以被父 Agent 通过工具调用 `spawn_agent`、`send_message`、`wait_agent` 等管理；
- 它同样可以再 spawn 自己的 sub-agent（受深度与并发限制）。

这种设计的好处是：

- **复用现有基础设施**：Agent 拥有与普通会话完全相同的 Session、Turn、Tool 循环；
- **统一持久化**：Agent 的历史同样走 rollout / thread-store 持久化，V2 agent 还会持久化 spawn 边（spawn edge），供后续会话恢复整棵树；
- **统一沙箱**：Agent 的工具执行同样受权限体系约束。

另外有一个值得强调的语义（也是 V2 usage hint 中向模型明说的）：**所有 Agent 共享同一个容器、文件系统和当前工作目录**。一个 Agent 的文件编辑对其他所有 Agent 立即可见——这是协作模型的基础，而不是每个 Agent 一份隔离工作区。

## 5.3 Agent 树与 AgentPath

Codex 用 `AgentPath` 给每个 Agent 一个类文件系统的路径。定义在 `codex-rs/protocol/src/agent_path.rs`：

```rust
pub struct AgentPath(String);

impl AgentPath {
    pub const ROOT: &str = "/root";
    pub const MORPHEUS: &str = "/morpheus";
    // ...
}
```

- `/root`：用户直接交互的根 Agent；
- `/root/task_1`：root spawn 的第一个子 Agent（用 task name 命名）；
- `/root/task_1/task_2`：子 Agent 再 spawn 的孙 Agent。

### 5.3.1 路径规则是硬校验

`AgentPath` 不是简单字符串拼接，而是一组**强制校验**：

| 规则 | 说明 |
|------|------|
| 绝对路径必须以 `/root` 开头 | 否则拒绝（另一个合法值是特殊的 `/morpheus`，用于 root 子树之外的系统级命名空间——从代码结构推断是云端编排类 Agent 的预留位） |
| 不能以 `/` 结尾 | 尾斜杠非法 |
| 每段名字只能用小写字母、数字、下划线 | `BadName`、"task 3" 都会被拒 |
| `root`、`.`、`..` 是保留段 | 不能作为任何一段的名字 |

尤其要注意最后一条与直觉的差异：`resolve("../sibling")` 会直接报错 "agent_name `..` is reserved"。**相对引用只能向下挂载**（join 到自己下面），不能向上逃逸或横跳；跨分支通信要用对方的完整规范名（canonical name）。这个限制把 Agent 树的引用结构约束成了真正的树，避免任意图状寻址带来的生命周期混乱。

### 5.3.2 join 与 resolve

- `join(agent_name)`：在当前路径后追加一段，spawn 时用它构造子 Agent 路径；
- `resolve(reference)`：解析模型给出的目标引用。传 "/root/x" 这类绝对路径按绝对路径处理；其余当作相对路径拼到当前路径下。

配套地，`name()` 返回路径最后一段（如 `/root/task_1` 的 `task_1`），工具输出和 TUI 展示都靠它生成人类可读的短名。父 Agent 和子 Agent 可以互换使用 `task_3` 或 `/root/task1/task_3` 称呼同一个子 Agent，但别的分支下的 `/root/task2/task_3` 只能用规范全名访问（这也是 V2 spawn 工具描述里明确教给模型的规则）。

## 5.4 AgentControl：多 Agent 控制中心

`AgentControl` 在 `codex-rs/core/src/agent/control.rs` 中，是 Multi-Agent 系统的控制面。它**每个 root session 树至多创建一份**，由根 Session 构造，随后克隆给该树下每一个 sub-agent 的 `SessionServices`——注册表的作用域因此被限定在一棵 root 树内，而不是整个 `ThreadManager`。

```rust
pub(crate) struct AgentControl {
    /// 整棵 agent 树共享同一个 session id
    session_id: SessionId,
    /// Weak 回指全局线程表，打断循环引用
    manager: Weak<ThreadManagerState>,
    /// 构造时捕获，使子代理沿用 manager 的线程 id 分配策略
    thread_id_generator: ThreadIdGenerator,
    state: Arc<AgentRegistry>,
    v2_residency: Arc<V2Residency>,
    agent_execution_limiter: Arc<AgentExecutionLimiter>,
    /// root 与所有子代理共享的预算
    rollout_budget: Arc<RolloutBudget>,
}
```

`manager` 用 `Weak<ThreadManagerState>` 是为了避免循环引用与“影子持久化”：`ThreadManagerState → CodexThread → Session → SessionServices → AgentControl → ThreadManagerState`。`upgrade()` 失败（manager 已释放）时所有操作统一返回 "thread manager dropped"，handler 层再把这类错误翻译成模型可读的 "collab manager unavailable"。

`AgentControl` 提供的关键能力：

| 方法 | 作用 |
|------|------|
| `spawn_agent` / `spawn_agent_with_metadata` | 创建子 Agent，初始输入为用户输入（前者仅测试用例直接使用） |
| `spawn_agent_with_communication` | 创建子 Agent，初始输入为 Inter-Agent 通信（V2 主路径） |
| `send_input` | 向指定 Agent 发送用户输入；底层走 `start_or_steer_turn`，能 start 新 turn 也能 steer 进行中的 turn |
| `send_inter_agent_communication` | 发送消息/任务/结果；`trigger_turn=true` 时先做执行容量检查 |
| `interrupt_agent` | 向指定 Agent 发送 `Op::Interrupt` |
| `get_status` / `subscribe_status` | 查询/订阅 Agent 状态（watch channel） |
| `resolve_agent_reference` | 把路径引用解析为 `ThreadId` |
| `list_agents` / `list_live_agent_subtree_thread_ids` | 列出活跃 Agent / 某节点的活体子树 |
| `register_session_root` | 把无父线程注册为 `/root` |
| `close_agent` / `shutdown_live_agent` / `shutdown_agent_tree` | 关闭单个 Agent 或连同其全部活体后代 |

几个值得注意的实现细节：

- **steer 时的 submission_id**：MAv1 对外暴露的是不透明的 `submission_id`。当消息被 steer 进进行中的 turn 时，底层 API 返回的是当前活动 turn id；为了让每次调用对模型可见的 id 保持唯一，控制层会用一个新的 UUIDv7 补齐（没有为此专门加回执类型）。
- **自愈式清理**：`handle_thread_request_result` 发现请求因 `InternalAgentDied` 失败时，顺手把它从线程表移除、清掉 residency 记录、释放注册表槽位——死掉的 Agent 不会占着名额。
- **子环境继承**：spawn 前从父线程快照两样运行期资产——`TurnEnvironmentSnapshot`（环境选择）与共享的 `ExecPolicyManager`（命令执行策略，仅在父子策略兼容时复用同一实例）；residency 驱逐时会把这些保存进注册表，重载时原样恢复。

### 5.4.1 完成通知：completion watcher

非 V2 的子 Agent 在 spawn 完成后会启动一个 detached watcher（`maybe_start_completion_watcher`）：订阅该线程的状态流直到进入终态，然后向父线程注入一条格式化好的完成通知（`format_subagent_notification_message`），以 `inject_user_message_without_turn` 的方式进入父上下文，不触发新 turn。

V2 不走这条路注入 user message：watcher 发现子 Agent 带 agent path 且走 V2 时，改发一条 `trigger_turn=false` 的 FINAL_ANSWER 类型 Inter-Agent 通信回父节点（见 5.9.3），投递交给统一的邮箱队列调度。

## 5.5 AgentRegistry：Agent 树状态

`AgentRegistry` 在 `codex-rs/core/src/agent/registry.rs` 中，维护当前 session 树中所有 Agent 的元数据：

```rust
pub(crate) struct AgentRegistry {
    active_agents: Mutex<ActiveAgents>,
    total_count: AtomicUsize,
}

struct ActiveAgents {
    agent_tree: HashMap<String, AgentMetadata>,       // path -> metadata
    thread_paths: HashMap<ThreadId, RegisteredAgent>, // thread -> path（+ 被驱逐环境）
    used_agent_nicknames: HashSet<String>,
    nickname_reset_count: usize,
}

pub(crate) struct AgentMetadata {
    pub(crate) agent_id: Option<ThreadId>,
    pub(crate) agent_path: Option<AgentPath>,
    pub(crate) agent_nickname: Option<String>,
    pub(crate) agent_role: Option<String>,
}
```

注意它是**双索引**：path 索引用于寻址（`agent_id_for_path`），thread id 索引用于反查元数据和保存“被驱逐环境”。重新注册同一线程或同一路径时会清理旧条目，保证两个索引不漂移。

### 5.5.1 SpawnReservation：原子预留 + 失败自动回滚

spawn 不是一把梭写进注册表，而是走两阶段：

1. `reserve_spawn_slot(max_threads)`：CAS 递增 `total_count`（超限返回 `AgentLimitReached`），拿到一个 `SpawnReservation`；
2. 在 reservation 上继续预留 agent path、昵称；
3. 线程真正建好后 `commit(metadata)` 正式登记。

关键是 `SpawnReservation` 实现了 `Drop`：若持有期间发生错误而没有 commit，Drop 会撤销 path 占位并把计数减回去。于是 spawn 流程中间任何一步失败都不会留下半截注册状态，也不会泄漏并发名额。

### 5.5.2 昵称池：101 位科学家

昵称池来自 `codex-rs/core/src/agent/agent_names.txt` 编译期内嵌的 101 个科学家名（Euclid、Archimedes、Hypatia、Galileo……）：

- 角色可以通过 role config 的 `nickname_candidates` 提供自己的候选名单，否则用默认池；
- 分配时从未占用集合里随机挑一个；全耗尽则清空已用集合、`nickname_reset_count += 1`，进入第二轮编号（并打一个 `codex.multi_agent.nickname_pool_reset` 指标）；
- 第二轮起昵称带序号后缀：`Archimedes the 2nd`、`Euclid the 13th`（11–13 固定 th，其余按个位数 st/nd/rd/th）。

昵称是纯展示用途；寻址永远走 agent path / thread id。

### 5.5.3 Agent 数量与层级限制

数量：`reserve_spawn_slot` 的上限按协议版本取值（见 5.11 配置矩阵）。到达上限的新 spawn 直接得到 `AgentLimitReached`。

层级：`DEFAULT_AGENT_MAX_DEPTH = 1`（core/src/config/mod.rs）。也就是说默认情况下**只有 root 能 spawn 一层**，除非显式调高 `config.agent_max_depth`：

```rust
pub(crate) fn next_thread_spawn_depth(session_source: &SessionSource) -> i32;
pub(crate) fn exceeds_thread_spawn_depth_limit(depth: i32, max_depth: i32) -> bool; // depth > max_depth
```

深度检查有两道闸门（见 5.12 与 5.10.2）：工具是否暴露给模型时查一次（spec_plan.rs），实际执行 spawn/resume handler 时再查一次。

## 5.6 Agent 状态机

Agent 的状态由 `AgentStatus` 定义（位于 `codex-rs/protocol/src/protocol.rs`）：

```rust
pub enum AgentStatus {
    PendingInit,
    Running,
    Interrupted,
    Completed(Option<String>),   // Completed 携带 last_agent_message
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

`is_final` 判定终态：除 `PendingInit`、`Running`、`Interrupted` 外都是终态。注意 `Interrupted` 不算终态——被中断的 Agent 还可能收到新任务继续跑。这与 V2 的 residency 驱逐条件（只驱逐“Completed/Errored/Interrupted 且 mailbox 清空且无活动 turn”的 Agent）是一致的。

工具层面还有一个更窄的线缆视图（wire schema，出现在 spawn/wait/list 等 output schema 里）：五个字符串枚举 `pending_init` / `running` / `interrupted` / `shutdown` / `not_found` 加上 `{completed: string|null}`、`{errored: string}` 两个对象形态。Protocol 枚举的内部细节不会全量透出给模型。

## 5.7 Agent 角色（Agent Roles）

角色系统让同一个底层模型在不同 Agent 中表现出不同行为。定义位置：

- 内置角色：随 codex 分发的 role config 文件（`codex-rs/core/src/agent/role.rs` 解析加载）；
- 用户自定义：config.toml 中的 `[agent_roles]`（解析逻辑在 `codex-rs/core/src/config/agent_roles.rs`）。

未指定 `agent_type` 时使用默认角色 `DEFAULT_ROLE_NAME = "default"`。

### 5.7.1 角色是“受限叠加层”，不是自由改配置

`apply_role_to_config`（`codex-rs/core/src/agent/role.rs`）的工作方式：

1. 按 role name 解析出 `AgentRoleConfig`（找不到直接报 unknown agent_type）；
2. 加载角色的 TOML 层，反序列化为一组**白名单化的覆盖项**：developer_instructions、model、model_reasoning_effort、model_reasoning_summary、model_verbosity、personality、service_tier、features、skills；
3. features 只允许做减法——能关掉的只有 ShellTool / Apps / Personality / Plugins / MemoryTool / RequestPermissionsTool 这几类能力开关；
4. skills 也只能裁剪：保留其中要求禁用的项，忽略 max_context_tokens 之类无关字段；
5. 以配置层（layer）的形式压入 ConfigLayerStack，保持 layer 体系的一致性。

源码注释点明了设计边界：**“Roles may customize the child or reduce its capabilities, but never replace the parent session's authority.”** 角色可以收窄子 Agent 的能力（比如做一个只读分析员），但不能替父会话扩大权限。加载角色内部出错时对外统一映射成模糊的 "agent type is currently not available"，避免向模型泄露配置细节。

### 5.7.2 角色与全量 fork 的互斥约束

V1 有一个明确的组合规则（`reject_full_fork_agent_type_override`）：全量历史 fork 的子 Agent 继承父 Agent 的角色，**不允许同时传 `agent_type` 覆盖**——否则等价于让子 Agent 冒充另一个角色去读父的全部历史，工具调用会被直接拒绝。

### 5.7.3 重载时的角色再现

V2 resident agent 从持久化存储重新载入（`ensure_v2_agent_loaded`）时要重新应用角色，但用“先存后还”的方式保住运行期状态：approval policy、cwd、permission profile snapshot 先取出现值，叠完角色层后再原样写回。这样重载不会让角色的静态覆盖顶掉会话的动态审批决定。

## 5.8 Spawn 一个 Agent

V2 的入口链路从工具 handler 开始：`multi_agents_v2/spawn.rs` 的 `handle_spawn_agent` → `AgentControl::spawn_agent_with_communication` → `spawn_agent_internal`（`codex-rs/core/src/agent/control/spawn.rs`）。

### 5.8.1 参数解析与校验（handler 层）

`SpawnAgentArgs` 是 deny_unknown_fields 的严格反序列化：message、task_name 必填；可选 agent_type、model、reasoning_effort、service_tier、fork_turns。

- **fork_mode 解析**：`fork_context` 在 V2 直接拒绝（提示改用 fork_turns）；`fork_turns` 缺省视为 all，接受 none / all / 正整数字符串（0 报错），分别对应 无 fork / 全量 fork / 截断最近 N 轮。
- **初始内容**：message 必须非空；同一段解析代码也支持 V1 风格的结构化 items（text/image/local_image/audio/skill/mention），与 message 二选一，同时给或都不给都报错。
- **构造子路径**：`parent_agent_path.join(task_name)` 得到子 Agent 的 canonical path（如 /root + task_3 → /root/task_3）。

### 5.8.2 子配置构建：继承的是什么

`build_agent_spawn_config`（multi_agents_common.rs）给出了标准答案——**从“当前 turn 的生效状态”出发，而不是克隆父 Session 的陈旧配置**：

1. 从父的 effective config 克隆起点；
2. 刷新运行期归属字段：model（turn.model_info.slug）、provider、reasoning effort/summary、developer instructions（V2 且配置了 `subagent_developer_instructions` 时替换之）、base instructions 及其 provenance；
3. `apply_spawn_agent_runtime_overrides` 强制同步四件“活的状态”：approval policy、cwd、permission profile 快照、approvals reviewer——源码注释明言跳过这一步会让子 Agent 在审批策略、cwd、沙箱上与父会话不一致。

在此基础上依次叠加可选覆盖，每一层都有验证兜底：

| 覆盖项 | 来源（优先级从高到低） | 校验 |
|--------|------------------------|------|
| model / reasoning_effort | 调用参数 > `agent_default_subagent_model` / `agent_default_subagent_reasoning_effort` 配置 | 必须存在于 models manager 列表且支持 multi-agent backend；effort 必须被所选模型支持 |
| service_tier | 显式参数 > config > parent | 必须被子模型支持；三者皆空则置 None |
| agent_role | agent_type 参数（trim 后非空才生效） | apply 之后若 model/effort 变了还要重新验证 effort 合法性 |

工具描述里列出的可覆盖模型清单最多 5 个（`MAX_SPAWN_AGENT_MODEL_OVERRIDES = 5`），只展示 picker 可见且支持相应后端的模型。

### 5.8.3 spawn_agent_internal：七步落地

`spawn_agent_internal` 的核心步骤：

1. **确定协议版本**：`effective_multi_agent_version_for_spawn` 综合 feature flag、session source、父线程与配置决定 V1/V2（优先序详见 5.10.2）。
2. **执行容量检查**：V2 子 Agent 受 `AgentExecutionLimiter` 限制，无余量即抛 AgentLimitReached。
3. **预留 residency slot**（V2 常驻模式，见 5.11.4）——先拿 pending slot，满员就尝试驱逐 LRU 候选。
4. **注册表预留**：`reserve_spawn_slot` + `prepare_thread_spawn`——depth==1 时顺带把父线程注册为 /root；预留 agent path 与昵称，产出最终的 `SessionSource::SubAgent(ThreadSpawn{...})` 与元数据。
5. **建线程**：有 fork 模式走 `spawn_forked_thread`（5.8.4），否则 spawn_new_thread_with_source（全新空白历史）；两种都显式带上 ThreadSource::Subagent、父子环境/exec policy 继承、父 turn/root turn id。
6. **提交与广播**：commit reservation 与 residency slot；发出 subagent session started 分析事件；notify_thread_created 通知客户端订阅新线程；非 ephemeral 线程把 spawn edge（Open 状态）持久化进 agent graph store——这是 V2 断线恢复整棵树的依据。
7. **发送初始输入**：
   - 用户输入型：send_input（start-or-steer）；
   - 通信型：容量复查后经 Op::InterAgentCommunication 提交（V2 主路径，内容通常是带任务描述的 NEW_TASK）。

随后非 V2 才启动 completion watcher（5.4.1），并返回 LiveAgent { thread_id, metadata, status }。handler 层再补两笔：emit 一条 SubAgentActivityKind::Started 的 TurnItem 供 UI 渲染；打点 codex.multi_agent.spawn{role, version} 计数器。

### 5.8.4 Fork：历史继承的真实算法

全量/截断 fork 比“复制粘贴对话记录”精细得多，都在 `spawn_forked_thread` 里：

**第一步，物化父历史。** rollout 写入是异步排队的，fork 快照前必须 ensure_rollout_materialized + flush_rollout，否则会漏掉最近的 item。

**第二步，按白名单筛选 items**（keep_forked_rollout_item）：

| 保留 | 丢弃 |
|------|------|
| system / developer / user 消息 | Reasoning、各类 tool call/output（除无 call_id 的孤儿输出） |
| assistant 仅 FinalAnswer phase 的最终回答 | AgentMessage（内部流式消息一律不带过去） |
| 没有 call_id 的 function 输出 | InterAgentCommunication 及其 metadata、SecurityRiskScore |
| Compaction、SessionMeta、EventMsg | TurnContext / WorldState（仅全量 fork 保留，理由见下） |

TurnContext/WorldState 的取舍有个精妙处：全量 fork 复用父的“参考上下文基线”，避免子端重建上下文；但一旦遇到旧式的 compaction checkpoint（无 replacement_history），就必须放弃基线重建——因为 checkpoint 已经让基线过期了。

**第三步，开发指令层的净化与替换。** 逐条扫 developer 消息内容：

- 剔除多 Agent 角色指令片段（MultiAgentRoleInstructions）、模式指令片段、时间提醒片段、usage hint 文本——这些“管理性段落”不应原样遗传给子 Agent，子端会重建属于自己的那份；
- V2 下若配置了子 Agent 专用 developer instructions，则把与父 developer instructions 文本匹配的片段**替换**为子版本（文本包含匹配——rollout item 里 fragment 尚无独立溯源标记，源码留了 TODO 改进）；
- 全量 fork 但父上下文本没写入 developer fragment 时，补一条 developer update item，确保子指令恰好送达一次。

**第四步，截断与追加。** LastNTurns(n) 先把 rollout 截到最后 n 轮；分页历史模式下过滤 EventMsg 类噪音 items；最后在全量 fork 场景追加子 Agent 自己的 usage hint（来自 role catalog 或配置）。

走完这套流程，`fork_thread_with_source` 以 InitialHistory::Forked(items) 建出新线程，并记下 forked_from_thread_id 与 fork_parent_spawn_call_id（审计用）。

### 5.8.5 spawn 结果输出

返回给模型的 JSON 由 `hide_spawn_agent_metadata` 控制（默认 true）：

```jsonc
// 默认：只给规范 task 名
{ "task_name": "/root/task_3" }
// hide_spawn_agent_metadata=false 时额外带昵称
{ "task_name": "/root/task_3", "nickname": "Kepler" }
```

关闭元数据输出的动机之一是减少不必要的可见信息随响应扩散。

## 5.9 Agent 间通信

### 5.9.1 协议消息

Agent 间通信通过 `InterAgentCommunication` 实现（`codex-rs/protocol/src/protocol.rs`）：

```rust
pub struct InterAgentCommunication {
    pub id: Option<ResponseItemId>,
    pub author: AgentPath,
    pub recipient: AgentPath,
    pub other_recipients: Vec<AgentPath>,
    pub content: String,
    pub encrypted_content: Option<String>,   // 非明文通道的内容走这里
    pub trigger_turn: bool,
}
```

工具层构造消息时有一步通道感知（communication_from_tool_message）：来源是 DirectPlaintextMessage 就渲染成人读的消息框架并以明文存放；否则改走 new_encrypted，正文装进 encrypted_content。

### 5.9.2 投递路径

```
父 Agent 调用 send_message / followup_task / spawn_agent
    │
    ▼
handler 构造 InterAgentCommunication（含渲染或加密决策）
    │
    ▼
AgentControl::send_inter_agent_communication
    │  （trigger_turn=true 时先做执行容量检查）
    ▼
ThreadManagerState::send_op(Op::InterAgentCommunication)
    │
    ▼
目标 Session 收到 Op → inter_agent_communication()   [session/handlers.rs]
    │
    ├─ enqueue_mailbox_communication：入邮箱队列
    └─ trigger_turn || 存在 durable sleep 待处理
          → maybe_start_turn_for_pending_work_with_sub_id
            （交由 pending-work 调度器决定空闲会话何时开 turn）
```

注意这里的语义比“入队后立刻开 turn”更讲究：投递统一进邮箱队列，是否开新 turn 由共享的 pending-work 调度器裁决——这正是 V2 wait 工具能在事件驱动模型下工作的前提。

### 5.9.3 三种语境化消息 + 一张通知卡

子 Agent 与父 Agent 收到的通信内容是四类固定渲染（实现都在 core/src/context/，均为 ContextualUserFragment）：

| Fragment | 角色 | 标记 | 形态 |
|----------|------|------|------|
| InterAgentMessage (Message/NewTask) | assistant | 无 | Message Type: MESSAGE\|NEW_TASK + Task name + Sender + Payload |
| InterAgentCompletionMessage | assistant | 无 | 同上，Message Type 为 FINAL_ANSWER |
| SubagentNotification | user | <subagent_notification></subagent_notification> | JSON {"agent_path", "status"} |

MESSAGE 由 send_message 产生（不触发 turn）；NEW_TASK 由 followup_task 与 spawn 的初始任务产生（触发 turn）；FINAL_ANSWER 是子 Agent 结束时 completion watcher 生成的结果回包（不触发 turn，入队等待父 Agent 取阅）。SubagentNotification 只服务非 V2 通知路径。

### 5.9.4 等待结果：wait_agent 的三种结局

父 Agent 通常在自己的 turn 里调 wait_agent 等 mailbox 动静（V2 版本，multi_agents_v2/wait.rs）：

- 监听的是本 session input queue 的 InputQueueActivity watch 流，活动分两类：Mailbox（有子 Agent 消息/完成通知到达）与 Steer（用户把新输入 steer 进了当前 turn）；
- **timeout_ms 校验**：超过 max 直接报错给模型；低于 min 则**钳制到最小值**并在输出里注明 clamp；缺省 30s；
- 三种结局对应三句输出文案："Wait completed." / "Wait interrupted by new input." / "Wait timed out."，外加 timed_out 布尔位；**不返回内容本体**——内容会随后续采样进入上下文，wait 只负责“叫醒”；
- 等待期间 emit 成对的 CollabAgentToolCall(tool=Wait, InProgress/Completed) TurnItem，UI 能画出等待时段。

V1 的 wait 是另一番形状：传 targets（多个 id 竞争，谁先终态谁先返回）+ timeout，返回「id → 最终状态」字典与 timed_out。V2 把“选谁”交给 mailbox 事件，把“是谁来了”推迟到后续消息本身，更适合不确定个数的并行协作。

### 5.9.5 其他管理动作

- **send_message**：轻量投递，永不触发 turn；
- **followup_task**：空闲则开 turn，忙碌则“sampling 过程中的消息边界处或 pending tool call 完成后尽快送达”——插入而不打断完整性；
- **interrupt_agent**（V2）：中断当前 turn 并返回 previous_status；Agent 本体仍存活可继续收消息。对照 V1 的 close_agent：连带活体后代一起关停，且先前是 Completed 的 Agent 若不显式 close 会一直计入并发额度（工具描述里明确提醒模型及时释放不需要的 Agent）；
- **resume_agent**（V1）：把此前 closed 的 Agent 恢复到可再次接收输入的状态；持久化层面的恢复由 `resume_agent_from_rollout` 完成——沿持久化的 spawn 边 BFS 恢复整棵后代树。

## 5.10 V1 与 V2 多 Agent 模式

Codex 实现了两套多 Agent 工具集，差异不止“工具名好不好看”。

### 5.10.1 总览对比

| 维度 | V1 | V2 |
|------|----|----|
| 工具命名 | namespace `multi_agent_v1`（常量 MULTI_AGENT_V1_NAMESPACE）下的 spawn_agent/send_input/wait_agent/resume_agent/close_agent | 扁平函数名 spawn_agent/send_message/followup_task/wait_agent/interrupt_agent/list_agents，默认配在 collaboration 命名空间（`to=functions.collaboration.*`） |
| 实现目录 | tools/handlers/multi_agents/{spawn,send_input,wait,resume_agent,close_agent}.rs | tools/handlers/multi_agents_v2/{spawn,send_message,followup_task,wait,interrupt_agent,list_agents,message_tool}.rs |
| 寻址 | ThreadId 字符串为主 | Task path（相对或规范全名）或 ThreadId |
| 初始/追加任务 | send_input(interrupt=bool) 一个入口双语义 | followup_task（触发 turn）/ send_message（不触发）分开 |
| 结束方式 | close_agent（关停子孙树） | interrupt_agent（仅断当前 turn，Agent 留守）；生命周期交给 residency |
| 历史 fork | fork_context: bool | fork_turns: none/all/N |
| 历史净化 | 较简单 | 开发指令片段剔除/替换、usage hint 重建、compaction 基线处理（5.8.4） |
| 输出 | submission_id / 状态字典 | canonical task name（默认隐藏昵称） |
| 结果回流 | 状态卡片 <subagent_notification> | FINAL_ANSWER 通信 + 邮箱调度 |
| 并发控制 | 名额总数限制 | AgentExecutionLimiter + residency LRU 驻留 |
| 教学材料 | 工具描述内嵌大段委派指南（何时委派/何时不委派/怎么拆子任务） | root/subagent 双份 usage hint + 模式指令 |

V1 特有一件小工具容易漏看：resume_agent 可以把此前 closed 的 Agent 恢复到可再次接收输入的状态；持久化恢复则由 resume_agent_from_rollout 完成——沿 spawn 边 BFS 恢复整棵后代树。

### 5.10.2 版本选择的完整判定

工具可用性（spec_plan.rs 的 collab_tools_enabled）：

- Disabled → 不可用；
- V1 → 当前深度没超限；
- V2 → 当前 session 没有已被赋值的 agent path（即是发起方），**或者**模型自身声明支持 multi-agent V2（model_info.multi_agent_version == Some(V2)）。深嵌套的孙子代想再 spawn，要靠模型能力位背书——比 V1 单纯的 depth gate 更注重“这一层的模型是否见过协作语义”。

最终版本裁定 effective_multi_agent_version_for_spawn（以及同族 multi_agent_version_for_model）的优先序：**显式配置覆盖 > 模型目录声明 > feature flag 默认值**。

## 5.11 并发、深度与预算控制

### 5.11.1 配置矩阵（默认值均在 core/src/config/mod.rs）

| 配置 | 默认值 | 含义 |
|------|--------|------|
| DEFAULT_AGENT_MAX_THREADS | Some(6) | V1 每 session 子线程总量上限 |
| DEFAULT_MULTI_AGENT_V2_MAX_CONCURRENT_THREADS_PER_SESSION | 4 | V2 每 session 并发线程上限；effective 计算为 -1（root 自身占一个名额，3 个真子代理） |
| DEFAULT_AGENT_MAX_DEPTH | 1 | 最大 spawn 深度（主要是 V1 的强闸门，见下） |
| min_wait_timeout_ms | 10_000 | wait_agent 最小等待（防忙轮询） |
| default_wait_timeout_ms | 30_000 | wait_agent 默认等待 |
| max_wait_timeout_ms（= HARD_MAX…TIMEOUT_MS） | 3_600_000 | wait_agent 上限（1 小时） |
| hide_spawn_agent_metadata | true | spawn 结果默认不给昵称 |

usage hint 还会把并发容量**念给模型听**："There are {max_concurrency} available concurrency slots…"，让模型对并行度形成预期。

### 5.11.2 并发限制：AgentExecutionLimiter

codex-rs/core/src/agent/control/execution.rs：

```rust
pub(super) struct AgentExecutionLimiter {
    active: AtomicUsize,
    max_threads: OnceLock<usize>,   // with_session_id 时一次性初始化
}
```

- 检查发生在两个时刻：spawn 前；以及 V2 触发型通信交付前（ensure_execution_capacity_for_turn_start——目标已在跑的 turn 则直接放行，不需要新容量）；
- 通过检查后取得 AgentExecutionGuard，RAII 式 Drop 减计数；
- 只对 “V2 + SubAgent source” 组合生效（is_execution_limited）。

与注册表的总量上限不同，limiter 限的是**同时在执行 turn 的 agent 数**，完成/idle 的不计入。

### 5.11.3 RolloutBudget 与深度

AgentControl 持有共享的 Arc<RolloutBudget>：整个树共用一份 token 预算配置（limit_tokens + 提醒阈值 + 采样/预填充权重），根与所有克隆句柄指向同一实例。

深度控制对 V1 是双重闸门（注册与执行都查）；V2 如 5.10.2 所述以模型能力位替代了无条件深度上限。原有的物理上限依然存在：超出后新 spawn 报错，防止无限递归。

### 5.11.4 V2 Residency：常驻代理的 LRU 驻留

V2 子 Agent 的存活语义由 V2Residency（control/residency.rs）管理，思路类似内存换页：

- 所有 SessionSource::SubAgent(_) 的 V2 线程都是 resident candidate；residency 维护一个 LRU VecDeque<ThreadId>；
- 新 spawn 要拿 pending slot；满了就从 LRU 头部找驱逐候选：必须是“终态附近”（Completed/Errored/Interrupted）、无活动 turn、mailbox 空——满足才 shutdown_and_wait + 物化 rollout + 移出线程表；中途被 touch（复活）过的候选跳过。找不到可驱逐者时归还 slot 并报 AgentLimitReached；
- 被驱逐者的环境选择存在注册表里（save_evicted_environments）；后续 ensure_v2_agent_loaded（比如有人给它发了 followup）会从 thread-store 读回元数据与模型上下文，重放角色（5.7.3）、恢复 stored model/provider，重建线程并 touch LRU；
- spawn edge 状态机（Open/Closed，agent graph store）与 residency 相互独立：residency 决定“此刻在不在内存里”，edge 决定“树关系还在不在”。这就是 V2 的“常驻”——Agent 关机不等于除名。

## 5.12 把 Multi-Agent 教给模型：工具描述与 usage hints

Multi-Agent 能力最终通过工具暴露给模型。在 spec_plan.rs 的 add_collaboration_tools 中按版本注册对应工具集。但暴露的不止 tool spec，还有一整套嵌入上下文的提示词工程。

### 5.12.1 工具描述里的委派纪律

V1 spawn_agent 的 description 本身就是一篇委派方法论（节选要点）：

- **授权门槛**：除非用户或 AGENTS.md/skill 明确要求委派/并行，不许 spawn；“研究得深入一点”“仔细分析”这类请求不算授权；
- **先计划再委派**：识别关键路径 blocker 与可并行的 sidecar 任务；blocker 必须本地自己做，别丢给子模型再干等；
- **子任务设计**：具体、自包含、实质推进主任务；编码类尽量委派“可落盘修改 + 最后列出改动文件”的 worker 任务而非只读探险；多个子任务的写集必须不相交；
- **委派之后**：wait_agent 要省着用（只在关键路径真被阻塞时调用）；等待期间立刻去做不重叠的正事，禁止反射性反复 wait。

这些规则与 5.11 的硬限流互为表里：机制挡住“不得不并行太多”，描述词引导“不该并行别并行”。

### 5.12.2 双视角 usage hints

V2 会根据身份注入不同的 hint（session/multi_agents.rs，可被配置 root_agent_usage_hint_text / subagent_usage_hint_text / model catalog 覆盖，空字符串表示彻底抑制）：

- **root 版**：“你是 /root……所有 Agent 同等智能、拥有同样的工具……你可以用 spawn_agent / followup_task / send_message……你将收到如下格式的消息”；它列出了 MESSAGE/FINAL_ANSWER 两种收件格式；
- **subagent 版**：多一条 NEW_TASK 进件格式，并教它“final channel 的回复会立即送回父 Agent”——这就是结果回传机制的说明文本；
- **公共尾巴**：协作工具不能从 functions.exec 内部调用，必须作为直接工具调用（to=functions.collaboration.*）；所有 Agent 共享目录/文件系统，编辑互相立即可见；并发槽数量宣示；wait 建议（分钟级等待避免忙轮询）。

hints 选择器（usage_hint_text）按 session source 分发：ThreadSpawn 子代理拿 subagent 版，Cli/VSCode/Exec/Mcp 等源头拿 root 版；Internal 等特殊来源不给。

### 5.12.3 模式指令：<multi_agent_mode>

还有一层总开关式的 developer message（context/multi_agent_mode_instructions.rs）：

| 模式 | 注入文本的效果 |
|------|----------------|
| ExplicitRequestOnly（默认） | 覆盖一切更早的主动性指示：“Do not spawn sub-agents unless explicitly asked…” |
| Proactive | 反转上述限制：主动委派生效，直到下一条 mode 消息改变它 |
| Custom(text) | 配置/model catalog 的自定义全文 |

模式的判定（effective_multi_agent_mode）颇有意思：reasoning effort 达到 **Ultra** 自动升级为 Proactive；配置了 hint 文本（含空串）则成为 Custom；否则 ExplicitRequestOnly。“推理强度越高越信任其自主委派判断”是一条刻在机制里的启发式。

## 5.13 设计取舍

| 取舍 | 选择 | 替代方案 | 原因 |
|------|------|----------|------|
| Agent = Thread | 复用 Session/CodexThread | 独立进程 | 复用基础设施，统一持久化/沙箱 |
| 共享 AgentControl | 同 session 树共享一份 | 每个 Agent 独立控制 | 统一 budget、并发、注册表；作用域天然收敛在树内 |
| Weak 引用 manager | 避免循环引用 | Arc 强引用 | 防止内存泄漏和影子持久化 |
| AgentPath 硬校验、禁 .. | 只许向下相对引用 | 任意相对寻址 | 保证树形结构，杜绝引用逃逸导致的归属混乱 |
| SpawnReservation 两阶段预留 | Drop 自动回滚 | 直接写入再补偿删除 | spawn 中途失败零残留 |
| 昵称池 + 序号轮次 | 101 科学家名可耗尽重来 | 每个 agent 随机哈希名 | 展示友好且有限可枚举，冲突靠轮次消解 |
| fork 白名单 + 净化 | 只带有用项、管理片段重建 | 整卷复制 | 防 instruction 遗毒、控上下文体积 |
| 通信默认加密通道 | 非 DirectPlaintext 走 encrypted_content | 全部明文 | 降低跨会话泄露面 |
| V2 用能力位替代纯深度限制 | 模型声明支持才暴露 spawn | 一律按 depth 硬卡 | 协作质量依赖模型对协语的熟悉度 |
| Residency LRU 驻留 | 完成即可被换出 | spawn 即常驻直到 close | 内存可控又不失“可回访性” |
| wait 只报告不动内容 | mailbox watch + summary | wait 返回全部积压内容 | 内容按正常采样渠道进场，职责单一 |
| V1 + V2 并存 | 两套工具集 | 只保留一套 | 平滑演进，兼容旧配置 |
| 执行容量限制只对 V2 | V2 限并发 | 所有版本都限 | V2 面向大规模协作，更需要挡失控 |
| 角色是白名单叠加层 | 只能收窄不能扩权 | 自由 config 改写 | 保住父会话权威，权限不逃逸 |

## 5.14 关键实现入口

| 概念 | 主要文件 | 说明 |
|------|----------|------|
| AgentControl | codex-rs/core/src/agent/control.rs | 多 Agent 控制面、通信投递、list/interrupt/status、completion watcher |
| Agent spawn | codex-rs/core/src/agent/control/spawn.rs | spawn_agent_internal、fork 算法、residency 恢复、V1 树状 resume |
| Spawn 共享辅助 | codex-rs/core/src/tools/handlers/multi_agents_common.rs | 子配置构建、模型/tier/role 校验、错误翻译 |
| V1 handlers | codex-rs/core/src/tools/handlers/multi_agents/{spawn,send_input,wait,resume_agent,close_agent}.rs | V1 五件套 |
| V2 handlers | codex-rs/core/src/tools/handlers/multi_agents_v2/{spawn,send_message,followup_task,wait,interrupt_agent,list_agents,message_tool}.rs | V2 六件套 + 消息内容处理 |
| 工具规格/schema | codex-rs/core/src/tools/handlers/multi_agents_spec.rs | 全部参数/输出 schema、委派指南描述文 |
| 关闭与关停 | codex-rs/core/src/agent/control/legacy.rs | shutdown/close/tree-level close |
| Agent 执行限制 | codex-rs/core/src/agent/control/execution.rs | AgentExecutionLimiter / Guard |
| V2 Residency | codex-rs/core/src/agent/control/residency.rs | LRU 常驻管理 |
| AgentRegistry | codex-rs/core/src/agent/registry.rs | 树索引、昵称池、SpawnReservation |
| AgentPath | codex-rs/protocol/src/agent_path.rs | 路径校验/解析 |
| AgentStatus | codex-rs/protocol/src/protocol.rs | 状态枚举与 InterAgentCommunication |
| 状态推导 | codex-rs/core/src/agent/status.rs | agent_status_from_event / is_final |
| Agent 角色 | codex-rs/core/src/agent/role.rs | apply_role_to_config、内置角色加载 |
| 角色配置定义 | codex-rs/core/src/config/agent_roles.rs | [agent_roles] 解析 |
| 通信 context fragments | codex-rs/core/src/context/{inter_agent_message,inter_agent_completion_message,subagent_notification}.rs | 四类注入内容的渲染 |
| 模式指令 | codex-rs/core/src/context/multi_agent_mode_instructions.rs | ExplicitRequestOnly/Proactive/Custom |
| usage hints | codex-rs/core/src/session/multi_agents.rs | root/subagent 双视角 hint 与解析 |
| 通信日志 | codex-rs/core/src/agent_communication.rs | 发送/接收事件与通信 Kind |
| Session 收信 | codex-rs/core/src/session/handlers.rs | inter_agent_communication 邮箱入队与调度 |
| 工具注册开关 | codex-rs/core/src/tools/spec_plan.rs | add_collaboration_tools / collab_tools_enabled |
| 配置默认值 | codex-rs/core/src/config/mod.rs | 并发/深度/wait 超时常量与 MultiAgentV2Config |
| 昵称池 | codex-rs/core/src/agent/agent_names.txt | 101 个科学家名 |

## 5.15 小结

Codex CLI 的 Multi-Agent 系统建立在“**Agent ≈ Thread**”的简洁抽象之上，围绕一棵 root 树展开：

- AgentControl 是同树共享的控制面；AgentRegistry 双索引维护树状态，两阶段 SpawnReservation 保证 spawn 的原子性；
- AgentPath 用类文件系统路径寻址，且硬校验禁掉 ..，把引用关系钉死成树；
- Spawn 是一次完整的配置工程：turn 生效态出发 → 可选模型/tier/角色叠加 → 运行期状态强制同步 → 白名单 fork（伴随开发指令净化与替换）→ 注册与持久化 spawn 边；
- 通信走统一邮箱：MESSAGE/NEW_TASK/FINAL_ANSWER 三种语境化消息 + 非 V2 的 <subagent_notification> 卡片；wait_agent 是事件驱动的唤醒原语，只报告活动不搬运内容；
- 并发三层防护：注册表总数（V1 默认 6）、执行 limiter（RAII guard，V2 默认并发 4 − 树根 1）、深度闸门（V1 默认 1）；V2 再加一层 residency LRU，让完成的 Agent 可被换出、可被召回；
- 角色是“只会削权”的配置叠加层；nickname 池耗尽靠序号轮次；工具描述与 usage hints 组成的提示词工程把委派纪律、消息格式、并发容量一并教给模型；
- V1 与 V2 从命名空间到生命周期语义全面分化：V1 管“会话内的工作小组”，V2 管“可持久化、可驱逐、可恢复的常驻团队”。

这套设计的总效果是：核心 Agent Loop 几乎不需要感知 Multi-Agent 的存在——树上的每个节点都在跑同一套循环，协作只是多了几种发消息和等结果的工具。

下一章（执行沙箱与权限）会深入讨论 Agent 执行命令和文件操作时的安全隔离机制。

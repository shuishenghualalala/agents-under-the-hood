# 第 4 章：Agent Loop（核心编排循环）

> 本章导读：Agent Loop 是 Codex CLI 的心脏。读完本章，你会理解一条用户消息如何被封装成 `Submission`，如何进入 `Session` 的串行处理队列，如何被转变成一个或多个 `Turn`，模型采样、工具执行、结果回流又怎样构成一个持续的循环，以及 Token 触顶时系统如何通过 Compaction“自我瘦身”。

## 4.1 本章要解决的问题

Codex CLI 不是简单的“一问一答”聊天机器人。用户说一句“给这个项目加测试”，模型可能需要：

1. 先搜索项目结构；
2. 读取相关源码；
3. 编辑或新增文件；
4. 运行测试命令；
5. 根据测试结果再修改；
6. 最后给出总结。

这意味着系统必须支持：**一次用户输入 → 多轮模型采样 → 多工具调用 → 结果回写到上下文 → 再次采样**，直到模型认为任务完成。

Agent Loop 要解决的工程问题包括：

| 问题 | 关键设计 |
|------|----------|
| 并发用户输入与模型输出如何协调 | `SessionIo` + `Submission` 串行队列 |
| 模型调用与工具执行如何交替 | `run_turn()` 内部的采样循环 |
| 多个工具调用能否并行 | `ToolCallRuntime` + `parallel_execution` 读写锁 |
| 上下文过长怎么办 | `run_auto_compact()` 触发 compaction |
| 用户中途想追加指令怎么办 | `steer_input()` 把输入注入当前 turn |
| 工具结果如何安全回写到历史 | `ResponseInputItem` 统一回流 |
| 如何支持 Code Mode、Plan Mode 等不同模式 | `TurnContext.mode` + `ToolRouter` |

## 4.2 Agent Loop 全景图

```
AppServer / TUI / Exec
    │
    │ 调用 CodexThread::start_or_steer_turn / steer_turn
    ▼
CodexThread::submit(Op::TurnInput { request, mode, reply })
    │
    ▼
SessionIo::submit_with_id(Submission)
    │
    ▼
async_channel ──▶ submission_loop(sess, rx_sub)
    │
    ▼
turn_input::handle(sess, request, mode, sub_id)
    │
    ├─ StartOrSteer: 空闲则 start，活动则 steer
    ├─ StartIfIdle: 空闲则 start，忙则拒绝
    └─ Steer { expected_turn_id }: 校验后 steer
        │
        ▼
    sess.run_task(Regular, input, ...)  启动 turn 任务
        │
        ▼
    run_turn(sess, turn_context, input, client_session, cancel_token)
        │
        ▼
    loop {
        1. capture_step_context / record_context_updates
        2. clone_history().for_prompt(...)  构造模型输入
        3. run_sampling_request(...)  调用模型
           ├─ ToolRouter 根据当前 step 组装可用工具
           ├─ ModelClientSession.stream(prompt, ...)  流式请求
           └─ 解析 ResponseEvent 流
        4. 如果是 assistant message → 记录历史，可能结束 turn
        5. 如果是 tool call(s) → ToolCallRuntime 并行/串行执行
        6. 工具输出转成 ResponseInputItem 写回历史
        7. 继续下一轮采样，直到 needs_follow_up == false
    }
        │
        ▼
    turn 完成，发送 TurnCompleted / 错误事件
```

## 4.3 核心数据结构

### 4.3.1 Session 与 SessionIo

`Session` 是 Agent Loop 的状态机，位于 `codex-rs/core/src/session/session.rs`。关键字段：

```rust
pub(crate) struct Session {
    pub(crate) thread_id: ThreadId,
    pub(super) tx_event: Sender<Event>,             // 向 UI/app-server 发事件
    pub(super) agent_status: watch::Sender<AgentStatus>,
    pub(super) state: Mutex<SessionState>,         // 历史、上下文、待处理输入等
    pub(crate) conversation: Arc<RealtimeConversationManager>,
    pub(crate) active_turn: Mutex<Option<ActiveTurn>>,
    pub(crate) input_queue: InputQueue,
    pub(crate) services: SessionServices,           // 模型客户端、扩展、MCP、工具等
    ...
}
```

> 历史存储细节：2026-08（#37871）起，持久化历史类型（`RolloutItem`、`ResponseItemEnvelope`、`CompactedItem` 等）从 `codex-protocol` 拆到了独立的 `codex-rs/history` crate。`SessionState` 中的历史项以 `ResponseItemEnvelope` 形式存储——它把原始的 `ResponseItem` 包上一层可选的 `CodexHarnessMetadata`（如 `client_authored` 标记），既保留模型可见内容不变，又能在不污染协议类型的前提下携带 harness 专属元数据。

`SessionIo` 是外部与 `Session` 交互的句柄，位于 `codex-rs/core/src/session/mod.rs`：

```rust
pub(crate) struct SessionIo {
    pub(crate) tx_sub: Sender<Submission>,        // 向 Session 提交操作
    pub(crate) rx_event: Receiver<Event>,        // 从 Session 接收事件
    pub(crate) agent_status: watch::Receiver<AgentStatus>,
    pub(crate) session_loop_termination: SessionLoopTermination,
}
```

**为什么把 `SessionIo` 单独拆出来？** 因为 `tx_sub` 和 `rx_event` 是通道端点，所有提交者被 drop 后，session loop 可以自然退出。`Session` 本身不需要关心谁是调用方。

### 4.3.2 TurnContext 与 StepContext

`TurnContext` 描述一个 turn 的静态配置快照（模型、权限、沙箱、模式、环境等），位于 `codex-rs/core/src/session/turn_context.rs`：

```rust
pub struct TurnContext {
    pub(crate) sub_id: String,                      // turn ID
    pub config: Arc<Config>,
    pub(crate) model_info: ModelInfo,
    pub(crate) provider: SharedModelProvider,
    pub(crate) reasoning_effort: Option<ReasoningEffortConfig>,
    pub(crate) mode: ModeKind,                      // Ask/Plan/Code/etc.
    pub(crate) approval_policy: Constrained<AskForApproval>,
    pub(crate) permission_profile: PermissionProfile,
    pub(crate) environments: TurnEnvironmentSnapshot,
    pub(crate) turn_metadata_state: Arc<TurnMetadataState>,
    ...
}
```

`StepContext` 描述一次模型采样步骤的上下文。一个 turn 里可能有多个 step（每次模型调用 + 工具结果回流算一个 step）。它让工具调用能够引用“是哪一步广告了这些工具”，从而保证工具执行时使用的是同一份工具列表视图。

## 4.4 提交与分发：从 UI 到 Session Loop

### 4.4.1 Submission 队列

所有对 Session 的操作都通过 `Op` 枚举表达，包装成 `Submission` 后发送到 `tx_sub`：

```rust
pub(crate) async fn submit_with_id(&self, mut sub: Submission,
) -> CodexResult<()> {
    if sub.trace.is_none() {
        sub.trace = current_span_w3c_trace_context();
    }
    self.tx_sub.send(sub).await.map_err(|_| CodexErr::InternalAgentDied)?;
    Ok(())
}
```

`Op` 包括：

- `TurnInput { request, mode, reply }`：用户输入，原子性地 start、steer 或拒绝（带回复通道）。`mode` 可以是 `StartOrSteer`（空闲 start / 活动 steer）、`StartIfIdle`（仅空闲 start）或 `Steer { expected_turn_id }`（校验后 steer）
- `RecoverTurn { thread_settings, reply }`：恢复空闲 turn（带回复通道）
- `Interrupt`：中断当前模型采样/工具执行
- `CleanBackgroundTerminals`：清理后台终端
- `RealtimeConversationStart/Audio/Text/Speech/Close/ListVoices`：实时语音对话
- `ThreadSettings`：更新 thread 配置
- `InterAgentCommunication`：子 Agent 间通信
- `ExecApproval` / `PatchApproval`：审批决策
- `ResolveElicitation`：解决 MCP 工具 elicitation
- `Review { review_request }`：审查请求
- `ApproveGuardianDeniedAction { event }`：批准 Guardian 拒绝的动作
- `UserInputAnswer` / `RequestPermissionsResponse`：对用户询问的回复
- `DynamicToolResponse`：动态工具响应
- `RefreshMcpServers` / `ReloadUserConfig`：配置热更新
- `Compact`：手动触发 compaction
- `ThreadRollback`：回滚历史
- `SetThreadMemoryMode`：设置记忆模式
- `RunUserShellCommand`：运行用户 shell 命令
- `Shutdown`：关闭 session

> 注：Op 枚举定义已从 `codex-rs/core/src/session/mod.rs` 移到 `codex-rs/protocol/src/protocol.rs:543`（2026-08，#38275），成为跨 crate 共享的协议类型。

### 4.4.2 submission_loop

`submission_loop` 在 `codex-rs/core/src/session/handlers.rs` 中，是一个单线程循环：

```rust
pub(super) async fn submission_loop(sess: Arc<Session>, config: Arc<Config>, rx_sub: Receiver<Submission>) {
    while let Ok(sub) = rx_sub.recv().await {
        let should_exit = async {
            match sub.op {
                Op::Interrupt => { interrupt(&sess).await; false }
                Op::TurnInput { request, mode, reply } => {
                    let result = turn_input::handle(&sess, *request, mode, sub.id.clone()).await;
                    let _ = reply.send(result);
                    false
                }
                Op::RecoverTurn { thread_settings, reply } => {
                    let result = turn_input::handle_recovery(&sess, thread_settings, sub.id.clone()).await;
                    let _ = reply.send(result);
                    false
                }
                Op::Compact => { compact(&sess, sub.id.clone()).await; false }
                ...
                Op::Shutdown => { shutdown(&sess, sub.id.clone()).await; true }
            }
        }.await;
        if should_exit { break; }
    }
}
```

**关键设计：Session 一次只处理一个 submission**。这避免了历史、token 计数、审批状态的并发竞争。所有“外部动作”都变成事件进入队列，按顺序消费。

### 4.4.3 用户输入如何进入 Turn

`turn_input::handle`（`codex-rs/core/src/session/turn_input.rs`）是 `Op::TurnInput` 的统一处理函数。它的定位注释说得很清楚：

> This is the one place Core decides whether submitted input starts a turn, steers an active turn, or is rejected.

它根据 `mode` 做三件事之一，并返回带类型的 `TurnInputSubmission`（`Started { turn_id }` / `Steered { turn_id }` / `NotSubmitted { reason }`）：

| 模式 | 行为 | 返回 |
|------|------|------|
| `StartOrSteer` | 空闲则 `new_turn_with_sub_id` 创建 `TurnContext` 和 `ActiveTurn`；活动 turn 则 steer | `Started` / `Steered` |
| `StartIfIdle` | 仅当无活动 turn 时才 start；有活动 turn 则拒绝 | `Started` / `NotSubmitted` |
| `Steer { expected_turn_id }` | 校验 `expected_turn_id` 与当前活动 turn 匹配后 steer | `Steered` / `NotSubmitted` |

关键语义：
- **持久化 thread 设置在 `Started` 和 `Steered` 时都生效**，但 turn start 选项（如 `final_output_json_schema`、`parent_turn_id`）只在 `Started` 时应用。
- 如果请求包含不兼容的输出 schema 或目标 turn 不可 steer（如 Review/Compact 类型），Core 会**拒绝输入且不应用任何设置或入队**，避免副作用。
- 如果当前已有 active turn，`StartOrSteer` 不会强制打断，而是把输入 steer 进当前 turn 的 mailbox，供下一轮采样前读取。

`RecoverTurn`（`Op::RecoverTurn`）走类似的 `turn_input::handle_recovery` 路径，用于从 idle 状态恢复一个之前中断的 turn。

## 4.5 Turn 生命周期：run_turn

`run_turn` 是 Agent Loop 的核心，位于 `codex-rs/core/src/session/turn.rs`。它的职责是：

1. 准备模型客户端会话；
2. 预 turn compaction；
3. 构建技能和插件注入；
4. 运行 hooks；
5. 进入采样循环，直到没有后续工作。

### 4.5.1 预 Turn Compaction

在真正采样前，`run_turn` 先调用 `run_pre_sampling_compact`：

```rust
if let Err(err) = run_pre_sampling_compact(&sess, &turn_context, &mut client_session
).await { ... }
```

如果当前上下文已经逼近阈值，先把历史压缩成摘要，避免第一次请求就爆窗。

### 4.5.2 技能和插件注入

`build_skills_and_plugins` 根据用户输入中的 `@skill` / `@plugin` / `plugin://` 提及，以及项目 `AGENTS.md` 中的指令，生成要注入到模型上下文中的 `ResponseItem`。这些注入项被记录到对话历史中，成为模型可见的“系统/用户消息”。

### 4.5.3 采样循环

```rust
loop {
    // 1. 捕获当前 step 上下文
    let step_context = ...;

    // 2. 构造模型输入
    let sampling_request_input = sess.clone_history().await.for_prompt(...);

    // 3. 调用模型
    let result = run_sampling_request(
        sess, step_context, turn_extension_data,
        turn_diff_tracker, client_session, responses_metadata,
        sampling_request_input, cancellation_token.child_token()
    ).await;

    // 4. 处理结果：assistant message / tool calls / error
    match result {
        Ok((output, input)) => {
            if output.needs_follow_up {
                // 有工具调用或待处理输入，继续循环
                continue;
            } else {
                // 拿到最终 assistant message，结束 turn
                break;
            }
        }
        Err(...) => { ... }
    }
}
```

每次循环迭代是一次 **sampling request**：把当前历史喂给模型，模型返回一组输出项。输出项可能是：

- 普通文本/推理内容（assistant message）
- 函数调用（`FunctionCall`）
- 工具搜索调用（`ToolSearchCall`）
- 自定义工具调用（`CustomToolCall`）
- 图片生成调用等

## 4.6 一次模型采样：run_sampling_request

`run_sampling_request` 负责把 `StepContext` + 历史 + 工具列表变成一次实际的模型调用。

### 4.6.1 构造 ToolRouter

```rust
let router = built_tools(sess.as_ref(), step_context.as_ref(), &cancellation_token).await?;
```

`ToolRouter` 包含：

- `registry`：所有可用工具的元数据和执行器；
- `model_visible_specs`：当前 step 要暴露给模型的工具 schema；
- 扩展工具执行器、动态工具、tool suggest 候选等。

工具列表不是全局固定的，而是**每个 step 根据配置、权限、扩展重新构建**。例如某些 MCP server 可能在 turn 中途被启用/禁用。

### 4.6.2 构造 Prompt

```rust
let prompt = build_prompt(
    prompt_input,       // 当前历史
    router.as_ref(),    // 可用工具
    turn_context.as_ref(),
    base_instructions.clone(),
);
```

`Prompt` 包含：

- `input`：模型可见的对话历史（`Vec<ResponseItem>`）
- `tools`：工具 schema
- `parallel_tool_calls`：是否允许并行工具调用
- `base_instructions`：系统级基础指令

### 4.6.3 流式请求

```rust
let mut stream = client_session.stream(
    prompt,
    &turn_context.model_info,
    &turn_context.session_telemetry,
    turn_context.reasoning_effort.clone(),
    turn_context.reasoning_summary,
    turn_context.config.service_tier.clone(),
    responses_metadata,
    &inference_trace,
).await?;
```

模型返回的是 `ResponseEvent` 流，每个事件可能是：

- `ResponseEvent::AgentMessageDelta`：文本/推理片段
- `ResponseEvent::FunctionCall` / `FunctionCallDelta`：工具调用或参数片段
- `ResponseEvent::Completed`：本次采样完成
- 各种错误/ moderation 事件

### 4.6.4 处理输出并决定是否需要 follow-up

`try_run_sampling_request` 消费完整流后返回 `SamplingRequestResult`：

```rust
struct SamplingRequestResult {
    needs_follow_up: bool,              // 是否有工具调用需要执行
    last_agent_message: Option<String>, // 如果是纯文本，保存最终消息
}
```

如果 `needs_follow_up == true`，说明模型产生了工具调用；这些调用会被转换成 `ResponseInputItem` 写回历史，然后进入下一轮采样。

## 4.7 工具分发与执行

### 4.7.1 从 ResponseItem 到 ToolCall

`ToolRouter::build_tool_call` 把模型输出中的 `ResponseItem::FunctionCall`、`ResponseItem::CustomToolCall`、`ResponseItem::ToolSearchCall` 转成内部 `ToolCall`：

```rust
pub fn build_tool_call(item: ResponseItem) -> Result<Option<ToolCall>, FunctionCallError> {
    match item {
        ResponseItem::FunctionCall { name, namespace, arguments, call_id, .. } => {
            Ok(Some(ToolCall { tool_name: ToolName::new(namespace, name), call_id, payload: ... }))
        }
        ResponseItem::CustomToolCall { ... } => { ... }
        ...
        _ => Ok(None),
    }
}
```

### 4.7.2 ToolCallRuntime：并行 vs 串行

`ToolCallRuntime` 在 `codex-rs/core/src/tools/parallel.rs` 中。它为每个工具调用创建一个 `AbortOnDropHandle` 任务，并用一个 `RwLock` 控制并发：

```rust
let _guard = if supports_parallel {
    Either::Left(lock.read().await)   // 读锁：允许并行
} else {
    Either::Right(lock.write().await) // 写锁：独占执行
};

router.dispatch_tool_call_with_terminal_outcome(
    session, step_context, cancellation_token, tracker,
    dispatch_call, source, dispatch_terminal_outcome_reached,
).await
```

- 支持并行的工具（如多个文件读取）获取读锁，可以并发；
- 不支持并行的工具（如涉及 shell 环境状态变化）获取写锁，串行执行。

### 4.7.3 工具结果回流

工具执行完成后，结果被包装成 `AnyToolResult`，最终转换成 `ResponseInputItem::FunctionCallOutput` 或 `ResponseInputItem::CustomToolCallOutput`，通过 `record_conversation_items` 写回 `SessionState` 的历史。这样下一轮采样时，模型就能看到工具输出。

```
模型调用 tool_A ──▶ ToolCallRuntime 执行 ──▶ AnyToolResult
                                          │
                                          ▼
                              ResponseInputItem::FunctionCallOutput
                                          │
                                          ▼
                              SessionState 历史 += 该项
                                          │
                                          ▼
                              下一轮 sampling_request 输入包含 tool_A 输出
```

## 4.8 Token 管理与 Compaction

### 4.8.1 何时触发 Compaction

每次采样结束后，`run_turn` 会检查 `context_window_token_status`：

```rust
let token_status = super::context_window::context_window_token_status(sess.as_ref(), turn_context.as_ref()).await;
let token_limit_reached = token_status.token_limit_reached;
let should_roll_over = needs_follow_up && (sess.take_new_context_window_request().await || token_limit_reached);
```

触发条件：

1. **Token 触顶**：当前上下文 tokens 超过 `auto_compact_token_limit` 或模型硬窗口。
2. **显式请求 new context**：模型或系统请求开启新上下文窗口。

### 4.8.2 Compaction 做什么

`run_auto_compact` 会把当前历史中的早期内容压缩成一条 `Compaction` 或 `ContextCompaction` 记录，保留关键摘要而丢弃完整对话。这样后续请求就有足够空间继续处理。

Compaction 的时机有两种：

- **Pre-turn**：在 `run_turn` 开始时触发，防止第一次请求就爆窗。
- **Mid-turn**：在 turn 内部多次触发，支持长任务的持续处理。

### 4.8.3 Token Budget

如果启用了 `Feature::TokenBudget`，compaction 会走 `compact_token_budget` 路径，它会强制开启新上下文窗口并消耗 token budget，而不是普通的摘要压缩。

## 4.9 中断与 Steer

### 4.9.1 中断当前 Turn

`Op::Interrupt` 会触发 `interrupt(&sess)`，它会取消当前 active turn 的 `CancellationToken`。正在运行的模型流或工具任务收到取消信号后，会尽量优雅地收尾（或返回 aborted 结果）。

### 4.9.2 Steer Input

`steer_turn`（`CodexThread::steer_turn`）用于在 turn 运行期间追加用户输入。它发送一个 `Op::TurnInput`，`mode` 为 `Steer { expected_turn_id }`：

1. `turn_input::handle` 校验 `expected_turn_id` 与当前活动 turn 匹配；
2. 拒绝非 `Regular` 类型的 turn（如 Review、Compact）；
3. 把输入合并到 `additional_context`；
4. 把新输入放入 `input_queue`；
5. 返回 `Steered { turn_id }`（或 `NotSubmitted { reason }`）。

在 `run_turn` 的采样循环中，`can_drain_pending_input` 为 true 时，会从 `input_queue` 读取待处理输入并记录到历史。

`start_or_steer_turn` 是更常用的上层 API：如果当前无活动 turn 则自动 start，有活动 turn 则自动 steer，调用方无需关心当前状态。

## 4.10 设计取舍

| 取舍 | 选择 | 替代方案 | 原因 |
|------|------|----------|------|
| Session loop 单线程 vs 多线程 | 单线程串行消费 Submission | 每个 turn 一个独立任务 | 状态一致性简单，避免历史竞争 |
| 工具执行并行控制 | `RwLock` 按工具属性读写 | 全部串行或全部并行 | 保证必须串行的工具（如 shell）不冲突，允许无依赖的工具并发 |
| 每次 step 重建 ToolRouter | 重新扫描可用工具 | 全局静态工具表 | 支持 MCP server 动态增删、权限变化、Code Mode 开关 |
| Compaction 作为历史项目 | 生成 `Compaction` ResponseItem | 直接修改历史数据结构 | 模型可见、可审计、支持回滚 |
| 流式解析工具参数 | 边收流边解析 | 等流结束再解析 | 减少延迟，支持大参数文件的增量 diff |
| TurnContext 不可变快照 | 每次 turn 新建 | 全局可变配置 | 避免 turn 中途配置变化导致的不确定性 |

## 4.11 关键实现入口

| 概念 | 主要文件 | 说明 |
|------|----------|------|
| Session | `codex-rs/core/src/session/session.rs` | 核心状态机 |
| SessionIo / submit | `codex-rs/core/src/session/mod.rs` | 外部提交入口 |
| Submission Loop | `codex-rs/core/src/session/handlers.rs` | `submission_loop` |
| TurnInput 统一处理 | `codex-rs/core/src/session/turn_input.rs` | `turn_input::handle`、`turn_input::handle_recovery` |
| Op 枚举定义 | `codex-rs/protocol/src/protocol.rs` | 跨 crate 共享的协议类型 |
| Turn 执行 | `codex-rs/core/src/session/turn.rs` | `run_turn` |
| 采样请求 | `codex-rs/core/src/session/turn.rs` | `run_sampling_request`、`try_run_sampling_request` |
| TurnContext | `codex-rs/core/src/session/turn_context.rs` | Turn 配置快照 |
| StepContext | `codex-rs/core/src/session/step_context.rs` | 单次采样步骤上下文 |
| 模型客户端 | `codex-rs/core/src/client.rs` | `ModelClient`、`ModelClientSession` |
| Prompt | `codex-rs/core/src/client_common.rs` | 模型请求输入 |
| 工具路由 | `codex-rs/core/src/tools/router.rs` | `ToolRouter` |
| 工具并行执行 | `codex-rs/core/src/tools/parallel.rs` | `ToolCallRuntime` |
| 工具注册表 | `codex-rs/core/src/tools/registry.rs` | `ToolRegistry` |
| 上下文窗口 | `codex-rs/core/src/session/context_window.rs` | `context_window_token_status` |
| Compaction | `codex-rs/core/src/compact.rs` | 上下文压缩逻辑 |
| Token Budget | `codex-rs/core/src/compact_token_budget.rs` | TokenBudget 模式压缩 |
| Input Queue | `codex-rs/core/src/session/input_queue.rs` | 待处理用户输入 |

## 4.12 小结

Codex CLI 的 Agent Loop 可以概括为：**一个以 `Session` 为中心的串行状态机，通过 `Turn` 把“用户输入”转化为“模型采样 → 工具执行 → 结果回流”的循环**。

- `SessionIo` 把所有外部动作序列化成 `Submission`；
- `submission_loop` 按顺序消费这些操作；
- `run_turn` 负责一次 turn 内的多轮采样；
- 每次采样都会根据当前 step 重新构建 `ToolRouter`；
- 工具通过 `ToolCallRuntime` 并行或串行执行，结果写回历史；
- 当 Token 触顶时，`run_auto_compact` 会压缩历史，让循环继续；
- `start_or_steer_turn` / `steer_turn` / `recover_turn_if_idle` 提供了统一的用户输入入口，原子性地决定 start、steer 或拒绝；`interrupt` 提供紧急中断。

下一章（工具系统与扩展点）会深入 `ToolRouter`、`ToolRegistry`、各种 handler 的实现，以及 Extensions / MCP / Skills 如何把新能力注册进这个循环。

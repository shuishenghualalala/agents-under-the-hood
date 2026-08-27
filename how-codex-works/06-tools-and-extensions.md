# 第 6 章：工具系统与扩展点

> 本章导读：Agent Loop 解决了“模型调用与工具执行如何交替”的问题，但没有回答“工具有哪些、从哪里来、如何新增”。本章会拆解 Codex CLI 的工具系统：模型可见的 `ToolSpec`、运行时 `ToolRegistry`、按 step 重建的 `ToolRouter`，以及 Extensions / MCP / Skills / Plugins 四种扩展机制如何在不修改核心循环的前提下，把新能力注册进系统。

## 6.1 本章要解决的问题

Codex CLI 的能力边界很大程度上由工具决定。工具系统需要回答：

1. **模型看到什么工具？** 不是全局固定，而是每次采样前根据当前配置、权限、环境动态决定。
2. **工具如何被调用？** 模型输出 tool call 后，系统如何找到对应的执行器并安全运行。
3. **新工具如何接入？** 不能每次新增能力都改 `core`。
4. **外部工具（MCP）如何统一？** 外部 MCP server 的 schema 和生命周期如何管理。
5. **提示词片段（Skills）如何注入？** 项目级/用户级提示词何时进入模型上下文。
6. **插件（Plugins）如何 Discovery 和安装？** 商店插件如何变成可用工具。

## 6.2 工具系统全景

Codex CLI 的工具系统可以概括为**三层架构**：

```
┌─────────────────────────────────────────────┐
│  模型可见层：ToolSpec                         │
│  每次采样前动态生成的 JSON schema 列表         │
├─────────────────────────────────────────────┤
│  运行时注册层：ToolRegistry / ToolRouter      │
│  把 ToolName 映射到 CoreToolRuntime 执行器     │
├─────────────────────────────────────────────┤
│  执行层：handlers / exec-server / MCP client  │
│  实际运行命令、读写文件、调用外部 API          │
└─────────────────────────────────────────────┘
```

一次工具调用的完整路径：

```
模型返回 FunctionCall / CustomToolCall / ToolSearchCall
    │
    ▼
ToolRouter::build_tool_call(item) → ToolCall { tool_name, call_id, payload }
    │
    ▼
ToolCallRuntime::handle_tool_call(call)
    │
    ▼
ToolRegistry::dispatch_any_with_terminal_outcome(invocation)
    │
    ▼
CoreToolRuntime::handle(invocation) → AnyToolResult
    │
    ▼
AnyToolResult::into_response() → ResponseInputItem
    │
    ▼
写回 SessionState 历史，进入下一轮采样
```

## 6.3 核心抽象

### 6.3.1 ToolSpec：模型可见的 JSON Schema

`ToolSpec` 定义在 `codex-tools` crate 中，对应 OpenAI Responses API 的 tool schema。它描述：

- 工具命名空间（namespace）和名称
- 输入参数 JSON Schema
- 描述（description）
- 是否支持并行调用
- 是否“只读”等 annotations

`ToolRouter::model_visible_specs()` 返回当前 step 要发给模型的 schema 列表。

### 6.3.2 ToolName：统一命名

```rust
pub struct ToolName {
    pub namespace: Option<String>,
    pub name: String,
}
```

Codex 用 namespace 区分工具来源：

| namespace | 例子 | 来源 |
|-----------|------|------|
| `None` | `shell`, `apply_patch` | 内置核心工具 |
| `mcp__servername` | `mcp__github__list_repos` | 外部 MCP server |
| `web` | `web.run` | web-search 扩展 |
| `image_gen` | `image_gen.imagegen` | image-generation 扩展 |
| `agents` | `agents.spawn` | multi-agent 工具 |

### 6.3.3 CoreToolRuntime：执行器 trait

`CoreToolRuntime` 在 `codex-rs/core/src/tools/registry.rs` 中定义，它扩展了 `codex_tools::ToolExecutor`：

```rust
pub(crate) trait CoreToolRuntime: ToolExecutor<ToolInvocation> {
    fn matches_kind(&self, payload: &ToolPayload) -> bool;
    fn waits_for_runtime_cancellation(&self) -> bool { false }
    fn telemetry_tags(&self, ...) -> BoxFuture<...>;
    fn post_tool_use_payload(...);   // 用于 hooks
    fn pre_tool_use_payload(...);    // 用于 hooks
    fn create_diff_consumer(&self) -> Option<Box<dyn ToolArgumentDiffConsumer>>;
}
```

每个工具 handler 都实现 `CoreToolRuntime`，并通过 `ToolRegistry::from_tools` 注册。

### 6.3.4 ToolInvocation：调用上下文

`ToolInvocation` 不是简单的参数，它携带了完整的执行上下文：

- `session: Arc<Session>`
- `step_context: Arc<StepContext>`
- `turn: Arc<TurnContext>`
- `call_id`, `tool_name`, `payload`
- 取消 token

这让工具在执行时可以访问历史、状态、配置、环境，而不是只拿到 JSON 参数。

## 6.4 每次采样前重建 ToolRouter

在 `run_sampling_request` 中，Codex 不会使用一个全局工具表，而是**为每个 step 重新构建 `ToolRouter`**：

```rust
let router = built_tools(sess.as_ref(), step_context.as_ref(), &cancellation_token).await?;
```

`built_tools` 最终调用 `codex-rs/core/src/tools/spec_plan.rs` 中的 `build_tool_router`：

```rust
pub(crate) fn build_tool_router(
    step_context: &StepContext,
    params: ToolRouterParams<'_>,
    tool_search_handler_cache: &ToolSearchHandlerCache,
) -> ToolRouter {
    let (model_visible_specs, registry) =
        build_tool_specs_and_registry(step_context, params, tool_search_handler_cache);
    ToolRouter::from_parts(registry, model_visible_specs)
}
```

`build_tool_specs_and_registry` 的工作流程：

1. **收集工具来源**（`add_tool_sources`）：
   - `add_shell_tools`：shell / unified exec 相关工具
   - `add_mcp_resource_tools`：MCP resource 相关工具
   - `add_core_utility_tools`：plan、request_user_input、request_permissions、current_time、sleep 等
   - `add_collaboration_tools`：multi-agent 工具
   - 加入 `params.tool_runtimes`：扩展贡献的运行时
   - `add_extension_tools`：Extension API 贡献的工具
   - `add_dynamic_tools`：动态工具
   - `hosted_model_tool_specs`：托管在模型侧的工具（如 hosted web search）
2. **应用 namespace override**：把 `code_mode.direct_only_tool_namespaces` 中的工具标记为 `DirectModelOnly`。
3. **追加 tool_search 执行器**：当 `tool_search` 启用且存在 deferred 工具时。
4. **前置 code mode 执行器**：如果当前 step 在 Code Mode 下。
5. **构建 model-visible specs 和 registry**：根据 `ToolExposure` 过滤出要发给模型的 schema，并创建 `ToolRegistry`。

### 6.4.1 ToolExposure：可见性控制

```rust
pub enum ToolExposure {
    Direct,           // 模型直接可见
    DirectModelOnly,  // 仅模型可见，不通过 tool_search
    Deferred,         // 不直接暴露，可通过 tool_search 发现
    Hidden,           // 仅用于内部 dispatch，模型不可见
}
```

这个设计让同一套 handler 可以根据配置以不同方式暴露给模型。例如 `tool_search` 可以把大量 deferred 工具隐藏起来，只在模型明确搜索时展示。

## 6.5 内置工具概览

`codex-rs/core/src/tools/handlers/` 目录包含所有内置工具实现：

| 工具 | handler 文件 | 用途 |
|------|-------------|------|
| `shell` | `shell/shell.rs` | 执行 shell 命令（通过 exec-server） |
| `apply_patch` | `apply_patch.rs` | 应用模型生成的代码补丁 |
| `multi_agents` | `multi_agents/` | spawn/wait/send/list 子 Agent |
| `mcp` / `mcp_resource` | `mcp.rs`, `mcp_resource.rs` | 调用外部 MCP server 工具/读取资源 |
| `web_search` | `hosted_spec.rs`（hosted）+ `tool_search.rs` | 网络搜索 |
| `request_user_input` | `request_user_input.rs` | 向用户请求输入 |
| `request_permissions` | `request_permissions.rs` | 请求权限变更 |
| `plan` | `plan.rs` | Plan mode 工具 |
| `current_time` / `sleep` | `current_time.rs`, `sleep.rs` | 时间相关 |
| `new_context_window` / `get_context_remaining` | `new_context_window.rs`, `get_context_remaining.rs` | Token budget 工具 |
| `view_image` | `view_image.rs` | 查看图片 |
| `test_sync` | `test_sync.rs` | 测试同步工具 |
| `dynamic` | `dynamic.rs` | 动态工具 |
| `extension_tools` | `extension_tools.rs` | Extension API 工具的适配器 |

## 6.6 工具执行与并发控制

### 6.6.1 ToolCallRuntime

`ToolCallRuntime` 是工具执行的调度器，位于 `codex-rs/core/src/tools/parallel.rs`。它为每个工具调用 spawn 一个 `AbortOnDropHandle` 任务，并用 `RwLock` 控制并发：

```rust
let _guard = if supports_parallel {
    Either::Left(lock.read().await)   // 并行：多个读锁共存
} else {
    Either::Right(lock.write().await) // 串行：写锁独占
};

router.dispatch_tool_call_with_terminal_outcome(...).await
```

如果工具被标记为 `supports_parallel_tool_calls` 或 MCP annotations 声明 `read_only_hint=true`，则使用读锁并发执行；否则使用写锁串行执行。

### 6.6.2 取消与Abort

当用户中断或 turn 结束时，`ToolCallRuntime` 会取消正在执行的工具：

```rust
tokio::select! {
    res = &mut dispatch_handle => res.map_err(...)?,
    _ = cancellation_token.cancelled() => {
        if terminal_outcome_reached.load(...) || dispatch_handle.is_finished() {
            // 已经有终态，等待结果
        } else {
            // 根据工具属性决定 abort 或等待 teardown
        }
    }
}
```

### 6.6.3 结果包装

工具执行结果统一包装成 `AnyToolResult`：

```rust
pub(crate) struct AnyToolResult {
    pub(crate) call_id: String,
    pub(crate) payload: ToolPayload,
    pub(crate) result: Box<dyn ToolOutput>,
    pub(crate) post_tool_use_payload: Option<PostToolUsePayload>,
}
```

`AnyToolResult::into_response()` 把结果转成 `ResponseInputItem`，写回历史。

## 6.7 扩展点：四种能力接入方式

Codex CLI 把“新增能力”与“核心循环”解耦，提供了四种扩展机制：

### 6.7.1 Extensions（原生 Rust 扩展）

`codex-rs/ext/*` 下的 crate 是原生 Rust 扩展，通过 `ExtensionRegistryBuilder` 注册：

- `ext/agent`：Agent 相关（较薄）
- `ext/goal`：目标管理
- `ext/guardian`：安全审查
- `ext/image-generation`：图片生成
- `ext/memories`：记忆读写
- `ext/web-search`：网络搜索
- `ext/mcp`：MCP 扩展相关

以 `ext/memories/src/extension.rs` 为例：

```rust
pub fn install(
    registry: &mut ExtensionRegistryBuilder<Config>,
    metrics_client: Option<MetricsClient>,
) {
    let extension = Arc::new(MemoriesExtension::new(metrics_client));
    registry.thread_lifecycle_contributor(extension.clone());
    registry.config_contributor(extension.clone());
    registry.tool_contributor(extension);
}
```

一个扩展可以同时是 `ThreadLifecycleContributor`、`ConfigContributor`、`ToolContributor` 等多种角色。

### 6.7.2 Extension API 贡献点

`codex_extension_api` 定义了 10+ 种贡献点（位于 `codex-rs/ext/extension-api/src/contributors.rs`）：

| 贡献点 | 用途 |
|--------|------|
| `ToolContributor` | 提供原生工具 |
| `ContextContributor` | 向 prompt 注入片段 |
| `TurnInputContributor` | 向 turn 输入注入上下文片段 |
| `TurnItemContributor` | 修改/观察 turn 输出项 |
| `ThreadLifecycleContributor` | 监听 thread 启动/恢复/空闲/停止 |
| `TurnLifecycleContributor` | 监听 turn 启动/停止/中止/错误 |
| `ToolLifecycleContributor` | 监听工具开始/结束 |
| `McpServerContributor` | 贡献 MCP server 配置 |
| `ConfigContributor` | 监听配置变化 |
| `TokenUsageContributor` | 监听 token 使用 |
| `SkillInvocationContributor` | 监听 skill 调用 |
| `ApprovalReviewContributor` | 参与审批评审 |

`ExtensionRegistry` 是构建后的不可变注册表，`Session` 持有它并在合适时机调用各贡献点。

### 6.7.3 MCP（Model Context Protocol）

MCP 是 Codex 接入外部工具的标准协议。相关 crate：

- `codex-rs/codex-mcp`：MCP runtime、连接管理、工具发现
- `codex-rs/core/src/mcp.rs`：`McpManager`
- `codex-rs/core/src/tools/handlers/mcp.rs`：MCP 工具的 handler
- `codex-rs/mcp-server`：Codex 作为 MCP server 的入口

`McpManager` 负责：

1. 从配置、插件、扩展中解析 MCP server 列表；
2. 管理连接生命周期；
3. 把 server 的工具 schema 暴露给 `ToolRouter`；
4. 在调用时通过 `handle_mcp_tool_call` 转发到对应 server。

MCP 工具的命名约定是 `mcp__servername__toolname`，handler 在收到调用时解析出 server 名和工具名，通过 MCP client 发送请求。

### 6.7.4 Skills

Skills 是声明式的提示词/工作流片段。相关 crate：

- `codex-rs/skills`：skill 数据模型和系统 skill 安装
- `codex-rs/core-skills`：skill 加载、渲染、注入服务
- `codex-rs/core/src/skills.rs`：与 core 的集成

`SkillsService` 扫描 `CODEX_HOME/skills` 和项目 `skills` 目录，根据用户输入中的 `@skill_name` 提及或隐式触发条件，把 skill 的提示词片段注入到模型上下文中。

### 6.7.5 Plugins

Plugins 是 Codex 的连接器/插件系统。相关 crate：

- `codex-rs/core-plugins`：`PluginsManager`，管理插件商店、安装、加载
- `codex-rs/plugin`：插件协议

Plugins 可以：

1. 通过 marketplace discovery 推荐安装；
2. 提供 MCP server；
3. 提供 skills；
4. 通过 `ToolSuggest` 机制在模型需要时推荐自己。

## 6.8 Code Mode：工具系统的特殊形态

Code Mode 是一种让模型通过结构化的“代码”而非自然语言来调用工具的模式。它在 `codex_code_mode` crate 中实现。

在 `spec_plan.rs` 中：

```rust
fn prepend_code_mode_executors(context: &CoreToolPlanContext<'_>, planned_tools: &mut PlannedTools) {
    let code_mode_executors = build_code_mode_executors(turn_context, planned_tools.runtimes());
    planned_tools.runtimes.splice(0..0, code_mode_executors);
}
```

Code Mode 会把普通工具的 schema 改写成代码形式的 schema，并前置 `codex_code_mode::PUBLIC_TOOL_NAME` 和 `WAIT_TOOL_NAME` 两个特殊工具。

## 6.9 工具搜索：Tool Search

当启用了大量 deferred 工具时，Codex 会暴露一个 `tool_search` 工具。模型可以先用 `tool_search` 搜索可用工具，再调用具体工具。这减少了单次请求的 schema 大小。

`tool_search` 的实现涉及：

- `codex-rs/core/src/tools/handlers/tool_search.rs`
- `codex-rs/core/src/tools/spec_plan.rs` 中的 `append_tool_search_executor`
- `codex_tools::ToolSearchInfo`

## 6.10 设计取舍

| 取舍 | 选择 | 替代方案 | 原因 |
|------|------|----------|------|
| 每次 step 重建 ToolRouter | 动态重建 | 全局静态工具表 | 支持 MCP 动态增删、Code Mode 切换、权限变化 |
| 工具命名用 namespace | `ToolName { namespace, name }` | 扁平字符串 | 区分来源，避免冲突，支持同名不同实现 |
| CoreToolRuntime 扩展 ToolExecutor | 增加 hooks/telemetry/diff consumer | 每个工具自己处理 | 统一切面，避免每个 handler 重复实现 |
| RwLock 控制并行 | 读锁并行、写锁串行 | 全部 spawn 自由竞争 | 保证 shell 等状态敏感工具的原子性 |
| 工具结果统一 ResponseInputItem | 标准化回流 | 每个工具自定义回流 | 历史格式统一，模型消费一致 |
| Extension 多种 contributor | 一个扩展注册多个角色 | 每个能力单独 crate | 相关能力内聚，减少跨 crate 协调 |
| MCP 工具前缀 `mcp__` | 特殊命名空间 | 与普通工具无差别 | 防止命名冲突，便于 hook/审计 |

## 6.11 关键实现入口

| 概念 | 主要文件 | 说明 |
|------|----------|------|
| ToolRouter | `codex-rs/core/src/tools/router.rs` | 工具路由与 build_tool_call |
| ToolRegistry | `codex-rs/core/src/tools/registry.rs` | 运行时工具注册表 |
| 工具规格计划 | `codex-rs/core/src/tools/spec_plan.rs` | `build_tool_router`、`add_tool_sources` |
| 工具并行执行 | `codex-rs/core/src/tools/parallel.rs` | `ToolCallRuntime` |
| 工具调用上下文 | `codex-rs/core/src/tools/context.rs` | `ToolInvocation`、`ToolPayload`、`ToolOutput` |
| 内置工具目录 | `codex-rs/core/src/tools/handlers/` | shell、apply_patch、multi_agents、mcp 等 |
| Extension API | `codex-rs/ext/extension-api/src/lib.rs` | 扩展能力 trait |
| Extension 贡献点 | `codex-rs/ext/extension-api/src/contributors.rs` | 各种 Contributor trait |
| Extension 注册表 | `codex-rs/ext/extension-api/src/registry.rs` | `ExtensionRegistryBuilder`、`ExtensionRegistry` |
| MCP 管理器 | `codex-rs/core/src/mcp.rs` | `McpManager` |
| MCP 工具 handler | `codex-rs/core/src/tools/handlers/mcp.rs` | `McpHandler` |
| MCP runtime | `codex-rs/codex-mcp/src/lib.rs` | MCP 连接与调用 |
| Skills 服务 | `codex-rs/core-skills/src/service.rs` | `SkillsService` |
| Skills 注入 | `codex-rs/core-skills/src/injection.rs` | `build_skill_injections` |
| Core skills 集成 | `codex-rs/core/src/skills.rs` | core 与 skills 的桥接 |
| Plugins 管理 | `codex-rs/core-plugins/src/manager.rs` | `PluginsManager` |
| Code Mode | `codex-rs/code-mode/src/lib.rs` | Code Mode 执行器 |

## 6.12 小结

Codex CLI 的工具系统是一个**动态、分层、可扩展**的架构：

- `ToolSpec` 是模型可见的 schema；
- `ToolRegistry` 按 `ToolName` 索引所有执行器；
- `ToolRouter` 在每个 sampling step 重新构建，确保工具列表始终反映当前配置；
- `ToolCallRuntime` 用 `RwLock` 控制并行/串行执行，并处理取消；
- 结果统一包装成 `ResponseInputItem` 回写历史；
- Extensions / MCP / Skills / Plugins 四种机制让新能力可以独立开发、独立注册，不污染核心 Agent Loop。

下一章（执行沙箱与权限）会深入工具执行最敏感的部分：shell 命令和文件操作如何通过 `exec-server` 在沙箱中运行，以及 `PermissionProfile` 如何控制读写网络权限。

# 专题：Codex CLI 有“定时任务”吗？——时间、异步与后台机制

> 本章导读：很多工程师会好奇：Codex CLI 作为本地 AI 代理，有没有类似 cron 的定时任务调度器？本章会先给出明确答案，再拆解 Codex 中与“时间”“等待”“后台”相关的技术设计。这些设计不是传统定时任务，但恰恰体现了 Codex 的架构亮点：**事件驱动优于轮询、模型主动等待优于系统强制调度、可中断性优于阻塞、可测试性优于隐式系统调用**。

## 1. 直接回答：Codex CLI 本地没有传统 cron 式定时任务调度器

如果你期待的是一个类似 Linux `cron` 或分布式调度器的东西——**Codex CLI 本地没有**。它不会在后台按固定时间表自动执行用户任务，也没有一个全局的 job scheduler。

但这不代表 Codex 不关心时间。相反，它有几种与时间/异步/后台相关的机制，分布在不同层面：

| 机制 | 类型 | 是否是“定时任务” | 关键文件 |
|------|------|------------------|----------|
| `clock.sleep` 工具 | 模型主动调用的等待工具 | 否（单次、turn 内） | `core/src/tools/handlers/sleep.rs` |
| Current Time Reminder | 按间隔注入时间上下文 | 否（turn-driven） | `core/src/session/time_reminder.rs` |
| Thread Idle Lifecycle | 扩展在 thread 空闲时触发 | 否（事件驱动） | `ext/extension-api/src/contributors.rs` |
| Models Refresh Worker | 后台定时轮询 | **是** | `app-server/src/models_refresh_worker.rs` |
| Skills Watcher | 文件系统事件监听 + throttle | 否（事件驱动） | `app-server/src/skills_watcher.rs`、`file-watcher/src/lib.rs` |
| Background Terminals | 后台长时间运行进程 | 否（进程管理） | `core/src/unified_exec/`、`core/src/codex_thread.rs` |
| Cloud Tasks | 云端异步任务 | 是（云端调度） | `cloud-tasks/src/lib.rs` |

下面按技术亮点逐一展开。

## 2. 技术亮点一：`TimeProvider` 抽象——让时间可注入、可测试

Codex 没有把 `tokio::time::sleep` 和 `Utc::now()` 直接散落在业务代码里，而是定义了一个 `TimeProvider` trait（`core/src/current_time.rs`）：

```rust
pub trait TimeProvider: Send + Sync {
    fn current_time(&self, thread_id: ThreadId) -> TimeFuture<'_>;
    fn sleep(&self, thread_id: ThreadId, duration: Duration) -> SleepFuture<'_>;
}
```

默认实现是 `SystemTimeProvider`，它调用 `Utc::now()` 和 `tokio::time::sleep`。但 trait 允许注入外部时间源（例如测试用的虚拟时钟、或未来可能的硬件时钟）。

**为什么这是亮点？**

- 测试时可以注入一个虚拟 `TimeProvider`，让涉及 12 小时 sleep 的测试不必真的等 12 小时；
- 业务代码不直接依赖系统调用，便于模拟和跨环境移植；
- `thread_id` 参数让外部时钟源可以按线程追踪时间行为。

## 3. 技术亮点二：`clock.sleep`——模型驱动的、可中断的等待

`clock.sleep` 是 Codex 暴露给模型的一个工具（`core/src/tools/handlers/sleep.rs`），让模型可以主动说“我先等一会儿”：

```rust
pub struct SleepHandler;
const MAX_SLEEP_DURATION_MS: u64 = 12 * 60 * 60 * 1000; // 最长 12 小时
```

实现要点：

1. 调用 `time_provider.sleep()` 异步等待；
2. 同时监听 `input_queue.subscribe_activity()`；
3. 如果用户在等待期间输入了新内容，`tokio::select!` 会提前唤醒 sleep；
4. 工具返回“Sleep interrupted by new input.”或“Sleep completed.”。

```rust
tokio::select! {
    result = &mut sleep => {
        // 正常完成
    }
    result = activity_rx.changed() => {
        // 被新输入中断
    }
}
```

**为什么这是亮点？**

- **可中断性**：长时间等待不会阻塞用户交互，这是本地 CLI 的关键体验；
- **模型可控**：不是系统强制调度，而是模型根据任务自主决定等待多久；
- **资源友好**：等待期间不占用模型 token，也不 polling；
- **有界**：最大 12 小时，防止无限等待。

这与“定时任务”的区别在于：sleep 是 turn 内工具调用，不是到点就触发的独立任务。

## 4. 技术亮点三：Current Time Reminder——让模型“感知时间”而不 polling

`CurrentTimeReminder`（`core/src/session/time_reminder.rs`）在每次模型采样前检查是否需要注入当前时间。它维护了 `CurrentTimeReminderState`：

```rust
pub(crate) struct CurrentTimeReminderState {
    last_delivery_time: Option<DateTime<Utc>>,
    last_window_id: Option<String>,
    pending_user_or_tool_output_boundary: bool,
}
```

判断逻辑：

- 是否进入新 window；
- 距离上次提醒是否超过 `reminder_interval_seconds`；
- 是否处于用户/工具输出边界（取决于 `delivery_mode`）。

如果条件满足，就把 `CurrentTimeReminder` 作为 `ContextualUserFragment` 注入模型上下文。

**为什么这是亮点？**

- **不是定时器，而是 turn-driven**：只在模型采样时检查，不会单独启动后台线程 polling；
- **可配置投递模式**：
  - `Interval`：固定间隔；
  - `AfterUserOrToolOutput`：只在有意义的边界后投递，避免每次请求都塞时间；
- **状态极小**：只有三个字段，维护成本低。

## 5. 技术亮点四：Thread Idle Lifecycle——扩展的“空闲钩子”

Codex 没有内置定时任务，但给扩展提供了一个 `ThreadLifecycleContributor::on_thread_idle` 钩子（`ext/extension-api/src/contributors.rs`）：

```rust
pub trait ThreadLifecycleContributor<C: Sync>: Send + Sync {
    fn on_thread_idle<'a>(&'a self, input: ThreadIdleInput<'a>) -> ExtensionFuture<'a, ()>;
}
```

当 thread 完成当前 turn、没有 pending work 时，Session 会调用所有注册的 `on_thread_idle` contributor。扩展可以利用这个钩子：

- 提交 follow-up 输入；
- 启动后台同步；
- 检查外部事件；
- 实现自己的定时/轮询逻辑。

Codex 只负责通知“你现在空闲了”，至于扩展要做什么、要不要自己设 timer，由扩展决定。

**为什么这是亮点？**

- **解耦**：核心不实现定时任务，但提供了比定时任务更通用的扩展点；
- **安全**：扩展触发的新输入仍然要经过 Session 的 submission loop，不会绕过核心状态机；
- **可组合**：多个扩展可以各自实现自己的“定时”策略。

## 6. 技术亮点五：Models Refresh Worker——真正的后台定时轮询

在 `app-server/src/models_refresh_worker.rs` 中，Codex 确实有一个定时运行的后台 worker：

```rust
const MODELS_REFRESH_INTERVAL: Duration = Duration::from_secs(3 * 60);

pub(crate) fn spawn(models_manager: &SharedModelsManager, ...) -> ModelsRefreshWorker {
    spawn_with_interval(models_manager, http_client_factory, MODELS_REFRESH_INTERVAL)
}
```

它每 3 分钟调用 `models_manager.list_models(RefreshStrategy::Online, ...)`，从服务端刷新可用模型列表。

实现要点：

- 使用 `Weak<SharedModelsManager>` 避免阻止 models manager 释放；
- 使用 `CancellationToken` 支持优雅关闭；
- `tokio::select!` 同时监听取消信号和 sleep，避免关闭时还要等完整周期。

```rust
loop {
    if worker_shutdown.is_cancelled() { break; }
    let Some(models_manager) = models_manager.upgrade() else { break; };
    models_manager.list_models(...).await;
    drop(models_manager);

    tokio::select! {
        _ = worker_shutdown.cancelled() => break,
        _ = tokio::time::sleep(refresh_interval) => {}
    }
}
```

**为什么这是亮点？**

- **生命周期清晰**：`ModelsRefreshWorker` 实现 `Drop`，关闭 app-server 时自动取消；
- **不持有强引用**：`Weak` 保证如果 models manager 被释放，worker 自然退出；
- **取消即停**：sleep 可被取消，避免 graceful shutdown 卡住。

## 7. 技术亮点六：Skills Watcher——事件驱动 + Throttle

`SkillsWatcher`（`app-server/src/skills_watcher.rs`）监听 skill 文件目录的变化。它不是定时轮询，而是基于 `codex_file_watcher`：

```rust
const WATCHER_THROTTLE_INTERVAL: Duration = Duration::from_secs(10);
```

`FileWatcher`（`file-watcher/src/lib.rs`）封装了跨平台文件系统事件监听：

- 基于 `notify` crate；
- 支持多订阅者，每个订阅者可注册不同路径；
- 路径 ref-counting，避免重复注册；
- missing path fallback：如果目录暂时不存在，会监听其祖先目录并在创建后切换；
- `ThrottledWatchReceiver` 对事件做 throttle，防止高频文件变化触发频繁刷新。

```rust
impl ThrottledWatchReceiver {
    pub async fn recv(&mut self) -> Option<FileWatcherEvent> {
        if let Some(next_allowed) = self.next_allowed {
            sleep_until(next_allowed).await;
        }
        // ...
        self.next_allowed = Some(Instant::now() + self.interval);
    }
}
```

**为什么这是亮点？**

- **事件驱动优于轮询**：文件变化立即感知，而不是每 N 秒扫描一次；
- **throttle 避免抖动**：批量处理短时间内的大量事件；
- **跨平台抽象**：屏蔽 macOS/Linux/Windows 文件监听差异；
- **RAII 注册**：`WatchRegistration` drop 时自动取消监听。

## 8. 技术亮点七：Background Terminals——后台长活进程

Codex 支持让 shell 命令在后台终端运行，而不是阻塞当前 turn。相关抽象：

```rust
pub struct BackgroundTerminalInfo {
    pub item_id: String,
    pub process_id: String,
    pub command: String,
    pub cwd: PathUri,
}
```

后台终端由 `UnifiedExecProcessManager`（`core/src/unified_exec/`）管理，用户可以在后续 turn 中通过 `write_stdin` 与之交互，或通过 `CleanBackgroundTerminals` 清理。

**为什么这是亮点？**

- **本地长期运行**：编译、测试服务器、日志监控等任务可以在后台跑；
- **不阻塞 Agent Loop**：后台进程不占用当前 turn；
- **可交互**：后续可以 stdin/stdout 交互，像真实终端一样。

## 9. 技术亮点八：Cloud Tasks——把“异步任务”交给云端

如果你确实需要“提交一个任务，让它稍后/在云端完成”，Codex 提供了 `codex cloud` 子命令（`cloud-tasks/src/lib.rs`）：

```rust
async fn run_exec_command(args: crate::cli::ExecCommand) -> anyhow::Result<()> {
    let ctx = init_backend("codex_cloud_tasks_exec").await?;
    let created = CloudBackend::create_task(
        &*ctx.backend, &env_id, &prompt, &git_ref, /*qa_mode*/ false, attempts
    ).await?;
    println!("{}", util::task_url(&ctx.base_url, &created.id.0));
}
```

它把任务提交到 Codex Cloud，由云端环境执行。本地 CLI 只负责提交和展示结果链接。

**为什么这是亮点？**

- **明确边界**：本地 CLI 不做云端调度，云端做云端擅长的事；
- **不混淆概念**：用户不会误以为本地 Codex 在后台自动运行；
- **可扩展**：云端可以有自己的队列、调度、重试策略。

## 10. 设计取舍：为什么 Codex 不做一个通用定时任务调度器？

| 取舍 | 选择 | 替代方案 | 原因 |
|------|------|----------|------|
| 本地无 cron | 不做通用定时调度 | 内嵌 cron 或 job queue | 本地 CLI 的生命周期由用户控制，后台定时任务不可靠 |
| Sleep 交给模型 | `clock.sleep` 工具 | 系统级 sleep task | 让模型自己决定何时等待、等待多久 |
| 时间可注入 | `TimeProvider` trait | 直接调用系统时间 | 测试可控、环境可移植 |
| 刷新用 Weak 引用 | `Weak<SharedModelsManager>` | `Arc` 强引用 | 避免 worker 阻止资源释放 |
| 文件监听用事件 | `notify` + throttle | 定时扫描目录 | 及时、省电、无 I/O 浪费 |
| 空闲工作用扩展钩子 | `on_thread_idle` | 核心内置定时器 | 扩展自治，核心保持简洁 |
| 云端任务交给云端 | `cloud-tasks` crate | 本地模拟云端调度 | 职责清晰，避免本地过载 |

## 11. 关键实现入口

| 概念 | 主要文件 | 说明 |
|------|----------|------|
| TimeProvider | `codex-rs/core/src/current_time.rs` | 可注入的时间源抽象 |
| Sleep 工具 | `codex-rs/core/src/tools/handlers/sleep.rs` | `clock.sleep`，最长 12 小时，可中断 |
| Current Time Reminder | `codex-rs/core/src/session/time_reminder.rs` | turn-driven 时间提醒 |
| Thread Idle 钩子 | `codex-rs/ext/extension-api/src/contributors.rs` | `ThreadLifecycleContributor::on_thread_idle` |
| Models 刷新 Worker | `codex-rs/app-server/src/models_refresh_worker.rs` | 每 3 分钟刷新模型目录 |
| Skills Watcher | `codex-rs/app-server/src/skills_watcher.rs` | skill 文件变化监听 |
| File Watcher | `codex-rs/file-watcher/src/lib.rs` | 跨平台文件监听 + throttle |
| Background Terminals | `codex-rs/core/src/codex_thread.rs`、`core/src/unified_exec/` | 后台终端进程 |
| Cloud Tasks | `codex-rs/cloud-tasks/src/lib.rs` | 云端任务提交 |
| MCP 刷新 | `codex-rs/app-server/src/mcp_refresh.rs` | 配置变更时刷新所有线程 MCP |

## 12. 小结

Codex CLI **没有传统意义上的本地定时任务调度器**，但它的“时间观”非常有特色：

- **模型主动等待**：`clock.sleep` 让等待成为模型可调度的工具，且可被用户输入中断；
- **turn-driven 时间意识**：`CurrentTimeReminder` 在采样时按需注入时间，不单独 polling；
- **扩展自治**：`ThreadIdle` 钩子让扩展实现各自的后台/定时逻辑，核心保持干净；
- **必要的轮询很克制**：只有 `ModelsRefreshWorker` 是真正的后台定时轮询，且生命周期管理精良；
- **事件驱动优先**：`SkillsWatcher` 用文件系统事件 + throttle 替代轮询；
- **云端异步明确分离**：`cloud-tasks` 把真正的异步任务交给云端。

如果你需要在 Codex 上实现“定时任务”，推荐路径是：

1. 短期等待（秒/分钟级）：让模型调用 `clock.sleep`；
2. 文件/配置变化响应：用 `FileWatcher` 或 `SkillsWatcher`；
3. thread 空闲后自动做事：写 Extension 实现 `on_thread_idle`；
4. 真正的离线/云端任务：用 `codex cloud` 提交到 Codex Cloud。

这种设计体现了一个核心原则：**本地 CLI 不要假装自己是常驻调度器；它应该提供良好的抽象，让模型和扩展在合适的时机做合适的事**。

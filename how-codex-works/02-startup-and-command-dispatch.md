# 第 2 章：启动与命令分发

> 本章导读：上一章描绘了 Codex CLI 的整体轮廓。本章聚焦“从用户按下回车到系统开始工作”这一段链路。你会看到 NPM 包装如何找到 Rust 二进制、单个 Rust 二进制如何通过 `argv[0]` 分身成多个辅助 CLI、主入口如何按子命令分发到 TUI 或 Exec，以及配置加载、状态 DB、AppServer 连接在启动顺序中的位置。

## 2.1 本章要解决的问题

Codex CLI 的启动链路不是“一个 main 函数走到底”。它要同时满足：

1. **跨平台分发**：一份 NPM 包要能在 Linux/macOS/Windows 上找到对应的本地二进制。
2. **单二进制多角色**：为了部署简单，Codex 希望用户只下载一个可执行文件，但它又要充当 `codex`、`codex-linux-sandbox`、`apply_patch`、fs helper 等多个角色。
3. **多种交互形态共享后端**：TUI、`codex exec`、桌面应用、远程控制都走同一套后端协议，但启动方式不同。
4. **状态可恢复**：本地 SQLite 状态库损坏时，TUI 要能自动备份并重建。
5. **配置可覆盖**：系统、用户、项目、CLI 参数四层配置需要在正确的时机加载。

这些需求把启动链路分成了三条主线：

- **包装线**：`codex.js` → `codex` 二进制。
- **分发线**：`arg0` 机制让同一个二进制按调用名进入不同入口。
- **业务线**：`cli/src/main.rs` 按子命令进入 `tui`、`exec`、`login` 等模块。

## 2.2 全景：从 npm install 到 Rust main

```
用户运行 codex
    │
    ▼
NPM 全局包 @openai/codex
    │  codex-cli/bin/codex.js
    │  ├─ 检测 process.platform / process.arch
    │  ├─ 定位对应平台的 @openai/codex-<platform> 包
    │  ├─ 在 vendor/<triple>/bin/codex 找到 Rust 二进制
    │  └─ spawn(binaryPath, process.argv.slice(2))
    ▼
Rust 二进制 codex-rs/cli/src/main.rs
    │
    ▼
codex_arg0::arg0_dispatch_or_else(...)
    │  ├─ 如果 argv0 == "codex-linux-sandbox" → 直接跑 Linux 沙箱
    │  ├─ 如果 argv0 == "apply_patch" / "applypatch" → 直接跑补丁应用
    │  ├─ 如果 argv1 == CODEX_FS_HELPER_ARG1 → 跑文件系统 helper
    │  ├─ 加载 ~/.codex/.env
    │  └─ 在临时目录创建 PATH 别名，暴露 codex_self_exe / linux_sandbox / execve_wrapper
    ▼
cli_main(arg0_paths, remote_control_disabled)
    │
    ▼
clap 解析 MultitoolCli
    │
    ├─ 无子命令 ────────▶ run_interactive_tui() ──▶ codex_tui::run_main()
    │                       (启动 TUI，可连接 embedded / local-daemon / remote app-server)
    │
    ├─ Subcommand::Exec ─▶ codex_exec::run_main()
    │                       (非交互式单轮执行，输出 JSONL 或人类可读文本)
    │
    ├─ Subcommand::Login ─▶ run_login_with_*()
    ├─ Subcommand::Mcp ───▶ mcp_cmd 处理
    ├─ Subcommand::Plugin ─▶ plugin_cmd 处理
    ├─ Subcommand::AppServer ─▶ app-server-daemon 生命周期
    └─ ... Doctor / Sandbox / Apply / Resume / Fork 等
```

## 2.3 NPM 包装层：跨平台二进制的分发器

`codex-cli/bin/codex.js` 的职责非常单一：**根据当前平台找到 Rust 二进制，并把参数和信号转发过去**。

它维护一个平台到 NPM 包名的映射：

```javascript
const PLATFORM_PACKAGE_BY_TARGET = {
  "x86_64-unknown-linux-musl": "@openai/codex-linux-x64",
  "aarch64-unknown-linux-musl": "@openai/codex-linux-arm64",
  "x86_64-apple-darwin": "@openai/codex-darwin-x64",
  "aarch64-apple-darwin": "@openai/codex-darwin-arm64",
  ...
};
```

关键行为：

1. **平台三元组推导**：通过 `process.platform` 和 `process.arch` 推导出 Rust target triple。
2. **二进制定位**：优先在可选依赖包的 `vendor/<triple>/bin/codex` 里找；找不到则回退到本地 `vendor/`。
3. **异步 spawn**：使用 `spawn(..., { stdio: "inherit" })` 而不是 `spawnSync`，这样 Node 进程能在 Rust 运行时继续接收 `SIGINT/SIGTERM/SIGHUP` 并转发给子进程。
4. **包管理器标记**：检测 `npm`/`pnpm`/`bun`，通过环境变量 `CODEX_MANAGED_BY_*` 告知 Rust 层。

这个文件是 Codex CLI 唯一暴露给用户的 JavaScript 层；它本身不包含业务逻辑，因此平台特定二进制可以独立升级。

## 2.4 arg0 分发：一个二进制如何扮演多个 CLI

Codex 把多个辅助工具打包进同一个 Rust 二进制，通过 **argv[0]（即 `arg0`）和 argv[1]** 判断当前应该扮演谁。这个机制封装在 `codex-rs/arg0/src/lib.rs` 中。

### 2.4.1 直接分派：永不进入主循环

| 调用名 / 参数 | 进入的模块 | 用途 |
|---------------|-----------|------|
| `argv0 == "codex-linux-sandbox"` | `codex_linux_sandbox::run_main()` | Linux 下的沙箱入口 |
| `argv0 == "apply_patch"` / `"applypatch"` | `codex_apply_patch::main()` | 独立补丁应用工具 |
| `argv1 == CODEX_FS_HELPER_ARG1` | `codex_exec_server::run_fs_helper_main()` | 文件系统 helper |
| `argv1 == CODEX_CORE_APPLY_PATCH_ARG1` | `codex_apply_patch::apply_patch()` | core 补丁应用 |
| `argv0 == "codex-execve-wrapper"` (unix) | `codex_shell_escalation::run_shell_escalation_execve_wrapper()` | shell 提权包装 |

这些入口在创建 Tokio runtime 之前就直接 `std::process::exit`，因此它们可以被外部进程以稳定的方式调用，而不用担心主 runtime 的副作用。

### 2.4.2 常规分派：创建 PATH 别名

如果以上都不匹配，`arg0_dispatch()` 会：

1. 从 `~/.codex/.env` 加载环境变量。
2. 在临时目录下创建指向当前可执行文件的 hard link / symlink：
   - `codex`（自身）
   - `codex-linux-sandbox`
   - `codex-execve-wrapper`（unix）
3. 把这个临时目录 prepend 到 `PATH` 环境变量。
4. 返回 `Arg0PathEntryGuard`，它持有 `TempDir` 和 lock file，保证整个 CLI 生命周期内这些别名可用。

`Arg0DispatchPaths` 随后被传给 `cli_main`，供后续代码使用：

```rust
pub struct Arg0DispatchPaths {
    pub codex_self_exe: Option<PathBuf>,               // 当前 Codex 可执行文件
    pub codex_linux_sandbox_exe: Option<PathBuf>,      // Linux 沙箱别名
    pub main_execve_wrapper_exe: Option<PathBuf>,      // execve 提权包装
}
```

**为什么用 arg0 而不是子命令？** 因为沙箱、补丁工具、fs helper 需要被外部进程（如 exec-server、操作系统 sandbox profile）以“看起来像独立可执行文件”的方式调用。如果用 `codex sandbox-helper -- ...`，调用方需要知道 Codex 的子命令协议；而用 `codex-linux-sandbox` 这个独立的 `argv0`，对调用方更透明，也更容易写最小权限的 sandbox profile。

## 2.5 Rust CLI 入口：子命令总控

`codex-rs/cli/src/main.rs` 中的 `main()` 非常简单：

```rust
fn main() -> anyhow::Result<()> {
    let remote_control_disabled = codex_app_server::take_remote_control_disabled_env();
    arg0_dispatch_or_else(move |arg0_paths: Arg0DispatchPaths| async move {
        cli_main(arg0_paths, remote_control_disabled).await?;
        Ok(())
    })
}
```

真正的工作在 `cli_main()` 里。它先用 `clap` 解析 `MultitoolCli`：

```rust
struct MultitoolCli {
    config_overrides: CliConfigOverrides,   // -c key=value 等
    feature_toggles: FeatureToggles,        // --enable/--disable
    remote: InteractiveRemoteOptions,       // --remote / --remote-auth-token-env
    interactive: TuiCli,                    // TUI 专用参数
    subcommand: Option<Subcommand>,         // 子命令
}
```

`Subcommand` 枚举覆盖了所有非交互式操作：

- `Exec` → 非交互执行
- `Review` → 代码审查
- `Login` / `Logout` → 认证管理
- `Mcp` → MCP 服务器管理
- `Plugin` → 插件管理
- `McpServer` → 以 MCP server 模式运行
- `AppServer` → 应用服务器生命周期
- `RemoteControl` → 远程控制守护进程
- `Apply` → 应用最近一次 diff
- `Resume` / `Fork` / `Archive` / `Delete` / `Unarchive` → 会话管理
- `Cloud` → Codex Cloud 任务
- `Doctor` / `Sandbox` / `Debug` / `Execpolicy` / `Features` → 诊断与调试

如果没有子命令，则进入交互式 TUI；否则把参数传给对应模块。

## 2.6 交互式启动路径：TUI

无子命令时，`cli_main` 调用 `run_interactive_tui(interactive, remote, remote_auth_token_env, arg0_paths)`。

### 2.6.1 TUI 启动前的准备

`run_interactive_tui` 做了几件事：

1. **prompt 规范化**：把 `\r\n` / `\r` 统一成 `\n`，避免 TUI 状态被污染。
2. **终端检测**：如果 `TERM=dumb` 且没有 TTY，直接 fatal；如果有 TTY，提示用户确认是否继续。
3. **解析远程端点**：`--remote` 可以指向一个 WebSocket 或 Unix socket；`--remote-auth-token-env` 指定从哪个环境变量读取 token。
4. **本地状态 DB 自恢复**：`start_tui()` 失败时，如果错误属于本地 SQLite 损坏，会自动备份受损文件并尝试重建；如果无法恢复，则给出诊断指引。

### 2.6.2 TUI 主入口

`codex_tui::run_main()` 位于 `codex-rs/tui/src/lib.rs`，它负责：

1. **处理危险标志**：`--dangerously-bypass-approvals-and-sandbox` 直接映射到 `SandboxMode::DangerFullAccess` + `AskForApproval::Never`。
2. **处理遗留标志**：`--search` 映射为 `web_search="live"` 配置覆盖。
3. **解析 `-c` 覆盖**：把 CLI 的 TOML 字符串覆盖解析成键值对。
4. **确定 codex_home**：`~/.codex` 的默认位置。
5. **选择 AppServerTarget**：
   - `Embedded`：在同进程内启动 `app-server`（默认）。
   - `LocalDaemon`：连接本地已经运行的 daemon socket（支持状态复用）。
   - `Remote`：连接远程 WebSocket / Unix socket（桌面应用或远程工作空间）。
6. **加载初始配置**：调用 `load_bootstrap_config_or_exit` 得到 `Config`。
7. **初始化状态 DB**：`Embedded` 模式下初始化 SQLite state DB。
8. **启动 ratatui app**：进入 `run_ratatui_app(...)`，打开备用屏幕、安装 panic hook、启动事件循环。

### 2.6.3 AppServer 连接方式

`AppServerTarget` 是 TUI 与后端通信的关键决策点：

```rust
pub(crate) enum AppServerTarget {
    Embedded,                         // 同进程 InProcessAppServerClient
    LocalDaemon { endpoint: RemoteAppServerEndpoint }, // 本地 daemon socket
    Remote { endpoint: RemoteAppServerEndpoint },      // 远程 socket
}
```

- **Embedded 模式**：TUI 直接调用 `InProcessAppServerClient::start(...)`，在同个 Tokio runtime 里启动 `app-server` 的事件循环。事件通过 mpsc channel 传递，延迟最低。
- **LocalDaemon 模式**：TUI 通过 Unix domain socket 连接一个已存在的 daemon 进程，实现多个 CLI 实例共享同一份状态。
- **Remote 模式**：通过 WebSocket 连接到远程 app-server，`uses_remote_workspace()` 返回 true，表示当前工作目录由远端决定。

这个设计让 **TUI 与后端解耦**：同一套 `AppServerClient` trait 可以对应同进程、本地进程、远程进程三种后端。

## 2.7 非交互式启动路径：Exec

`codex exec "some prompt"` 走的是 `codex-rs/exec/src/lib.rs` 中的 `run_main()`。

与 TUI 不同，Exec 模式的目标是：**给模型一个任务，收集完整输出，然后退出**。它不需要 ratatui，也不需要持久化状态 DB（除非显式要求）。

### 2.7.1 Exec 启动流程

1. **参数解构**：从 `Cli` 中取出 `command`、`strict_config`、`shared`、`json_mode`、`prompt` 等。
2. **颜色检测**：根据 `--color` 和环境检测 stdout/stderr 是否支持 ANSI。
3. **配置加载**：与 TUI 类似，通过 `load_bootstrap_config_or_exit` 加载分层配置。
4. **模型提供者选择**：处理 `--oss`、CLI 指定的模型、默认模型。
5. **启动 AppServerClient**：同样使用 `InProcessAppServerClient::start`，但通常不需要状态 DB。
6. **创建 EventProcessor**：
   - `--json` 模式 → `EventProcessorWithJsonOutput`
   - 默认 → `EventProcessorWithHumanOutput`
7. **发送 turn 请求**：把 prompt 和附件发给 app-server，等待事件流。
8. **事件消费**：处理器把 app-server 的原始事件翻译成 `CommandExecutionItem`、`FileChangeItem`、`AgentMessageItem` 等结构化事件，输出到 stdout/stderr 或 JSONL。
9. **退出**：turn 完成后按 exit code 退出。

### 2.7.2 Exec 的事件抽象

`codex-rs/exec/src/exec_events.rs` 定义了 Exec 模式下的事件类型。这些事件是 app-server 协议事件的一个**面向人类/脚本消费的视图**：

- `ThreadStartedEvent`
- `TurnStartedEvent` / `TurnCompletedEvent` / `TurnFailedEvent`
- `AgentMessageItem`（模型文本/思考）
- `CommandExecutionItem`（shell 命令执行）
- `FileChangeItem`（文件变更）
- `McpToolCallItem`（MCP 工具调用）
- `TodoItem`（模型生成的任务列表）

JSONL 模式把这些事件逐行输出，方便脚本解析；人类模式则在终端渲染进度、diff、输出。

## 2.8 其他子命令

### 2.8.1 认证：`Login` / `Logout`

`codex login` 支持多种登录方式：

- `--with-api-key`：从 stdin 读取 API key。
- `--with-access-token`：从 stdin 读取 access token。
- 默认：ChatGPT OAuth / device code 浏览器登录。

这些逻辑在 `codex-rs/cli/src/login.rs` 中实现，调用 `codex_login` crate。

### 2.8.2 外部能力管理：`Mcp` / `Plugin`

- `Mcp` 子命令管理外部 MCP servers 的添加、删除、列出、测试连接。
- `Plugin` 子命令管理 Codex 插件（connector 等）。

两者都是纯 CLI 操作，不进入 TUI 或 Exec 主循环。

### 2.8.3 应用服务器生命周期：`AppServer` / `RemoteControl`

- `AppServer` 调用 `codex_app_server_daemon::run(LifecycleCommand::Start/Stop/Restart/Version)`。
- `RemoteControl` 启动/管理支持远程控制的 daemon，供桌面应用或远程 CLI 连接。

### 2.8.4 会话与诊断

- `Resume` / `Fork` / `Archive` / `Delete` / `Unarchive`：操作 `codex-rollout` 持久化的会话记录。
- `Apply`：读取最近一次模型生成的 diff，调用 `git apply`。
- `Doctor`：诊断安装、配置、认证、运行时健康。
- `Sandbox`：在 Codex 提供的沙箱中运行命令。
- `Debug`：输出模型目录、prompt 输入、trace reduce 等内部状态。

## 2.9 配置加载在启动链中的位置

配置加载是启动链路中最关键也最容易被忽略的一环。TUI 和 Exec 都会在启动早期调用 `load_bootstrap_config_or_exit`：

```
cli_main / exec::run_main / tui::run_main
    │
    ▼
find_codex_home()
    │
    ▼
load_bootstrap_config_or_exit(
    codex_home,
    config_cwd,           // 当前工作目录，用于定位项目配置
    cli_kv_overrides,     // -c key=value
    loader_overrides,     // --profile, --ignore-user-config 等
    strict_config,        // --strict-config
    cloud_config_bundle,  // 云端/托管配置
)
    │
    ▼
ConfigBuilder / ConfigToml / ConfigLayerStack
    │
    ▼
Config（包含 model、permissions、sandbox、features 等完整配置）
```

配置来源按优先级大致如下（从高到低）：

1. CLI 参数（`-c`、`--approval-policy`、`--sandbox-mode` 等）
2. 环境变量
3. 项目级 `codex.config.toml` / `.codex/config.toml` / `AGENTS.md`
4. 用户级 `~/.codex/config.toml`（或 `--profile` 指定的 profile）
5. 系统级/托管配置
6. 内置默认值

`strict_config` 为 true 时，任何配置文件中不被当前版本识别的字段都会报错，避免旧配置在新版本里静默失效。

## 2.10 设计取舍

| 取舍 | 选择 | 替代方案 | 原因 |
|------|------|----------|------|
| NPM 包装层 | 一个轻量 JS 转发器 | 直接发布 Rust 二进制 | 利用 npm 的分发、版本、平台包机制，降低用户安装门槛 |
| 单二进制多角色 | arg0 分发 | 多个独立二进制 | 部署简单，平台包体积小，辅助工具版本永远一致 |
| TUI 与后端同进程 vs. 协议分离 | Embedded 默认走协议，但同进程 | UI 直接调 core API | 保持协议统一，天然支持 LocalDaemon/Remote |
| Exec 单独 crate | `codex-rs/exec` | 复用 TUI 入口加 `--no-tui` | Exec 的事件处理和输出语义与 TUI 差异大，独立更清晰 |
| 状态 DB 损坏自恢复 | TUI 启动时自动备份重建 | 直接报错退出 | 本地 SQLite 损坏是常见问题，自动恢复提升可用性 |
| 配置尽早加载 | TUI/Exec 启动就 load config | 按需延迟加载 | 模型、权限、沙箱等决策都依赖配置，早加载失败早退出 |

## 2.11 关键实现入口

| 概念 | 主要文件 | 说明 |
|------|----------|------|
| NPM 包装 | `codex-cli/bin/codex.js` | 平台检测、二进制定位、spawn |
| arg0 分发 | `codex-rs/arg0/src/lib.rs` | `arg0_dispatch_or_else`、`Arg0DispatchPaths` |
| CLI 主入口 | `codex-rs/cli/src/main.rs` | `main`、`cli_main`、`MultitoolCli`、`Subcommand` |
| TUI 入口 | `codex-rs/tui/src/lib.rs` | `run_main`、`run_ratatui_app` |
| TUI CLI 参数 | `codex-rs/tui/src/cli.rs` | `Cli`、`TuiSharedCliOptions` |
| Exec 入口 | `codex-rs/exec/src/lib.rs` | `run_main` |
| Exec 事件 | `codex-rs/exec/src/exec_events.rs` | 结构化事件定义 |
| AppServer 目标 | `codex-rs/tui/src/lib.rs` | `AppServerTarget` |
| 同进程 AppServer 客户端 | `codex-rs/app-server-client/src/lib.rs` | `InProcessAppServerClient::start` |
| AppServer 实现 | `codex-rs/app-server/src/in_process.rs` | `start`、`start_uninitialized` |
| AppServer 守护进程 | `codex-rs/app-server-daemon/src/lib.rs` | `run`、`bootstrap` |
| 配置加载 | `codex-rs/core/src/config/mod.rs` | `Config`、`load_bootstrap_config_or_exit` |
| 登录实现 | `codex-rs/cli/src/login.rs` | `run_login_with_*` |
| CLI 库公开 API | `codex-rs/cli/src/lib.rs` | `login`、`debug_sandbox`、`SandboxStateArgs` |

## 2.12 小结

Codex CLI 的启动链路体现了“**薄入口、厚分发、协议解耦**”的设计：

- `codex-cli/bin/codex.js` 是最薄的跨平台包装；
- `codex_arg0` 用 `argv0` 技巧让一个二进制扮演多个角色，简化部署；
- `cli/src/main.rs` 是子命令总控，把交互式、非交互式、管理类命令分发给不同模块；
- TUI 和 Exec 都通过 `AppServerClient` 协议与后端通信，默认 embedded，但天然支持 local daemon 和 remote；
- 配置在启动早期加载，并支持从 CLI 到系统默认的多层覆盖。

下一章（TUI 与 AppServer）会进入交互式路径的细节：ratatui 事件循环如何渲染流式输出，以及 `app-server` 如何维护 thread 状态并向 TUI 推送事件。

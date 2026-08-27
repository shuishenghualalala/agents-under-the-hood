# how-codex-works

本目录是对 [OpenAI Codex CLI](https://github.com/openai/codex) 仓库的架构拆解，目标是把一个真实的、规模较大的 Rust（+ 少量 TypeScript）项目转化为可读的“系统模型”，帮助新工程师快速理解 Codex CLI 是怎么跑起来的。

> 写作风格参考 [how-claude-code-works](https://www.anthropic.com/engineering/claude-code-how-claude-code-works)：先说清楚“解决什么问题”，再讲“机制是什么”，最后才给出关键代码入口。

## 阅读顺序

| 章节 | 主题 | 解决的问题 |
|------|------|-----------|
| [第 1 章：总览](./01-overview.md) | 定位、核心问题、系统边界 | Codex CLI 是什么、为谁服务、和 ChatGPT/Codex Web 有什么区别 |
| [第 2 章：启动与命令分发](./02-startup-and-command-dispatch.md) | 从 `codex.js` 到 `codex-rs/cli` | NPM 包如何分发平台二进制、子命令如何路由到 TUI/Exec/Login 等 |
| 第 3 章：TUI 与 AppServer（待写） | 交互层架构 | 终端 UI 如何与后端核心解耦、AppServer 协议怎么支撑桌面/远程模式 |
| [第 4 章：核心编排循环](./04-agent-loop.md) | Thread、Session、Turn、TurnInput | 用户消息通过 `TurnInput` 统一入口原子性 start/steer/reject，turn 内多轮采样循环，工具并行执行，Token 触顶 Compaction，历史类型拆入 `codex-history` crate |
| [第 5 章：Agent 与多 Agent 控制](./05-multi-agent.md) | AgentControl、子 Agent | 多 Agent 如何 spawn、通信、预算与并发控制 |
| [第 6 章：工具系统与扩展点](./06-tools-and-extensions.md) | ToolRouter、Extensions、MCP、Skills | 新工具/新能力如何接入而不改核心循环 |
| [第 7 章：执行沙箱与权限](./07-execution-sandbox-and-permissions.md) | exec-policy、SandboxManager、PermissionProfile | 命令判定、文件系统隔离、网络拦截三层纵深防御如何衔接审批 |
| 第 8 章：配置与状态管理（待写） | Config 分层加载、State DB、Rollout | 用户配置、项目配置、会话持久化怎么组合 |
| 第 9 章：模型提供者与网络（待写） | ModelProvider、backend-client | 如何对接 OpenAI、Ollama、LM Studio 等不同模型后端 |
| 第 10 章：最小可复现实现（待写） | 精简版 Codex | 如果要重写一个最小 Codex，哪些组件不能省 |
| [专题：时间、异步与后台机制](./11-time-and-background.md) | TimeProvider、Sleep、Watcher、Cloud Tasks | Codex CLI 有没有定时任务？它如何处理等待、刷新、后台任务 |
| [第 12 章：用户管理](./12-user-management.md) | 身份认证、工作空间、数据隔离 | 用户如何登录、本地数据存放在哪、多项目/多用户如何隔离 |
| [专题：浏览器自动化插件](./13-browser-plugin.md) | Codex Browser、CDP、Playwright | Agent 如何发现/连接浏览器，如何安全地操控页面、登录、上传 |
| [第 14 章：安全审批与 Guardian](./14-security-approval-and-guardian.md) | AskForApproval、ApprovalsReviewer、Guardian（两层） | 受限动作何时审批、由谁审批，以及“替我审批”的 Guardian 如何工作：V2 异步风险评分（gpt-5.6-luna）秒批低风险 + 同步子代理审查兜底，fail-closed、熔断 |

## 关键仓库信息

- **根目录**: `/Users/ahuamao/Documents/Codes/codex`
- **主要语言**: Rust（工作区 `codex-rs/`）+ TypeScript（NPM 分发包装 `codex-cli/`）
- **构建系统**: Bazel（主）+ Cargo（开发/测试）+ pnpm（前端包装）
- **CLI 二进制入口**: `codex-rs/cli/src/main.rs`
- **核心库入口**: `codex-rs/core/src/lib.rs`
- **TUI 库入口**: `codex-rs/tui/src/lib.rs`
- **执行服务器入口**: `codex-rs/exec-server/src/lib.rs`

## 约定

- 代码引用尽量使用**文件级路径**；只有对“容易误解或关键的实现细节”才会给出行号。
- “从代码结构推断”表示该结论没有直接运行证据，是基于源码结构的合理推断。
- 章节标题和正文使用中文。

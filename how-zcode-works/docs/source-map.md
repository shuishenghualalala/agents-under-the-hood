# 源码索引


> **文档同步跟踪**
> - 最后同步代码：`ZCode` commit `872ad960de`（2026-09-21）
> - 同步方式：基于该文档撰写时的源码路径梳理

这份索引列出当前拆解用到的主要源码入口。约定：`CORE` = `apps/zcode-cli/packages/core/src`，其余以仓库根为基准。

## 仓库骨架（01）

- `package.json`、`pnpm-workspace.yaml`（外层 workspace 与构建入口）
- `apps/zcode-cli/pnpm-workspace.yaml`（内层子 workspace）
- `AGENTS.md`、`architecture-policy.yaml`（工程约束与架构检查）
- `README.md`（三种入口与开发命令）
- `packages/rpc/src/`（ChannelServer/ChannelClient、VQL 序列化、proxy-channel）
- `packages/services/src/`（服务集合总目录）
- `packages/shared/src/`（跨进程类型与协议定义）

## Agent 运行时（02）

- `CORE/runtime/agent-runtime.ts`（类 + 声明合并接口 + 装配收口）
- `CORE/runtime/methods/index.ts`（mixin 装配表，~190 个赋值）
- `CORE/runtime/internal.ts`（mixins 的内部视图类型）
- `CORE/runtime/deps.ts`、`CORE/runtime/types.ts`（依赖注入契约）
- `CORE/runtime/methods/prompt-admission.ts`（消息准入三分支）
- `CORE/runtime/methods/turn.ts`（turn 启动/拆除）、`turn-loop.ts`（模型步循环）
- `CORE/runtime/methods/turn-model-step.ts`（单个模型步）
- `CORE/runtime/methods/turn-tools.ts`（工具批执行与结果回喂）
- `CORE/runtime/methods/turn-stop.ts`、`turn-guide-drain.ts`
- `CORE/runtime/methods/steering.ts`（steer 排队/消费/队列管理）
- `CORE/runtime/command-queue.ts`、`runtime/methods/runtime-command-queue.ts`（运行时命令队列）
- `CORE/runtime/methods/model.ts`（流式事件扇出）、`model-streaming-event-queue.ts`（写队列背压）
- `CORE/runtime/methods/streaming-recovery.ts`（流恢复）
- `CORE/runtime/methods/events.ts`（事件追加与 sink 扇出）、`resume.ts`（恢复）
- `CORE/context/builder.ts`（系统段装配）
- `CORE/runtime/helpers/provider-request-messages.ts`、`media-budget.ts`
- `apps/zcode-cli/packages/bootstrap/src/app/create-app.ts`（组装点）
- `apps/zcode-cli/packages/adapters/src/storage/session-store/`（SQLite 会话库）

## 工具与权限（03）

- `CORE/tool/types.ts`（工具契约与元数据）、`tool/registry.ts`（注册表）
- `CORE/tool/handlers/index.ts`（内置工具组装与功能门控）
- `CORE/tool/executor/call-runner.ts`（单次调用管道）
- `CORE/tool/executor/permission-flow.ts`（权限裁决流 + 钩子赛跑）
- `CORE/tool/scheduler.ts`、`tool/executor/batch-runner.ts`（并行调度）
- `CORE/runtime/methods/streaming-tool-coordinator.ts`（流中执行）
- `CORE/permission/service.ts`（模式决策序）、`permission/plan-mode-policy.ts`、`permission/rule-matching.ts`
- `CORE/tool/handlers/bash-command-parser.ts`、`bash-semantics.ts`、`bash-readonly-policy-argv.ts`、`bash-command-permission-policy.ts`
- `CORE/tool/executor/result-serialization.ts`（结果预算）
- `CORE/mcp/index.ts`、`mcp/name.ts`（MCP 工具注册）
- `CORE/hooks/runner.ts`、`hooks/output.ts`（钩子）
- `CORE/runtime/permission-full-access.ts`、`permission-grant-recovery.ts`

## 多智能体（04）

- `CORE/subagent/profile.ts`（profile 与内置 general-purpose/Explore）
- `apps/zcode-cli/packages/bootstrap/src/subagents.ts`（用户/项目/插件 profile 根）
- `CORE/runtime/methods/subagent.ts`（子运行时构造、工具借用、派生端口）
- `CORE/subagent/runner.ts`（launch/run/start、watchdog、auto-background）
- `CORE/tool/handlers/agent.ts`（Agent/Task 工具）
- `CORE/subagent/tool-event-mirror.ts`（工具事件镜像）
- `CORE/runtime/methods/message-steering.ts`（SendMessage 路由）、`coordinator-response.ts`、`subagent-messages.ts`
- `CORE/subagent/tool-policy.ts`、`CORE/subagent/persistent-memory.ts`
- `CORE/runtime/methods/background-notifications.ts`（后台通知账本与批量合成）
- `CORE/workflow/`、`CORE/dynamic-workflow/`、`apps/zcode-cli/packages/dynamic-workflow-runtime/`（工作流引擎）

## 上下文压缩（05）

- `CORE/compact/policy.ts`（阈值算术与熔断）、`compact/microcompact.ts`、`compact/manual.ts`、`compact/prompt.ts`
- `CORE/runtime/methods/compact.ts`、`compact-active.ts`、`compact-persistence.ts`、`microcompact.ts`
- `CORE/runtime/methods/turn-loop-state.ts`（rapid-refill 熔断）

## 记忆（06）

- `CORE/memory/project-root.ts`、`memory/index-content.ts`、`memory/extraction.ts`
- `CORE/memory/memory-agent-loop.ts`（受限循环与工具策略）
- `CORE/memory/recall/manifest.ts`、`memory/memory-file-path.ts`
- `CORE/runtime/helpers/project-memory-extraction.ts`、`runtime/helpers/project-memory.ts`
- `CORE/context/sections/memory.ts`（system Memory 段）
- `packages/bootstrap/src/zcode-protocol/server-operations.ts`（桌面默认关闭注入点）

## 桌面端（07）

- `packages/desktop/src/main/index.ts`（Main 职责）
- `packages/desktop/src/main/desktopHostProcess.ts`（Host fork 与 port 转交）
- `packages/desktop/src/host/index.ts`（Host 服务编排，头注释即拓扑声明）
- `packages/desktop/src/main/taskRealtimeBus.ts`（lease/流镜像/路由）
- `packages/desktop/src/scheduler/index.ts`（定时与闲时任务调度）
- `packages/services/src/zcode-agent/zcodeAgentProcessManager.ts`（Agent 子进程解析链）
- `packages/services/src/zcode-agent/zcodeStdioTransport.ts`（JSON-line stdio）
- `packages/ui/src/v4/agentConversationTransport.ts`（topic 帧订阅）
- `packages/desktop/src/main/desktopRuntimeEnv.ts`（随包 runtime 解析）
- `packages/shared/src/zcode-task-types-core.ts`（desktop-continuous / web-remote-replayable）

## Server 与远程（08）

- `packages/server/src/entry-http.ts`、`entry-stdio.ts`、`http.ts`、`stdio.ts`、`stdioServices.ts`
- `packages/server/src/remote/`（ssh/wsl/docker-backend、deploy、remoteAsset*、remoteDeployLock）
- `scripts/zcode-distribution/runner.mjs`（发行包装器分发）、`scripts/build-zcode.mjs`
- `packages/provider/src/config-overlay.ts`、`config/`、`effective-model-selection.ts`
- `packages/provider-node/src/`（内置配置物化/下载/同步、个人配置仓库）
- `packages/server/src/bundledZCodeBuiltinProviderConfig.ts`
- `apps/zcode-cli/packages/adapters/src/model/`（model-execution、official-coding-plan-gateway、协议 compat、runner-* 可靠性群）
- `packages/services/src/paths.ts`（数据目录解析）
- `packages/zcode-server-cli/src/`（远端 supervisor）

## 插件、技能与 MCP（09）

- `apps/zcode-cli/packages/adapters/src/plugins/index.ts`、`plugin-components.ts`、`marketplace.ts`、`mcp.ts`
- `apps/zcode-cli/packages/bootstrap/src/plugins.ts`（装/卸/启停/商店总览）、`app/official-plugin-definitions.ts`
- `packages/services/src/plugins/plugins.ts`、`plugin-sync/pluginSyncService.ts`
- `packages/ui/src/settings/PluginStoreListView.tsx`（商店 UI）
- `apps/zcode-cli/packages/adapters/src/skills/index.ts`、`scan.ts`、`roots.ts`
- `CORE/tool/handlers/skill.ts`、`CORE/context/sections/skills.ts`
- `apps/zcode-cli/packages/bootstrap/src/custom-commands.ts`、`custom-command-shell-expansion.ts`、`slash-command-surface.ts`
- `apps/zcode-cli/packages/contracts/src/interfaces/mcp.port.ts`
- `apps/zcode-cli/packages/adapters/src/mcp/`（传输、连接池、oauth-interactive）
- `packages/services/src/official-mcp/officialMcpCredentials.ts`、`mcp-sync/mcpSyncService.ts`
- `apps/zcode-cli/packages/bootstrap/src/workspace-hook-trust-cli.ts`

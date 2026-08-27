# 源码索引


> **文档同步跟踪**
> - 最后同步代码：`kimi-code` commit `c3a2ef0ce`（2026-08-27）
> - 同步方式：基于该文档撰写时的源码路径与提交记录梳理

这份索引列出当前拆解用到的主要源码入口。

## CLI / TUI

- `apps/kimi-code/src/main.ts`
- `apps/kimi-code/src/cli/commands.ts`
- `apps/kimi-code/src/cli/run-shell.ts`
- `apps/kimi-code/src/cli/run-prompt.ts`
- `apps/kimi-code/src/tui/kimi-tui.ts`
- `apps/kimi-code/src/tui/commands/registry.ts`
- `apps/kimi-code/src/tui/commands/config.ts`
- `apps/kimi-code/src/tui/reverse-rpc/approval/adapter.ts`
- `apps/kimi-code/src/tui/controllers/streaming-ui.ts`
- `apps/kimi-code/src/tui/components/dialogs/approval-panel.ts`

## SDK

- `packages/node-sdk/src/kimi-harness.ts`
- `packages/node-sdk/src/session.ts`
- `packages/node-sdk/src/sdk-rpc-client.ts`

## Agent Core

- `packages/agent-core/src/rpc/core-impl.ts`
- `packages/agent-core/src/session/index.ts`
- `packages/agent-core/src/session/subagent-host.ts`
- `packages/agent-core/src/session/subagent-batch.ts`
- `packages/agent-core/src/agent/index.ts`
- `packages/agent-core/src/agent/swarm/index.ts`
- `packages/agent-core/src/agent/background/index.ts`
- `packages/agent-core/src/agent/background/agent-task.ts`
- `packages/agent-core/src/agent/context/notification-xml.ts`
- `packages/agent-core/src/agent/turn/index.ts`
- `packages/agent-core/src/loop/README.md`
- `packages/agent-core/src/loop/run-turn.ts`
- `packages/agent-core/src/loop/turn-step.ts`
- `packages/agent-core/src/loop/tool-call.ts`
- `packages/agent-core/src/loop/tool-access.ts`
- `packages/agent-core/src/loop/tool-scheduler.ts`
- `packages/agent-core/src/agent/tool/index.ts`

## Tool System

- `packages/agent-core/src/tools/builtin/index.ts`
- `packages/agent-core/src/tools/args-validator.ts`
- `packages/agent-core/src/tools/display/index.ts`
- `packages/agent-core/src/tools/display/schemas.ts`
- `packages/agent-core/src/tools/store.ts`
- `packages/agent-core/src/tools/builtin/file/read.ts`
- `packages/agent-core/src/tools/builtin/file/write.ts`
- `packages/agent-core/src/tools/builtin/file/edit.ts`
- `packages/agent-core/src/tools/builtin/file/grep.ts`
- `packages/agent-core/src/tools/builtin/file/glob.ts`
- `packages/agent-core/src/tools/builtin/file/read-media.ts`
- `packages/agent-core/src/tools/builtin/shell/bash.ts`
- `packages/agent-core/src/tools/builtin/web/web-search.ts`
- `packages/agent-core/src/tools/builtin/web/fetch-url.ts`
- `packages/agent-core/src/tools/builtin/state/todo-list.ts`
- `packages/agent-core/src/tools/background/task-list.ts`
- `packages/agent-core/src/tools/background/task-output.ts`
- `packages/agent-core/src/tools/background/task-stop.ts`
- `packages/agent-core/src/tools/cron/cron-create.ts`
- `packages/agent-core/src/tools/cron/cron-list.ts`
- `packages/agent-core/src/tools/cron/cron-delete.ts`
- `packages/agent-core/src/tools/builtin/goal/create-goal.ts`
- `packages/agent-core/src/tools/builtin/goal/get-goal.ts`
- `packages/agent-core/src/tools/builtin/goal/set-goal-budget.ts`
- `packages/agent-core/src/tools/builtin/goal/update-goal.ts`
- `packages/agent-core/src/tools/builtin/collaboration/ask-user.ts`
- `packages/agent-core/src/tools/builtin/collaboration/skill-tool.ts`
- `packages/agent-core/src/tools/policies/path-access.ts`
- `packages/agent-core/src/tools/policies/sensitive.ts`
- `packages/agent-core/src/agent/turn/tool-dedup.ts`
- `packages/agent-core/src/agent/turn/tool-result-budget.ts`
- `packages/agent-core/src/agent/permission/index.ts`
- `packages/agent-core/src/agent/permission/policies/index.ts`
- `packages/agent-core/src/mcp/connection-manager.ts`
- `packages/agent-core/src/mcp/tool-naming.ts`
- `packages/agent-core/src/mcp/output.ts`

## Subagent / Delegation

- `docs/zh/customization/agents.md`
- `docs/zh/reference/tools.md`
- `packages/agent-core/src/profile/default/agent.yaml`
- `packages/agent-core/src/profile/default/coder.yaml`
- `packages/agent-core/src/profile/default/explore.yaml`
- `packages/agent-core/src/profile/default/plan.yaml`
- `packages/agent-core/src/tools/builtin/collaboration/agent.ts`
- `packages/agent-core/src/tools/builtin/collaboration/agent.md`
- `packages/agent-core/src/tools/builtin/collaboration/agent-swarm.ts`
- `packages/agent-core/src/tools/builtin/collaboration/agent-swarm.md`
- `packages/agent-core/src/agent/permission/policies/default-tool-approve.ts`
- `packages/agent-core/src/agent/permission/policies/agent-swarm-exclusive-deny.ts`
- `packages/agent-core/src/agent/permission/policies/swarm-mode-agent-swarm-approve.ts`
- `packages/protocol/src/events.ts`
- `apps/kimi-code/src/tui/controllers/subagent-event-handler.ts`
- `apps/kimi-code/src/tui/components/messages/tool-call.ts`
- `apps/kimi-code/src/tui/components/messages/agent-group.ts`
- `apps/kimi-code/src/tui/components/messages/agent-swarm-progress.ts`

## Plan Mode

- `packages/agent-core/src/agent/plan/index.ts`
- `packages/agent-core/src/agent/injection/plan-mode.ts`
- `packages/agent-core/src/agent/injection/manager.ts`
- `packages/agent-core/src/tools/builtin/planning/enter-plan-mode.ts`
- `packages/agent-core/src/tools/builtin/planning/enter-plan-mode.md`
- `packages/agent-core/src/tools/builtin/planning/exit-plan-mode.ts`
- `packages/agent-core/src/tools/builtin/planning/exit-plan-mode.md`
- `packages/agent-core/src/agent/permission/policies/index.ts`
- `packages/agent-core/src/agent/permission/policies/plan-mode-guard-deny.ts`
- `packages/agent-core/src/agent/permission/policies/plan-mode-tool-approve.ts`
- `packages/agent-core/src/agent/permission/policies/exit-plan-mode-review-ask.ts`
- `packages/agent-core/test/harness/plan-mode-session.test.ts`

## Server / Web / Desktop

- `packages/server/src/start.ts`
- `packages/server/src/routes/registerApiV1Routes.ts`
- `packages/server/src/routes/prompts.ts`
- `packages/server/src/services/serviceCollection.ts`
- `packages/agent-core/src/services/coreProcess/coreProcessService.ts`
- `packages/agent-core/src/services/prompt/prompt.ts`
- `packages/agent-core/src/services/prompt/promptService.ts`
- `packages/protocol/src/index.ts`
- `apps/kimi-web/src/api/daemon/client.ts`
- `apps/kimi-web/src/api/daemon/ws.ts`
- `apps/kimi-desktop/src/main/index.ts`

## agent-core-v2 / kap-server（v2 路径）

- `packages/agent-core-v2/src/index.ts` — v2 引擎入口与导出。
- `packages/agent-core-v2/src/app/bootstrap/bootstrapService.ts` — 启动与 App Scope 创建。
- `packages/agent-core-v2/src/app/scopes.ts` — DI scope 枚举与拓扑。
- `packages/agent-core-v2/src/_base/di/` — DI 内核（service / fiber / collection / cascade）。
- `packages/agent-core-v2/src/features/` — 内建 Feature（plan / goal / swarm / tower / skill / externalHooks）。
- `packages/agent-core-v2/src/workspace/workspaceInstance/` — Workspace 实例管理。
- `packages/agent-core-v2/src/workspace/sessionLifecycle/sessionLifecycleService.ts` — Session 生命周期。
- `packages/agent-core-v2/src/session/agentLifecycle/agentLifecycleService.ts` — Agent 生命周期。
- `packages/agent-core-v2/src/wire/` — wire 协议与 durable record。
- `packages/kap-server/src/start.ts` — kap-server 启动。
- `packages/kap-server/src/routes/` — REST 路由（v1 / v2）。
- `packages/kap-server/src/transport/ws/v1/registerWsV1.ts` — WebSocket 注册。
- `packages/kap-server/src/services/transcript/transcriptService.ts` — transcript 服务。
- `packages/klient/src/core/klient.ts` — klient facade。
- `packages/transcript/src/contract/` — transcript 协议类型。
- `packages/minidb/src/` — 嵌入式 JSON 存储与搜索索引。
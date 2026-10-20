# 源码索引

> 全仓库关键实现入口按主题归类，供与各章正文对照。路径均相对仓库根（`minimax-code`）；标注 ⭐ 的是「如果只读十个文件」清单。

## 启动与表面（第 2 章）

| 关注点 | 路径 |
|---|---|
| ⭐ 进程入口 | `packages/tui/src/index.ts` |
| CLI 分发表 | `packages/tui/src/cli/program.ts`、`main.ts`、`contract.ts` |
| TUI 启动器（并行启动/延迟代理） | `packages/tui/src/tui/launcher.ts`、`packages/tui/src/runtime/deferred.ts` |
| 运行时装配与关停 | `packages/tui/src/runtime/lifecycle.ts`、`embedded-host.ts` |
| 无头管线 | `packages/tui/src/headless/runner.ts`、`supervisor.ts`、`exit-policy.ts` |
| ACP 适配 | `packages/tui/src/acp/agent.ts`、`stdio.ts` |
| 共享运行协调器 | `packages/tui/src/application/run-coordinator.ts` |

## 核心循环（第 3 章）

| 关注点 | 路径 |
|---|---|
| ⭐ agent 循环本体（基座） | `third_party/pi-mono/packages/agent/src/agent-loop.ts`（runLoop :155）、`agent.ts`（:174） |
| ⭐ 发送用例与发送门 | `packages/local-runtime-v2/src/application/conversation/conversation-application.ts`、`direct-send-delivery.ts` |
| Turn 提交/准入/执行 | `packages/local-runtime-v2/src/service/turn-system/turn-submission.service.ts`、`execution/turn-execution.service.ts` |
| AgentHost 编排 | `…/turn-system/agent-host/local-agent-host.ts`、`execution/executor.ts` |
| 安全包装与重生成 | `…/turn-system/agent-host/runner/local-agent-turn-runner.ts` |
| Turn 装配器（第一方包装层） | `packages/agent-core/src/pi-turn-runner/pi-turn-runner.ts`、`agent.ts` |
| 事件桥 | `packages/agent-core/src/event-bridge/bridge.ts` |
| 会话流服务 | `…/session-system/stream/session-stream-service.ts` |
| 队列派发器与转向 | `…/turn-system/queue.dispatcher.ts`、`execution/steering/steering-session.service.ts` |
| 服务组装根（接线全景） | `packages/local-runtime-v2/src/services.ts` |

## Subagent 与 Plan（第 4、5 章）

| 关注点 | 路径 |
|---|---|
| task 工具定义与实现 | `packages/agent-core/src/tools/builtin-defs.ts:230`、`packages/agent-tools/src/desktop/local-task.ts`、`local-task-control.ts` |
| 子 Agent 类型协议 | `packages/protocol/src/runtime.ts:321` |
| Goal 协调与验证 | `packages/local-runtime-v2/src/application/agent/goal-subagent-coordinator.ts`、`goal-subagent-verifier.ts` |
| 子任务模型选择链 | `packages/local-runtime/src/api/child-model-config.ts` |
| 交互模式状态机 | `packages/local-runtime-v2/src/service/session-system/sessions/interaction-mode-capability.ts` |
| Plan 服务（准入/守卫/栅栏） | `packages/local-runtime-v2/src/service/plan/`（`contracts.ts`、`tool-guard.ts`、`initialize.ts`） |
| TUI 计划面板 | `packages/tui/src/tui/features/interaction/plan-entry-panel.ts`、`plan-review-panel.ts` |

## 工具系统（第 6 章）

| 关注点 | 路径 |
|---|---|
| ⭐ 全部本地工具定义 | `packages/agent-tools/src/desktop/builtin-defs.ts` |
| 工具注册表与实现 | `packages/agent-tools/src/desktop/index.ts:118`、`local-pi-tools.ts` |
| 文件工具引擎（基座） | `third_party/pi-mono/packages/coding-agent/src/core/tools/` |
| 工具契约类型 | `packages/agent-core/src/tools/types.ts` |
| 输出预算 | `packages/agent-tools/src/desktop/output-limit.ts` |
| 托管媒体工具 | `packages/agent-tools/src/cloud/matrix-tools/` |
| 浏览器核心 | `packages/browser-core/src/`、`packages/tui/src/runtime/browser/` |

## 安全与执行环境（第 7、8 章）

| 关注点 | 路径 |
|---|---|
| ⭐ 权限模式与检查器 | `packages/agent-modules/permission/src/`（`types.ts`、`ask-policy.ts`、`tools/`） |
| 权限门面与云分类器 | `packages/local-runtime/src/permissions/facade.ts`、`service.ts` |
| 规则存储（三作用域） | `packages/local-runtime/src/permissions/rules.ts:325-355` |
| V2 权限闸 | `packages/local-runtime-v2/src/…/runner/policy/local-turn-permission-gate.ts` |
| ⭐ 沙箱本体（fork） | `third_party/sandbox-runtime/src/sandbox/sandbox-manager.ts:1553`、`macos-sandbox-utils.ts:1234` |
| 沙箱服务与后端选择 | `packages/local-runtime-v2/src/service/sandbox/`（`initialize.ts:35-40`） |
| 沙箱默认值 | `packages/config/src/sandbox-settings.ts:14-27` |
| bash 环境擦洗 | `packages/agent-core/src/bash-subprocess-env.ts:53-100` |
| 可恢复删除 | `packages/agent-tools/src/desktop/host-trash-executor.ts` |

## 持久化与上下文（第 9、10 章）

| 关注点 | 路径 |
|---|---|
| ⭐ 数据库与表结构 | `packages/local-runtime-v2/src/infra/db/client.ts:48-49`、`schema/` |
| 会话目录与追加式正史 | `…/session-system/messages/history/session-history-paths.ts`、`canonical-history-provider.ts` |
| Turn 仓储（1195 行） | `…/persistence/turn.repository.ts` |
| ⭐ 系统提示装配 | `…/agent-host/preparation/config/local-agent-config-builder.ts` |
| 静态指引（AGENTS.md 32KB） | `…/agent-host/preparation/static-prompt-reader.ts`、`…/persistence/global-instructions.ts` |
| 自动压缩与工具结果外置 | `…/agent-host/compaction/context-compaction.ts:79`、`…/compaction/automatic-context-compactor.ts`、`algorithm/tool-result-archiver.ts` |

## 扩展系统（第 11 章）

| 关注点 | 路径 |
|---|---|
| ⭐ 插件系统 | `packages/local-runtime-v2/src/service/plugin-system/`（`initialize.ts`、`plugin-system.ts`） |
| 清单读取（四方言） | `…/plugin-system/plugin/package/package-readers.ts`、`agent-plugin-reader.ts` |
| 快照构建 | `…/plugin-system/plugin/runtime/snapshot-builder.ts` |
| Turn 能力租约与空闲发布 | `…/turn-system/agent-host/assembly/turn-capability-lifecycle.ts` |
| ⭐ 技能根目录与目录渲染 | `packages/local-runtime/src/skills/roots.ts`、`catalog.ts` |
| MCP 连接池与命名注册 | `packages/agent-modules/mcp/src/runtime/name-registry.ts`、`connection-pool.ts` |
| MCP 披露（tool_search/mcp_invoke） | `packages/agent-tools/src/mcp-disclosure/` |
| 插件钩子 | `packages/agent-modules/plugin-hooks/src/` |

## 配置、账号与模型（第 12 章）

| 关注点 | 路径 |
|---|---|
| ⭐ 配置模式与模型目录 | `packages/config/src/config.ts`（MINIMAX_MODELS :1528-1565） |
| 数据目录迁移 | `packages/config/src/data-dir.ts` |
| OAuth 核心 | `packages/oauth-core/src/auth-core.ts`、`oauth-client.ts:79-187`、`endpoint-config.ts:24-56` |
| 模型解析器 | `packages/local-runtime/src/runtime/model-resolver.ts:172-334` |
| 租约协议与 broker | `packages/oauth-lease-protocol/src/`、`packages/mcode-tools-host/src/lease-broker.ts` |
| 遥测 | `packages/tui/src/analytics/business-telemetry.ts`、`packages/config/src/telemetry-policy.ts` |

## TUI（第 13 章）

| 关注点 | 路径 |
|---|---|
| ⭐ 引擎核心 | `packages/tui/src/tui/engine/tui.ts:22-80,323` |
| 状态环与投影 | `packages/tui/src/tui/state/`、`controller/projection/` |
| 布局区域 | `packages/tui/src/tui/shell/chat-layout.ts:17-29` |
| 斜杠命令目录 | `packages/tui/src/tui/commands/catalog.ts` |

## 仓库级参考

| 关注点 | 路径 |
|---|---|
| 官方架构边界 | `docs/architecture.md` |
| 公开投影清单 | `release/public-source.json`、`release/extraction.json` |
| pi-mono fork 台账 | `third_party/pi-mono/MINIMAX_CHANGES.md` |
| 沙箱 fork 出处 | `third_party/sandbox-runtime/upstream.json` |
| 版本与证据基线 | `docs/open-source-status.md` |
| 仓库贡献与同步规则 | `AGENTS.md`、`CONTRIBUTING.md`、`docs/source-sync.md` |

## 建议读码顺序

十分钟的骨架之旅，按依赖深度从外到内：

```text
packages/tui/src/index.ts                              # 进程入口：四段式启动的前两段
packages/tui/src/cli/program.ts                        # 子命令地图
packages/tui/src/runtime/lifecycle.ts                  # 装配线全顺序
packages/local-runtime-v2/src/local/cli-service.ts     # 进程内门面
packages/local-runtime-v2/src/services.ts              # 服务图接线全景（读前 300 行即可）
…/application/conversation/conversation-application.ts # 发送用例与发送门
…/service/turn-system/initialize.ts                   # Turn 图初始化（谁拥有什么）
packages/agent-core/src/pi-turn-runner/pi-turn-runner.ts # 第一方如何包装循环
third_party/pi-mono/packages/agent/src/agent-loop.ts   # 循环本体（runLoop）
packages/agent-tools/src/desktop/builtin-defs.ts       # 模型看到的工具面
```

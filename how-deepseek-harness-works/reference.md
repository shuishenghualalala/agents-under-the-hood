# 源码地图（reference）

> 按主题组织的关键实现入口索引，供查证各章论断。路径相对仓库根。行号仅在论断微妙处给出（以 `0.1.0-rc.5` 为准，可能随版本漂移）。各章末尾也有本章专属的入口表。

## 框架层（vendor/）

| 文件 | 职责 |
|---|---|
| `vendor/cordis/src/context.ts:74` | Context proxy、extend/isolate/intercept |
| `vendor/cordis/src/fiber.ts:147` | Fiber 状态机；`:418` effect/dispose；`:611` epoch 重载 |
| `vendor/cordis/src/events.ts:234` | 五种分发模式；waterfall next 语义；`:329` internal 事件总线 |
| `vendor/cordis/src/reflect.ts:135` | 服务 provide/get proxy handler |
| `vendor/cordis/src/registry.ts:92` | 插件形态解析、Runtime 记录 |
| `vendor/loader/src/config/entry.ts:142` | Entry 事务化 update；`:104` disabled 求值 |
| `vendor/loader/src/config/isolate.ts` | isolate realm（symbol 化服务 key） |
| `vendor/include/src/index.ts:58` | `applyEntryPatches`：patch 语义唯一实现 |
| `vendor/hmr/src/index.ts:297` | 模块/配置监视、registerConfig |

## 组装与启动

| 文件 | 职责 |
|---|---|
| `apps/cli/src/args.ts`、`bin.ts` | 参数解析、mode 分发 |
| `apps/cli/src/profile-boot.ts:122` | 层叠顺序、watchUserPatches 热更 |
| `apps/cli/src/dump-config.ts` | --dump-config（不启动树的组合渲染） |
| `apps/cli/src/process-shutdown.ts` | 5s 优雅退出 |
| `packages/boot/app-boot/src/index.ts:757` | boot() 主序列；`:379` renderConfigDump |
| `packages/boot/app-boot/src/profile.ts:114` | profile 模板；`:344` bundle 双锚点解析；`:413` composeEntries |
| `packages/bundle/{base,web-app,headless}/cordis.patch.yml` | 三个随发行组合包 |
| `packages/util/home-paths/src/index.ts:87` | $DSH_HOME 解析 |

## 核心循环

| 文件 | 职责 |
|---|---|
| `packages/core/agent-loop/src/agent.ts:64` | ReactLoopAgent 全循环；`:255` turn/start 先于 claim；`:347` chunk 落日志；`:458` request/header 记录 |
| `packages/core/agent-loop/src/tool-calls.ts:59` | 工具调度器；`:146` 模型序提交；`:249` abort 合成结果 |
| `packages/core/agent-loop/src/invariant.ts:21` | "模型可见⟺已记录"断言 |
| `packages/core/agent-loop/src/index.ts:350` | setFactory 注册；创建事务 |
| `packages/core/agent-loop/src/runtime-context.ts:64` | 运行时上下文投影 |
| `packages/core/agent/src/inbox.ts:158` | Inbox：持久先于投影；`:71` claim |
| `packages/core/agent/src/runtime-types.ts:244` | agent/request、agent/request-error 声明 |
| `packages/core/agent/src/index.ts:256` | AgentRegistry、withInitiator |
| `packages/core/agent/src/dispatch.ts:107` | 融合派发器 |
| `packages/core/scope/src/index.ts:137` | createScope/scopeTarget/bindScopeParent |

## 会话日志与持久化

| 文件 | 职责 |
|---|---|
| `packages/core/session/src/types.ts:56` | SESSION_FORMAT_VERSION；`:236` SessionEventMap |
| `packages/core/session/src/index.ts:598` | append 校验/发布；`:726` deriveMessages 缓存；`:1130` fork 的 OPEN_TURN 拒绝 |
| `packages/core/session/src/surface.ts:83` | deriveEventMessage；`:239` foldSurface 校验 |
| `packages/core/session/src/chunk-rows.ts` | chunk 打包行（约 56× 压缩） |
| `packages/session/session-persistence/src/index.ts:84` | SessionPersistence seam |
| `packages/session/session-persistence-jsonl/src/{index,win32,zstd}.ts` | JSONL 后端：link/unlink 发布、koffi Win32、zstd |
| `packages/session/session-persistence-sqlite/src/schema.ts:17` | SCHEMA_VERSION=15 |
| `packages/session/session-checkpoint-policy/src/index.ts:32` | 请求前 flush |
| `packages/session/session-projection/src/index.ts:171` | 投影注册表（全量值） |
| `packages/compaction/compaction-basic/src/index.ts:147` | pressure/overflow 触发 |
| `packages/compaction/compaction-basic/src/region.ts:463` | replace op 摘要写入 |
| `packages/llm/token-meter/src/index.ts:188` | 日志回放式计量 |

## LLM 层

| 文件 | 职责 |
|---|---|
| `packages/llm/llm/src/types.ts:291` | StreamChunk 封闭联合 |
| `packages/llm/llm/src/index.ts:180` | LlmAdapter 抽象类；`:284` LlmRuntime；`:779` prepareCall；`:843` 失败归一化；`:923` llm/stream waterfall |
| `packages/llm/llm/src/assembler.ts:10` | BlockAssembler（assertNever） |
| `packages/llm/llm/src/retry-policy.ts:14` | 重试策略默认值 |
| `packages/llm/llm-deepseek/src/{adapter,serialize,translate}.ts` | DeepSeek 适配器；serialize.ts:86 空内容发 `""` 规则 |
| `packages/llm/llm-pi-ai/src/{adapter,stream,replay}.ts` | pi-ai 适配器；adapter.ts:97 maxRetries:0 |
| `packages/llm/llm-retry/src/index.ts:182` | 从日志推导重试次数 |
| `packages/test-support/llm-replay` | 快照回放（llm/stream 短路） |

## 工具与执行世界

| 文件 | 职责 |
|---|---|
| `packages/core/tools/src/index.ts:221` | ToolDefinition；`:796` 分阶段调度；`:1463` 分阶段流水线；`:1689` ask→approval 桥 |
| `packages/core/tools/src/presentation.ts` | UI render intent |
| `packages/fs/fs/src/index.ts:86` | FileSystem SD 与三个坐标方法 |
| `packages/fs/fs-observation-policy/src/index.ts:65` | 先读后写 WeakMap |
| `packages/fs/fs-sandbox/src/index.ts:126` | canonicalize-then-contain |
| `packages/shell/tool-bash/src/index.ts:330` | bash 工具主流程 |
| `packages/shell/bash-local/src/index.ts:146` | resolve(request)→spec 模板 |
| `packages/shell/bash-sandbox/src/index.ts:177` | confine 调用点 |
| `packages/subprocess/subprocess/src/index.ts:102` | 执行世界约定（SD 文档） |
| `packages/subprocess/subprocess-local/src/spawn.ts:276` | 进程组 spawn；Windows taskkill |
| `packages/sandbox/sandbox/src/index.ts:158` | confine SD；escalation.ts:157 提权 |
| `packages/sandbox/sandbox-local/src/index.ts:205` | denial 方言；`:316` argv 包装 |
| `packages/e2b/{e2b,subprocess-e2b,fs-e2b}/src/` | E2B 远程世界 |
| `native/landlock-run/` | Landlock 启动器（约 300 行 C11） |

## 交互与安全

| 文件 | 职责 |
|---|---|
| `packages/interaction/user-approval/src/index.ts:257` | request() 轮次内要求；`:304` decide()；`:312` never 服务内路径 |
| `packages/interaction/permission-presets/src/index.ts:159` | 预设服务 |
| `packages/interaction/commands/src/index.ts:225` | CommandRuntime |
| `packages/hooks/hook-protocol/src/{codec,merge,runner}.ts` | hook 协议库（runHook 永不抛出） |
| `packages/hooks/hooks-claude-code/src/index.ts:242` | CC 桥 ask 返回点 |
| `packages/guard/repeat-tool-reminder/src/index.ts:185` | 被拒调用也计数 |
| `packages/guard/timeout-policy/src/index.ts` | TOOL_TIMEOUT 包裹 |
| `packages/schedule/schedule/src/runtime.ts` | idle 后 followup 交付 |

## 编排

| 文件 | 职责 |
|---|---|
| `packages/subagent/subagent/src/index.ts:171` | SubagentRuntime；`:212` startContinuable |
| `packages/subagent/subagent/src/continuation.ts:349` | Activation 管理器；`:883` coldResume |
| `packages/subagent/tool-subagent/src/index.ts:392` | 三种委派入口（continuable/jobs 后台/前台） |
| `packages/preset/agent-presets/` | preset roster |
| `packages/jobs/jobs/src/index.ts:62` | JobRegistry SD |
| `packages/workflow/workflow/src/index.ts:157` | WorkflowEngine SD |
| `packages/plan/plan-mode/src/index.ts:184` | ctx.planMode |
| `packages/goal/goal/src/domain.ts:66` | goal/change 事件 |
| `packages/extensions/tool-cordis/` | 自我修改工具 |

## 应用与接口

| 文件 | 职责 |
|---|---|
| `packages/host/webserver/src/index.ts:59` | ctx.webServer |
| `packages/client/modules` | dsh.client 扫描与 `__DSH_BOOT__` |
| `packages/client/web/src/boot.tsx` | 浏览器壳 |
| `packages/client/runtime/src/client/sessions/conversation-assembler.ts:137` | Conversation Node 引擎 |
| `packages/typert/{protocol,generator,registry,loader}/` | Typert 四件套 |
| `packages/api/remotes/src/agent-lookup.ts` | agentId → 活 Agent |
| `packages/sdk/{protocol,server,client}/` | 自有 JSON-RPC |
| `packages/acp/acp/src/index.ts:215` | ACP 服务器与机器应答者 |
| `python/sdk-runtime/` | 单 exe 依赖清单 |
| `packages/mcp/mcp-client/` | MCP 客户端桥（外部 MCP 工具接到 ctx.tools） |
| `packages/session-query/` | 会话检索（live 优先 + SQLite FTS5） |

## 官方文档对照

本拆解与仓库自带文档互补（官方文档面向贡献者，本拆解面向架构理解）：

| 官方文档 | 对应章节 |
|---|---|
| `docs/architecture.zh.md` | 全局，尤其[第 4 章](04-核心循环.md) |
| `docs/cordis-primer.zh.md`、`cordis-tutorial/` | [第 1 章](01-cordis与组装.md) |
| `docs/agent-lifecycle.zh.md`（时序图） | [第 4 章](04-核心循环.md) |
| `docs/subsystems/*.zh.md`（50+ 子系统页） | 各专章 |
| `docs/capability-seams.zh.md` | [第 7 章](07-工具系统与执行世界.md) |
| `docs/cookbook/extension-cookbook.zh.md` | 扩展实操（本拆解的"速查表"之外的分步指南） |
| `docs/api-gateway.zh.md`、`docs/subsystems/{typert,web,web-server,client-modules}.zh.md` | [第 10 章](10-应用与对外接口.md) |
| `python/README.zh.md`、`python/development.zh.md`、`docs/cookbook/adding-a-conversation-node.zh.md` | [第 10 章](10-应用与对外接口.md) |
| `.agents/notes/implemented/architecture/` | 各设计决策的原始 Agent Note |

## 已知的文档-源码出入（探索中发现）

1. `docs/subsystems/core.zh.md` 把 `steering/message` 列为会话事件变体并称"十二种"，但当前核心 `SessionEventMap` 实为 13 成员且不含它（另漏列 `request/context` 与 `session/end-seed`）——steering 经 inbox 领取后以 `user/message` 落日志；`steering/message` 仅在持久化层作为 legacy 类型出现（加载时拒绝/迁移）。
2. `docs/capability-seams.zh.md` 把 `ctx.lsp` 实现写作 `lsp-local`，实际包名是 `lsp-stdio`（疑似重命名残留）。

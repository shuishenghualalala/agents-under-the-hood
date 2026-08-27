# 第 8 章：Session 持久化与恢复

> **文档同步跟踪**
> - 最后同步代码：`kimi-code` commit `c3a2ef0ce`（2026-08-27）
> - 同步方式：基于该文档撰写时的源码路径与提交记录梳理

> 本章导读：kimi-code 的 Session 不是“内存里的聊天对象”，而是一个**可崩溃恢复、可离线查看、可 fork、可归档、可删除**的持久化实体。读完本章你会理解：v1 怎么用 `wire.jsonl` 和 `session_index.jsonl` 把 Agent 状态写回磁盘；v2 的 DI × Scope 架构又怎么把这些机制拆成 `ISessionIndex`、`ISessionMetadata`、`IAppendLogStore`、`IAtomicDocumentStore` 和 `IWireService`；以及为什么 transcript 的“冷重建”只能覆盖 timeline，却还原不了实时细节。

## 8.1 为什么 Session 需要持久化

一个 Agent 会话通常包含：

- 多轮对话历史（`context.append_message`）。
- 工具调用、审批结果、LLM 请求轨迹。
- Plan / Goal / Swarm / Tower / Skill 等 Feature 的内部状态。
- 用户自定义的 todo、attachment、task、cron。
- 权限模式、活动工具集、MCP 配置。

如果这些都只活在内存里，进程一重启就全丢。kimi-code 的 Session 持久化要解决三个问题：

1. **崩溃/重启恢复**：用户重新打开 kimi-code，能继续刚才的会话。
2. **离线查看**：服务器可以把冷会话的 transcript 投影给 Web/Desktop UI，而不需要把整棵 Agent 树重新跑起来。
3. **派生与归档**：从某个 turn fork 出新会话、把旧会话归档、批量删除。

核心设计选择是：**Session 的真相在磁盘上的 wire 日志里，而不是在某张 sqlite 表或序列化对象里**。恢复就是按顺序重放这些 durable records。

## 8.2 v1 持久化模型：SessionStore + AgentRecords

### 8.2.1 磁盘布局

v1 的入口是 `SessionStore`（`packages/agent-core/src/session/store/session-store.ts`）。一个 kimi-code home 目录下大致这样：

```text
<home>/
  session_index.jsonl          # 全局会话索引，追加式
  sessions/
    <workDirKey>/              # 工作目录的哈希桶
      <sessionId>/
        state.json             # 会话元数据
        wire.jsonl             # 主 Agent 的 durable records
        blobs/                 # 大对象 offload
        upcoming-goals.json
        agents/                # 子 Agent 目录（如果有）
          <agentId>/
            wire.jsonl
            tasks/
            cron/
```

- `workDirKey` 由 `encodeWorkDirKey(normalizeWorkDir(workDir))` 生成（`packages/agent-core/src/session/store/workdir-key.ts`）。
- 目录 `mode: 0o700`，会话文件只对当前用户可见。

### 8.2.2 session_index.jsonl：全局索引

`session_index.jsonl` 是追加式 JSON Lines（`packages/agent-core/src/session/store/session-index.ts`）：

```jsonl
{"sessionId":"abc","sessionDir":"...","workDir":"..."}
{"sessionId":"abc","deleted":true}
```

特点：

- **追加优先**：新的 entry 会覆盖旧的；deletion marker 会让该 id 从内存 Map 中删除。
- **自愈**：`SessionStore.reindex()` 会扫描 `sessions/<workDirKey>/<sessionId>/state.json`，重新生成索引。
- **路径校验**：读索引时校验 `sessionDir` 是否真的在 `sessionsDir` 内部、目录名是否等于 `sessionId`，防止索引漂移导致越界。

### 8.2.3 AgentRecords：每个 Agent 的 durable log

每个 Agent 在构造时会挂一个 `AgentRecords` 实例，持久化到 `wire.jsonl`（`packages/agent-core/src/agent/index.ts`）。

```ts
this.records = new AgentRecords(
  this,
  options.persistence ??
    (options.homedir
      ? new FileSystemAgentRecordPersistence(join(options.homedir, 'wire.jsonl'), {
          onError: ..., blobStore: this.blobStore,
        })
      : undefined),
);
```

`FileSystemAgentRecordPersistence`（`packages/agent-core/src/agent/records/persistence.ts`）的行为：

- `append(record)` 先写入内存 pending 队列，再批量 flush 到文件，`open(..., 'a')` + `fsync`。
- `rewrite(records)` 用于迁移：当检测到 wire protocol 版本落后时，整文件重写为最新版本。
- 支持 `blobStore` offload：大对象（如图片）不落 wire，只落一个 blob ref。

### 8.2.4 wire.jsonl 里的 record 类型

`AgentRecordEvents`（`packages/agent-core/src/agent/records/types.ts`）定义了所有 durable 类型。最重要的几类：

| 类型 | 作用 |
|------|------|
| `metadata` | 协议版本、创建时间 |
| `turn.prompt` / `turn.steer` | 用户/系统注入的新 turn |
| `context.append_message` | 追加到上下文的历史消息 |
| `context.apply_compaction` | compaction 后替换上下文 |
| `context.undo` | 撤销 N 条用户输入 |
| `config.update` | 模型、system prompt 等配置 |
| `permission.set_mode` / `permission.record_approval_result` | 权限模式与审批结果 |
| `tools.set_active_tools` / `tools.register_user_tool` | 活动工具集 |
| `goal.create` / `goal.update` / `goal.clear` | Goal 状态 |
| `llm.request` / `llm.tools_snapshot` / `mcp.tools_discovered` | 可观测记录，仅用于回放/调试 |

### 8.2.5 恢复流程

恢复路径：

1. `SessionService` 或 `buildReplay` 构造 Agent 时传入 `AgentRecordPersistence`。
2. `Agent.resume()` 调用 `this.records.replay()`。
3. `AgentRecords.replay()` 逐行读取 `wire.jsonl`：
   - 第一行必须是 `metadata`，否则报错。
   - 根据 `metadata.protocol_version` 决定是否需要 `resolveWireMigrations`。
   - 每行通过 `restoreAgentRecord(agent, record)` 重建内存状态。
4. 若版本落后且允许重写，`rewrite(replayedRecords)` 把整个文件升到当前协议版本。
5. `markOpened()` 之后，新的 observability records（`llm.request` 等）才能继续写入。

`ReplayBuilder`（`packages/agent-core/src/agent/replay/index.ts`）负责在恢复期间收集记录，并支持 `range` 限制（用于 fork 时只取前 N 条）。

## 8.3 v2 持久化模型：DI × Scope + Store 抽象

v2 不再让一个 `SessionStore` 包揽所有文件操作，而是把持久化拆成**访问模式**层面的 Service：

- `IAppendLogStore`：追加日志（如 `wire.jsonl`）。
- `IAtomicDocumentStore`：原子文档（如 `state.json`）。
- `IBlobStore`：大对象。
- `IQueryStore`：查询索引（session list 的 minidb 读模型）。
- `ISessionIndex`：会话索引领域服务。

这些 Store 是 Scope 感知的：不同层级只关心自己要写的 key。

### 8.3.1 磁盘布局

v2 的地址由 `packages/agent-core-v2/src/workspace/sessionLifecycle/internal/addressing.ts` 决定：

```ts
workspacePersistenceScope(sessionsScope, workspaceId) // => sessions/<workspaceId>
sessionScopeOf(handlerScope, sessionId)              // => <handlerScope>/<sessionId>
sessionDirOf(homeDir, handlerScope, sessionId)         // => <homeDir>/<handlerScope>/<sessionId>
agentScopeOf(sessionScope, agentId)                  // => <sessionScope>/agents/<agentId>
```

所以 v2 一个 home 下的布局类似：

```text
<home>/
  sessions/
    <workspaceId>/
      <sessionId>/
        state.json               # ISessionMetadata -> IAtomicDocumentStore
        session_index.jsonl      # 和 v1 兼容的全局索引追加
        agents/
          main/
            wire.jsonl           # IWireService -> IAppendLogStore
          agent-0/
            wire.jsonl
          ...
  cache/
    query-store/                 # ISessionIndex 的 minidb 读模型
```

注意：v2 的 `state.json` 既可能放在 `<sessionScope>/state.json`，也可能出于兼容放在 `<sessionScope>/session-meta/state.json`（`legacySessionMetaScopeOf`）。

### 8.3.2 ISessionIndex：会话列表与恢复入口

`ISessionIndex`（`packages/agent-core-v2/src/app/sessionIndex/sessionIndex.ts`）是 App-scope 服务，提供 `get/listRecent/count/remove`。

它内部有两条路：

1. **权威目录扫描**（`sessionIndexSource.ts`）：递归读 `sessions/<workspaceId>/<sessionId>/state.json`，生成 `SessionSummary`。
2. **minidb 读模型**（`sessionIndexProjector.ts`）：把权威扫描结果投影到 `IQueryStore`，加索引、计数、分页。

`ISessionIndex.prepare()` 会异步把目录扫描投影到读模型；读模型未准备好时，请求直接走权威扫描 + `ISessionIndexMirror` 队列保证读己之写。

`SessionSummary` 字段（`sessionIndex.ts:10-22`）：

```ts
interface SessionSummary {
  readonly id: string;
  readonly workspaceId: string;
  readonly cwd?: string;
  readonly title?: string;
  readonly lastPrompt?: string;
  readonly createdAt: number;
  readonly updatedAt: number;
  readonly archived: boolean;
  readonly archivedAt?: number;
  readonly custom?: Record<string, unknown>;
  readonly lastTurnReason?: 'completed' | 'cancelled' | 'failed';
}
```

### 8.3.3 ISessionMetadata：会话元数据

`ISessionMetadata`（`packages/agent-core-v2/src/session/sessionMetadata/sessionMetadata.ts`）是 Session-scope 服务：

- `load()` 启动时读 `state.json`；没有则新建。
- `update(patch)` 原子更新内存 + 写 `IAtomicDocumentStore` + `ISessionIndexMirror`。
- `setArchived()` 切换归档位。
- `registerAgent()` 记录该 session 下有哪些 agent。

`SessionMeta` 的字段包括 id、title、titleKind、lastPrompt、createdAt、updatedAt、archived、agents、custom 等。其中 `version` 用于兼容迁移。

### 8.3.4 IWireService：Agent 的 durable log

`IWireService`（`packages/agent-core-v2/src/wire/wire.ts`）是 Agent-scope 服务：

- `appendRecord(record, dehydrate?)`：追加一条 wire record；可选 dehydrate 用于把大 content parts offload 到 blob。
- `readJournal()`：读整个 `wire.jsonl`，并自动从文件头记录的 protocol_version 开始迁移到当前版本。
- `seal()`：若当前 wire 文件为空，先写入一条 `metadata` 记录。

`WireService` 的实现（`packages/agent-core-v2/src/wire/wireService.ts`）：

- 它并不直接碰文件，而是依赖 `IAppendLogStore`。
- `readJournal` 会检测第一条记录：
  - 若无 metadata，则插入 metadata 并标记需要迁移。
  - 若 protocol_version 更新，则不再重写。
  - 否则按 `resolveWireMigrations` 链式迁移每条记录。
- 读完后若 `rewrittenRecords` 非空，调用 `log.rewrite` 原地升级文件。

### 8.3.5 wire protocol 版本与迁移

当前 wire protocol 版本是 `'1.5'`（`packages/agent-core-v2/src/wire/migration/migration.ts:19`）。

v2 的迁移器有一串升级器：`migrateV1_0ToV1_1`、`migrateV1_1ToV1_2`、`migrateV1_2ToV1_3`、`migrateV1_3ToV1_4`、`migrateV1_4ToV1_5`。

v1 也有对应的迁移器在 `packages/agent-core/src/agent/records/migration/`，但 v1 的协议版本叫 `AGENT_WIRE_PROTOCOL_VERSION`。v2 的 `wire.jsonl` 和 v1 的格式**概念相同**（都是 line-delimited JSON records，都有 metadata + event types），但 v2 把这套 record 语义用到了 DI × Scope 架构里，并且新增了 v2-only 类型（如 `profile.bind`）。

一个关键设计：v1 的 `restoreAgentRecord` 已经能识别 `profile.bind` 等 v2 类型，并把它映射回 v1 的 `config.update` + `tools.set_active_tools`（`packages/agent-core/src/agent/records/index.ts:51-72`）。这意味着 v2 创建的 wire 日志在 legacy 模式下也能被读回来。

## 8.4 恢复流程对比

### 8.4.1 v1 的 Session 恢复

v1 里 `SessionService` 维护活跃的 `Session` 对象。恢复时：

1. 用户或启动流程通过 `SessionStore` 找到 session 目录。
2. 构造 `Agent` 时把 `FileSystemAgentRecordPersistence(<sessionDir>/wire.jsonl)` 传进去。
3. `Agent.resume()` 重放所有 records。
4. 子 Agent 类似处理：每个子 Agent 有自己的 `wire.jsonl`。

### 8.4.2 v2 的 Session 恢复

v2 没有全局的 Session 门面。恢复由 `SessionManager` + `SessionLifecycleService` 协同完成：

1. `ISessionManager.resume(sessionId)`（`packages/agent-core-v2/src/app/sessionManager/sessionManagerService.ts:73-85`）做生命周期串行化：同一 session 的 resume 不会并发。
2. 通过 `ISessionIndex.get(sessionId)` 拿到 `SessionSummary`，确认 `workspaceId` 匹配。
3. `SessionLifecycleService.doResume`（`sessionLifecycleService.ts:342-371`）调用 `materializeSession`：
   - 创建 Session scope DI container。
   - 注入 session context、telemetry、skill catalog、instructions、MCP handle 等 seed。
4. 拿到 `IAgentLifecycleService`，若 main agent 不存在则创建它。
5. Agent 创建时会 `IWireService.seal()` 并 `IEventDispatcher.restore()`、`ManagedAgent.runtimeSet.restore()`，也就是从各自 `wire.jsonl` 重放 durable records。

```ts
// agentLifecycleService.ts:258-271
await handle.accessor.get(IWireService).seal();
managed!.attachDurableRuntimes();
await this.sessionMetadata.registerAgent(agentId, { ... });
...
await handle.accessor.get(IEventDispatcher).restore();
await managed!.runtimeSet.restore();
```

### 8.4.3 关键差异

| 维度 | v1 | v2 |
|------|-----|-----|
| 恢复入口 | `Agent.resume()` 直接读文件 | `SessionLifecycleService.doResume` → 构造 DI scope → Agent restore |
| Session 元数据 | `state.json` 由 `SessionStore` 直接读写 | `ISessionMetadata` 通过 `IAtomicDocumentStore` 读写 |
| Agent 记录 | `AgentRecords + FileSystemAgentRecordPersistence` | `IWireService + IAppendLogStore` |
| 索引 | `session_index.jsonl` 扫描 | `ISessionIndex`：权威扫描 + minidb 读模型 |
| 归档 | 修改 `state.json.archived` | `SessionMetadata.setArchived`；冷归档走 `coldSessionArchive.ts` |
| 作用域 | 一个 home 一个全局 SessionStore | Workspace-scope 的 `SessionLifecycleService`，每个 workspace 一个 handler |

## 8.5 生命周期操作

### 8.5.1 create

**v1**：`SessionStore.create({ id, workDir })` 创建目录，写 `session_index.jsonl`。

**v2**：`SessionManager.create` → `SessionLifecycleService.create` → `materializeSession` 创建 Session scope，然后创建 main agent，最后 `appendSessionIndexEntry` 写全局索引。

### 8.5.2 fork

fork 是 kimi-code 最常用的派生操作：从源会话某个 turn 切出一份新历史。

**v1**：`SessionStore.fork`（`session-store.ts:140-188`）：

1. 复制整个源 session 目录到目标目录。
2. 删掉 `upcoming-goals.json`。
3. 若指定 `turnIndex`，截断 `wire.jsonl` 到该 turn。
4. 写新 `state.json`，记录 `forkedFrom`。
5. 追加新 session 到 `session_index.jsonl`。

**v2**：`SessionLifecycleService.fork`（`sessionLifecycleService.ts:457-601`）更复杂：

1. 检查源 session 是否处于 active turn，是则拒绝。
2. 复制源 session 文件（跳过 `state.json`、`logs`、`upcoming-goals.json`、各 agent 的 `wire.jsonl`）。
3. 对每个 agent：
   - main agent：用 `sliceMainRecordsAtTurn` 按 turn 截断 `wire.jsonl`。
   - 子 agent：用 `sliceSubagentRecordsAtTime` 按 cutoffTime 截断。
4. 用 `copyAgentWire` 把截断后的 records 重写到目标 agent 目录，并追加 `forked` 记录。
5. 在目标 session 里创建对应 agent。
6. 写目标 `state.json`（`forkedFrom`、title 等）。
7. 追加目标 session 索引。

`sliceMainRecordsAtTurn`（`packages/agent-core-v2/src/workspace/sessionLifecycle/internal/forkTurnSlice.ts`）的核心逻辑：

- 识别 `context.append_message` 中 role=user 且 origin 是 user/skill_activation/plugin_command/shell_command 的记录作为“可见 turn”。
- 保留到目标 turn 结束之前的所有记录。
- 同时保留与之匹配的 `turn.prompt` / `turn.steer` 输入记录。

### 8.5.3 archive / restore

**v1**：`SessionStore.archive` 直接改 `state.json.archived=true`；restore 类似。

**v2**：分两条路径：

- **live session**：`SessionLifecycleService.archive` 调 `ISessionMetadata.setArchived(true)`，然后关 agent、刷日志、dispose scope。
- **cold session**（未加载到内存）：`coldSessionArchive.ts` 里的 `setColdSessionArchived` 直接读 `IAtomicDocumentStore` 里的 `state.json`，改 archived 位，再写回，并通过 `ISessionIndexMirror` 同步读模型。

`setSessionArchivedBatch` 支持批量归档，并发 8。

### 8.5.4 delete

**v1**：`SessionStore.delete` 删除目录，追加 deletion marker。

**v2**：`SessionLifecycleService.delete`（`sessionLifecycleService.ts:426-444`）：

1. 若 session 正 resume，先等待。
2. 若 session 在内存中，先 `close`。
3. `hostFs.remove(sessionDir)` 删除整个目录。
4. `ISessionIndex.remove(sessionId)` 从读模型删除。
5. 追加 `session_index.jsonl` deletion marker。

## 8.6 wire.jsonl 与 transcript 冷重建

### 8.6.1 wire.jsonl 是真相源

无论 v1 还是 v2，单个 Agent 的 durable 真相都在 `wire.jsonl`。它是一行一个 JSON record 的 append-only 日志，包含：

- `metadata` 头（protocol_version、created_at）。
- `turn.prompt` / `turn.steer`：turn 起点。
- `context.append_message`：历史消息。
- 各种状态记录（config、permission、tools、goal、compaction、undo）。
- 可观测记录（`llm.request`、`llm.tools_snapshot`、`mcp.tools_discovered`）。

v2 的 `WireRecord`（`packages/agent-core-v2/src/wire/record.ts`）是开放结构：

```ts
export interface WireRecord {
  readonly type: string;
  readonly time?: number;
  readonly [key: string]: unknown;
}
```

### 8.6.2 transcript 冷重建

`packages/transcript` 负责把 engine 事件转换成 UI 可渲染的 transcript。它自己不碰引擎状态，只消费 op。

冷重建（cold rebuild）指：session 没加载进内存时，kap-server 仍能从磁盘重建 transcript。

流程：

1. 读对应 agent 的 `wire.jsonl`。
2. `history/groupTurns.ts` 把 `context.append_message` 等折叠成 turn 树。
3. `history/foldFacts.ts` 把非 context 记录折叠成 tasks、interactions、todos、goal/plan/swarm meta。
4. 生成 `TranscriptOperation` 序列，喂给 `AgentTranscript.apply`。

限制（`packages/transcript/AGENTS.md`）：

> Cold rebuild is a two-level fold over `wire.jsonl` ... interactions left pending at shutdown fold to `cancelled`.
> These live-projected fields are NOT backfilled by the cold rebuild.

也就是说：

- 对话历史、turn 结构、task/todo/goal 状态可以冷重建。
- LLM 请求的 `usage`、`finishReason`、`timing`、tool frame 的实时 `progress`、prompts queue 等**实时投影字段**无法从 wire 日志回放。

## 8.7 v1 与 v2 持久化差异总结

| 维度 | v1 | v2 |
|------|-----|-----|
| 架构 | 单引擎内部类 | DI × Scope，持久化拆成 Store 抽象 |
| Session 管理 | `SessionStore`（全局单例） | `ISessionManager` + Workspace-scope `SessionLifecycleService` |
| 元数据 | 直接读写 `state.json` | `ISessionMetadata` → `IAtomicDocumentStore` |
| Agent durable log | `AgentRecords` + `FileSystemAgentRecordPersistence` | `IWireService` + `IAppendLogStore` |
| 全局索引 | `session_index.jsonl` 直接扫描 | `ISessionIndex` = 权威扫描 + minidb 读模型 + Mirror |
| fork 截断 | 复制目录 + 截断单文件 | 按 agent 分别截断 main/subagent records |
| 归档 | 只改 state.json | live 路径改 state + dispose；cold 路径直接改文档 |
| 读模型 | 无 | `IQueryStore` + `minidb`（`persistence_minidb_readmodel` flag） |
| 迁移 | v1 内部有迁移器 | v2 把迁移器集中在 `wire/migration`，v1 也能读 v2 的部分类型 |

## 8.8 关键实现入口

| 职责 | v1 文件 | v2 文件 |
|------|--------|--------|
| 全局会话索引 | `packages/agent-core/src/session/store/session-index.ts` | `packages/agent-core-v2/src/app/sessionIndex/sessionIndex.ts` |
| 会话 CRUD | `packages/agent-core/src/session/store/session-store.ts` | `packages/agent-core-v2/src/workspace/sessionLifecycle/sessionLifecycleService.ts` |
| 元数据 | `packages/agent-core/src/session/store/session-store.ts`（直接读写） | `packages/agent-core-v2/src/session/sessionMetadata/sessionMetadataService.ts` |
| Agent durable log | `packages/agent-core/src/agent/records/persistence.ts` | `packages/agent-core-v2/src/wire/wireService.ts` |
| 恢复/重放 | `packages/agent-core/src/agent/records/index.ts`、`packages/agent-core/src/agent/index.ts` | `packages/agent-core-v2/src/session/agentLifecycle/agentLifecycleService.ts`、`packages/agent-core-v2/src/wire/wireService.ts` |
| fork 截断 | `packages/agent-core/src/session/store/session-store.ts` | `packages/agent-core-v2/src/workspace/sessionLifecycle/internal/forkTurnSlice.ts` |
| 冷归档 | 无（只改 state.json） | `packages/agent-core-v2/src/workspace/sessionLifecycle/coldSessionArchive.ts` |
| 持久化抽象 | 无 | `packages/agent-core-v2/src/persistence/interface/appendLogStore.ts`、`packages/agent-core-v2/src/persistence/interface/atomicDocumentStore.ts` |
| transcript 冷重建 | 无 | `packages/transcript/src/history/groupTurns.ts`、`packages/transcript/src/history/foldFacts.ts` |
| wire 迁移 | `packages/agent-core/src/agent/records/migration/` | `packages/agent-core-v2/src/wire/migration/` |

## 8.9 小结

kimi-code 把 Session 当成**持久化的事件日志**而不是**内存对象**：

1. **真相在 `wire.jsonl`**：每个 Agent 的所有 durable 状态变化都按顺序落盘。
2. **恢复就是重放**：v1 是 `AgentRecords.replay()` 直接读文件；v2 是构造 DI scope 后让 `IWireService` / `IEventDispatcher` / runtime set 各自重放。
3. **索引与元数据分离**：v1 用 `session_index.jsonl` 扫描；v2 用 `ISessionIndex` 的权威扫描 + minidb 读模型，并通过 `ISessionIndexMirror` 保证读写一致。
4. **生命周期操作都是文件操作 + 作用域操作**：create/fork/archive/delete 最终都落为目录、`state.json`、`wire.jsonl` 和全局索引的变化。
5. **transcript 冷重建只能还原 timeline**：实时 LLM 细节、tool progress、prompts queue 等无法从 wire 日志回放。

理解这套机制，是读懂 agent-core-v2 的 `SessionLifecycleService`、`AgentLifecycleService` 以及 kap-server transcript 投影的前提。

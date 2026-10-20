# 第 13 章：运行时日志、Trace 与遥测

> 本章导读：dsh 的"日志"不是单一体。会话日志（第 5 章）是唯一真相源；运行时日志是插件本地的结构化诊断；遥测则是会话日志经投影、脱敏后的外部可观测副本。本章回答：插件运行时的诊断消息走哪条路、为什么默认不在控制台打印、trace 是怎么被收集的、遥测数据如何离开进程、以及这些日志最终落在哪里、会不会被自动清理。读完能分清三者的职责边界，并知道改行为该去哪一层。

## 13.1 三种"日志"的分工

dsh 同时存在三类记录，容易混为一谈：

| 名称 | 内容 | 消费者 | 是否进入模型历史 | 持久化 |
|---|---|---|---|---|
| **会话日志** | `SessionEvent`：user/assistant/tool 消息、turn/step 边界、chunk、request header 等 | 模型请求、回放、fork、压缩、UI transcript、持久化后端 | 是（经 surface 投影） | JSONL/SQLite |
| **运行时日志** | `ctx.logger` 输出的结构化诊断："某适配器配置回退"、"某 watcher 失败"等 | 开发者/运维，本地调试 | 否 | 默认只在内存 buffer，不持久化 |
| **遥测记录** | 从会话日志投影出的 `SessionTelemetryRecord`，经脱敏后送出 | 外部 OTel collector/可观测平台 | 否（它是会话日志的派生） | 由外部 collector 决定 |

关键设计事实：**运行时日志和遥测都不是会话日志的一部分**，也不会被模型看到；它们服务于不同的观测者，有各自的生命周期和失败语义。

## 13.2 运行时日志：`ctx.logger`

### 13.2.1 架构位置

Cordis 给每个 `Context` 挂载了一个 `logger` 服务。它的核心语义是：

- **结构化**：每条消息生成 `{ sn, ts, name, type, level, args, fiber }` 记录，而不是纯文本。
- **多 exporter**：一个 context 上可注册多个 exporter，每个 exporter 独立决定阈值、格式、输出目标。
- **fiber 派生命名**：未显式命名时，logger 名从当前 fiber 名推导（通常是插件名），所以日志天然带有"谁发出的"上下文。
- **拦截可配置**：通过 Cordis 的 `intercept` 机制可统一调整 logger 名或最低级别。

### 13.2.2 默认行为：只进内存 buffer

dsh 的默认 bundle **不挂载控制台 exporter**。`ctx.logger` 唯一的内置 exporter 是一个大小为 1000 的循环内存 buffer。这意味着：

- 产品正常运行时，这些日志不会刷到 stdout/stderr。
- 它们主要用于测试、运行时 attach 调试、或后续由某个 exporter 插件转走。
- 启动阶段若 Cordis 树本身还没立起来，`app-boot` 会绕过 `ctx.logger`，直接把配置/环境错误写到 `process.stderr`。

### 13.2.3 为什么不默认输出到控制台

原因不是疏忽，而是产品定位：dsh 的 CLI 可能长期作为后台服务运行（`dsh web`），且大量插件会在正常路径里产生 warn/info。如果默认全部落控制台，会把 stderr 变成噪音，同时泄露内部插件名、路径等实现细节。需要控制台可见性时，显式挂载 `logger-console` 插件即可。

### 13.2.4 `logger-console`：可选的控制台 exporter

`logger-console` 是 Cordis 社区提供的控制台 exporter，dsh 将其 vendor 进来。它会：

- 注册为 `ctx.logger` 的一个 exporter；
- 按 `[级别] 名称 消息` 的格式着色输出；
- 支持时间戳、label 宽度对齐、颜色等级、最大长度等配置。

它不在 base bundle 中，只在示例和测试 fixture 里使用。也就是说：**控制台日志在 dsh 是 opt-in，不是默认**。

## 13.3 Trace 与遥测：`session-telemetry`

### 13.3.1 设计定位：会话日志是可回放的 trace

dsh 没有采用传统分布式追踪的 span/trace 模型。它的 trace 概念直接建立在会话日志之上：

> 一次 agent 会话的完整 `SessionEvent` 流，就是一条可回放、可 fork、可审计的 trace。

因此，"收集 trace"不是去创建 spans，而是**决定把会话日志的哪些内容、以什么形式、在什么时机导出到外部系统**。

### 13.3.2 能力 seam：`session-telemetry`

`session-telemetry` 是一个能力 seam，由 Service Definition + Coordinator + Backend 构成：

| 角色 | 职责 |
|---|---|
| **Service Definition** (`SessionTelemetryBackend`) | 声明 `sharing` 策略、定义 record 格式、暴露 `emit/flush/shutdown` 契约 |
| **Coordinator** (`SessionTelemetryCoordinator`) | 负责 capture：订阅事件流、投影、脱敏、维护 handoff cursor、失败隔离 |
| **Backend** (`session-telemetry-otel`) | 真正的导出实现：OTel JS SDK + OTLP/HTTP logs |

 Coordinator 是 capture 侧，backend 是 export 侧；二者之间的边界是 `SessionTelemetrySink.emit(record)`。

### 13.3.3 Capture 的两条路径

| 模式 | 触发时机 | 行为 | 典型用途 |
|---|---|---|---|
| **live** | 每次 `session/event`、`agent/error`、`session/flush` 发生时同步触发 | 实时跟随会话事件流 | `FULL` 模式，全量上报 |
| **on-demand** | 只在显式调用 `captureSession(session, throughSeq?)` 时 | 从 handoff cursor 之后重放指定前缀 | `FEEDBACK_ONLY` 模式，只在用户反馈时上报 |

on-demand 模式下，Coordinator 不注册连续监听器，因此不会常驻 capture 状态；只在反馈事件发生时，读取该事件之前的会话日志前缀。

### 13.3.4 投影规则

会话事件不能直接作为遥测记录送出，需要经过固定投影：

- **ledger 记录**：与会话事件 1:1 对应，携带 `session.id`、`event.type`、`event.seq`、事件 body 的深度拷贝。
- **ops 记录**：没有日志对应物的操作信号，如 `agent-error`、`shutdown`。
- **first-chunk 投影**：同一个 `(turn, step)` 的多个 `assistant/chunk` 只送第一个。完整内容已在随后的 `assistant/message` 中组装，后续 chunk 是冗余流式信号。

### 13.3.5 脱敏：`session-telemetry/record` 瀑布

这是整个设计中最关键的安全决策：

- 每条 record 在交给 backend 之前，都会经过 `session-telemetry/record` 这个 waterfall 事件。
- seam 本身**不内置任何脱敏规则**，内层 `next()` 是直接透传。
- 部署方通过挂载 waterfall 监听器来定义自己的 scrubbing 策略。
- 抛出的规则会 fail-closed：只扣留那一条 record，不影响后续事件。
- **脱敏只作用于导出的副本，绝不改写 canonical session log**。

这个设计把"哪些数据能离开进程"的决策权交给部署方，而不是由框架做保守猜测——因为框架无法知道什么对某个部署是秘密。

### 13.3.6 OTel backend 的三种模式

`session-telemetry-otel` 是参考 backend，默认模式为 `DISABLED`：

| 模式 | 行为 | 数据离开进程？ |
|---|---|---|
| `DISABLED` | 不构造任何传输 pipeline；feedback 事件触发本地 warn 提示 | 否 |
| `FULL` | live capture，实时将会话事件经 OTel logs 送到 collector | 是 |
| `FEEDBACK_ONLY` | on-demand capture，仅在 `feedback/record` 事件时上报该事件之前的日志前缀 | 是（仅反馈上下文） |

配置直接透传给 OTel JS SDK：`exporter` 对应 `OTLPLogExporter`，`processor` 对应 `BatchLogRecordProcessor`，`shutdownTimeoutMillis` 是 dsh 自己加的外层截止期限。

### 13.3.7 资源属性与匿名身份

导出记录携带的 OTel Resource 包括：

- `service.name` / `service.version`：来自 `APP_IDENTITY`；
- `user.id`：来自 `$DSH_HOME/.anonymous-user-id` 的匿名 UUID（删除文件可重置）。

这意味着：遥测不直接关联登录用户，而是关联一个本地随机标识；部署方可通过 collector 侧再关联真实身份。

### 13.3.8 失败与关闭语义

- **emit 必须非阻塞**：backend 的 `emit()` 只是入队，不能阻塞 agent loop。
- **失败被包含**：capture 侧用 try/catch 包含每一步，backend 抛错只扣留当条记录，并写 `ctx.logger.warn`。
- **shutdown 有截止**：应用退出时，Coordinator 先为每个存活 session 发出 `shutdown` ops 记录，再 await backend shutdown；超过 `shutdownTimeoutMillis` 会超时拒绝，但不阻塞 teardown。
- **无 turn-boundary flush**：OTel backend 故意不实现 `flush()` 转发，因为并发 flush 会与 SDK 内部 drain 产生静默丢记录；导出 cadence 完全由 `scheduledDelayMillis` 控制。

## 13.4 插件怎么记日志

### 13.4.1 标准做法：用 `ctx.logger`

插件记录运行时诊断的标准入口：

```ts
ctx.logger.warn('settings: keeping last good "%s" after invalid stored section', ns)
ctx.logger.error(error)
```

优点：

- 与 fiber 生命周期绑定，插件卸载后相关 logger 不再活跃；
- 结构化记录可被任意 exporter 消费；
- 测试里可以 mock `ctx.logger.warn` 来断言行为。

### 13.4.2 命名约定

插件通常用领域前缀作为消息文本开头，例如：

- `llm-deepseek:`、`settings:`、`schedule:`、`skill-filesystem:`、`telemetry:`

这不是框架强制，而是约定，便于在启用 `logger-console` 时快速定位来源。

### 13.4.3 什么时候写什么级别

从现有插件的用法可以归纳出默认约定：

| 级别 | 用途 |
|---|---|
| `error` | 配置/状态损坏、持久化失败、关键服务初始化失败后仍保持最后可用状态 |
| `warn` | 非致命失败（watcher 失败、listener 抛错、backend 暂时不可达、telemetry 脱敏/关闭提示） |
| `info` | 压缩/计划模式等可见生命周期事件 |
| `debug` | 调试信息（实际代码中极少使用） |

### 13.4.4 启动诊断为什么直接写 `process.stderr`

`app-boot` 在 Cordis 树还没建立前就需要输出配置/环境错误。它接受一个 `warn(line)` 回调，默认实现是 `process.stderr.write(line)`。这属于**启动器自身的诊断通道**，不是 `ctx.logger` 的一部分。

同样，`dsh web` 启动成功后打印访问地址用的是 `console.log`，因为这是面向用户的显式反馈，不是插件诊断。

## 13.5 日志落在哪里、会清理吗

三类日志的存储位置和生命周期完全不同：

| 日志类型 | 默认保存位置 | 是否持久化 | 是否自动清理 |
|---|---|---|---|
| **会话日志** | `$DSH_HOME/sessions/<项目目录>/<sessionId>/session.log`（JSONL 后端）或 SQLite 文件 | 是 | 否 |
| **运行时日志** | 内存循环 buffer（1000 条），除非挂载 `logger-console` | 否 | buffer 满时覆盖，进程退出即消失 |
| **遥测记录** | 默认 `DISABLED`；启用后发送到外部 OTel collector | 由 collector 决定 | 由 collector 决定 |

### 13.5.1 会话日志的磁盘布局

默认 JSONL 后端把每个会话存成一个 append-only 文件：

```text
~/.dsh/
  sessions/
    <project-name>/
      <session-id>/
        session.log          # zstd 压缩的 JSONL 事件流
        session.log.header   # 可选的元数据旁路文件（取决于后端版本）
```

`$DSH_HOME` 默认是 `~/.dsh`，可通过环境变量覆盖。SQLite 后端则把所有会话集中到一个 SQLite 数据库文件，路径由配置决定；默认 bundle 不启用它。

### 13.5.2 会话日志会定期清理吗

**目前不会。**

- dsh 没有内置 retention、TTL 或自动删除旧会话日志的机制。
- 压缩（compaction）会追加摘要事件并遮蔽旧 surface，但**不删除物理事件**——旧事件仍在 `session.log` 里，只是不再进入模型派生历史。
- 删除 workspace 在 UI 上只是解除注册，会话目录和日志文件会被保留（UI 文案明确说明 "The folder and session logs will be kept"）。

因此，如果你担心磁盘增长，目前只能**手动删除 `~/.dsh/sessions/` 下的旧会话目录**；dsh 本身还没有提供生命周期策略。

### 13.5.3 运行时日志的生命周期

默认运行时日志不持久化，只驻留在内存：

- `LoggerService` 内置一个 1000 条的循环 buffer；
- 超过 1000 条后，最老的记录被覆盖；
- 进程退出，buffer 消失。

挂载 `logger-console` 后，日志会输出到控制台（stdout/stderr），但仍不会写入文件；它属于诊断通道，不是审计通道。

### 13.5.4 遥测记录的生命周期

遥测是"发送即结束"：

- `DISABLED` 模式下，不构造任何传输 pipeline，本地不保留记录；
- `FULL` / `FEEDBACK_ONLY` 模式下，Coordinator 把 record 交给 backend，`emit()` 只是入队；
- OTel SDK 的 `BatchLogRecordProcessor` 会批量发送，发送成功即释放；
- 本地不会长期保存已发送的遥测数据，也不会自动清理——因为本来就没有本地副本。

崩溃时未被 SDK 发送成功的记录会丢失，这是设计上的 best-effort/at-most-once 语义。

## 13.6 三者的关系与边界

用一句话概括各自的主人：

- **会话日志**属于 `Session`，是产品行为的真相源；
- **运行时日志**属于 `Context` / `Fiber`，是插件开发者的本地诊断；
- **遥测记录**属于部署方配置的外部系统，是会话日志经策略投影后的可观测副本。

三者互不影响：

- 运行时日志写失败不会影响会话追加；
- 遥测 backend 不可用不会阻塞 agent loop；
- 脱敏只改遥测副本，不改会话日志。

## 13.7 设计取舍

**为什么运行时日志默认不持久化**：持久化运行日志会带来新的存储/隐私/保留策略问题，而 dsh 已经把"需要持久的产品状态"放在会话日志里。运行时日志保持轻量、可选项，避免与真相源竞争。

**为什么遥测不用 spans**：传统 span 模型对可 fork、可恢复、可压缩的会话事件是 lossy 的。session event ledger 天然保留完整的事件序列和 replace 关系，更适合 dsh 的会话语义。

**为什么脱敏规则由部署方提供**：框架无法预先知道哪些字段是秘密。内置保守规则会造成误伤，同时给部署方"已脱敏"的虚假安全感。seam 只提供机制和明确默认（透传）。

**为什么 feedback-only 模式只读前缀不重放**：on-demand capture 不维护连续监听器，只在反馈事件触发时读取 `session.events` 到该 seq 为止。这避免了为可能永远不发送的反馈保留一份内存副本。

**为什么会话日志不自动清理**：session log 是真相源，任何自动删除都可能破坏 fork、恢复、审计和未来的查询能力。保留策略是一个部署级决策，dsh 目前把它留给用户或外部工具。

## 13.8 关键实现入口

| 文件 | 职责 |
|---|---|
| `vendor/cordis/src/logger.ts` | `LoggerService`、`Logger` facade、`Exporter` 接口 |
| `vendor/logger-console/src/shared.ts` | 控制台 exporter，可选插件 |
| `packages/session/session-telemetry/src/index.ts` | `SessionTelemetryBackend` SD、`SessionTelemetryRecord` 格式、`SessionTelemetrySink` 契约 |
| `packages/session/session-telemetry/src/coordinator.ts` | capture 协调器：live/on-demand、投影、脱敏瀑布、handoff cursor、失败隔离 |
| `packages/session/session-telemetry-otel/src/index.ts` | OTel backend：`FULL`/`FEEDBACK_ONLY`/`DISABLED`、SDK pipeline、shutdown deadline |
| `packages/bundle/base/cordis.patch.yml` | 默认 telemetry 配置：`DISABLED`、OTLP endpoint、processor 参数 |
| `packages/boot/app-boot/src/index.ts` | 启动阶段 `warn` 回调，默认写 `process.stderr` |
| `packages/session/session-persistence-jsonl/src/index.ts` | JSONL 后端：目录布局、zstd 压缩、按 session 一个 append-only 文件 |
| `packages/session/session-persistence-sqlite/src/schema.ts` | SQLite 后端 schema，无 TTL 字段 |

## 13.9 小结

运行时日志、trace、遥测在 dsh 中是三条清晰的线：

1. **运行时日志走 `ctx.logger`**，默认只进内存，控制台输出需要显式挂载 `logger-console`。
2. **Trace 就是会话日志本身**；收集 trace 意味着把 `SessionEvent` 流投影后导出，而不是创建独立的 spans。
3. **遥测走 `session-telemetry` 能力 seam**：Coordinator 做 capture 和脱敏，backend 做 export；OTel backend 默认关闭，支持 `FULL` 和 `FEEDBACK_ONLY`。
4. **插件统一用 `ctx.logger`** 记录本地诊断；启动诊断和用户显式输出则走各自独立的通道。
5. **会话日志持久化在 `~/.dsh/sessions/`**，默认不自动清理；担心磁盘增长需手动维护或外部脚本清理。
6. **三条线互不阻塞、互不改写**：会话日志保持唯一真相源，遥测是其受控副本，运行时日志是轻量本地诊断。

理解了这套分工，就能解释为什么 dsh 默认很安静（没有控制台日志）、为什么遥测默认关闭、为什么插件不需要自己实现导出逻辑，以及为什么 `~/.dsh/sessions/` 会随时间增长。

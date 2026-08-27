# DeepSeek Harness 工作原理（how-deepseek-harness-works）

> 这是一份面向工程师的架构走读文档：不按目录逐个文件介绍，而是讲清这个系统解决什么问题、核心循环是什么、哪些抽象是稳定契约、状态在哪里、副作用在哪里受控、设计取舍是什么。
>
> 阅读对象：`deepseek-harness` 仓库（下称 dsh），版本 `0.1.0-rc.5`（developer preview）。本文全部论断均经源码或官方文档验证；推断之处会明确标注。

## 十分钟概览

**dsh 是什么**：DeepSeek 开源的 agent harness（智能体执行骨架）。它不做模型，做"模型之外的一切"——会话、工具、审批、沙箱、持久化、UI、协议。`npx @deepseek-ai/dsh web` 启动 Web UI；`--profile headless` 则是一次性任务运行器。

**最重要的一个事实**：dsh 里没有"内核"。产品的每一部分——模型适配器、工具注册表、会话日志、**包括 agent 循环本身**——都是插件，挂载在 Cordis 框架的共享上下文上。扩展 dsh 不是打补丁，而是把新插件挂到别的插件旁边；每个插件的注册都是可逆副作用，卸载即撤销。

**三个支撑性设计**：

1. **一切皆插件（Cordis）**。插件向共享 `Context` 贡献服务（`ctx.llm`、`ctx.tools`、`ctx.sessions`……）、类型化事件和 effect。加载顺序不由编排决定，而由每个插件声明的 `inject` 依赖推导；依赖被替换，依赖方自动重载。→ [第 1 章](01-cordis与组装.md)

2. **模型可见 ⟺ 已记录**。抵达模型请求的一切，必须能从会话日志（一条仅追加的 `SessionEvent` 流）重建，且有一个运行时不变量在每次请求发出前逐字节断言这一点。于是 fork、恢复、回放、transcript、遥测、压缩全部从同一条日志派生，系统没有第二份事实来源。→ [第 4 章](04-核心循环.md)、[第 5 章](05-会话日志与持久化.md)

3. **能力 seam 三角色**。每个能力（文件系统、shell、子进程、终端、沙箱、LSP、subagent……）都由三种角色组成：声明接口的 **Service Definition**、实现它的 **Service Provider**、消费它的 **Consumer**（通常是面向模型的工具）。换一个 Provider，整个产品的行为就变了——把 `ctx.fs` 和 `ctx.subprocess` 指向远程沙箱，Bash、PTY、LSP 就一并搬了过去，工具层零改动。→ [第 7 章](07-工具系统与执行世界.md)

**组装方式**：运行中的 dsh 是一棵插件树，由一组 patch 层按序叠加在空条目列表上组合而成：bundle（组合包，随发行版交付的 `dsh-base` / `dsh-web-app` / `dsh-headless`）→ profile 的 `cordis.patch.yml` → home 级 patch → `--patch` overlay。`dsh --profile web --dump-config` 打印实际启动的配置树，其中任何条目都可被你自己的 patch 替换。→ [第 1 章](01-cordis与组装.md)

**核心循环一句话**：一个**轮次（turn）** 在领取输入前打开、在不再欠任何工作时关闭；一个**步骤（step）** 是一次模型请求加它调用的工具。每个决策点（`agent/pre-step`、`agent/request`、`llm/stream`、`tools/pre-execute`/`execute`/`post-execute`）都是 waterfall 事件——监听器不调 `next()` 就能否决内建行为。→ [第 4 章](04-核心循环.md)

## 章节索引

| 章 | 主题 | 读完能回答 |
|---|---|---|
| [第 1 章](01-cordis与组装.md) | Cordis 框架与插件组装 | Context/Fiber/effect/事件五模式是什么；profile/bundle/patch 如何叠出运行的插件树；`dsh` 启动序列 |
| [第 2 章](02-仓库分层.md) | monorepo 地图 | 45 个包怎么分组；host/client 双面构建是什么；改代码该去哪一层 |
| [第 3 章](03-配置与数据落点.md) | Harness home 与配置 | `$DSH_HOME` 里有什么；配置目录、凭据、设置的分层 |
| [第 4 章](04-核心循环.md) | 轮次与步骤 | turn/step 语义；inbox 如何工作；三个事件域的分工；一次完整轮次的 14 个阶段 |
| [第 5 章](05-会话日志与持久化.md) | 事件溯源 | SessionEventMap；surface 投影；持久化后端；版本机制；压缩如何不破坏可重建性 |
| [第 6 章](06-LLM能力层.md) | 模型适配 | 为什么有自有流式词汇表；适配器契约；一次请求的完整生命周期；重试与 token 计量 |
| [第 7 章](07-工具系统与执行世界.md) | 工具与 seam | 工具流水线五段；seam 三角色速查；"执行世界"共享的含义；E2B 实证 |
| [第 8 章](08-交互审批与安全.md) | 纵深防御 | 七层防御各防什么；审批生命周期；外部 hook 桥；guard 循环卫生 |
| [第 9 章](09-编排能力.md) | 多 agent 与后台工作 | subagent 为什么是 seam；preset 的 isolate realm；jobs/workflow/plan/goal/schedule |
| [第 10 章](10-应用与对外接口.md) | UI 与协议 | Web UI 从浏览器到 agent-loop 的路径；为什么需要 Typert；SDK/ACP/Python 的分工 |
| [第 11 章](11-预制的Agent模式.md) | 预制的 Agent 模式 | host/preset 两层平面；极简/标准/PTC/创造四副面孔的能力递增链 |
| [第 12 章](12-最小复刻指南.md) | 重建最小 dsh | 复刻这个架构需要实现哪些组件、按什么顺序 |
| [源码地图](reference.md) | 关键实现入口 | 按主题组织的文件级索引，供查证 |

## 速查：想改行为，去哪一章

| 目标 | 机制 | 章节 |
|---|---|---|
| 加模型提供方 | 在 `ctx.llm` 注册适配器 | [6](06-LLM能力层.md) |
| 加面向模型的工具 | 在 `ctx.tools` 注册 | [7](07-工具系统与执行世界.md) |
| 加 shell/文件系统/沙箱后端 | 注册对应 seam 的 Provider | [7](07-工具系统与执行世界.md) |
| 拦截请求/工具/轮次 | 挂 `agent/*`、`llm/stream`、`tools/*` waterfall | [4](04-核心循环.md) |
| 加模型可见上下文 | `agent.inject()` 或扩展 `SessionEventMap` | [4](04-核心循环.md)、[5](05-会话日志与持久化.md) |
| 改审批/权限行为 | 组合 approval/permission 能力或当应答者 | [8](08-交互审批与安全.md) |
| 加子 agent 后端 | 注册 subagent Provider | [9](09-编排能力.md) |
| 加 UI 会话节点 | `ConversationNodeDefinition` + keyed renderer | [10](10-应用与对外接口.md) |
| 换掉某一层组合 | 写 bundle 或 patch | [1](01-cordis与组装.md) |
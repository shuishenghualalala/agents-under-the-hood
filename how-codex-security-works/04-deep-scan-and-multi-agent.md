# 第 4 章：Deep Scan 与多 Agent

## 本章导读

读完这一章，你会知道：

1. 为什么需要 deep 模式，以及它和标准扫描的根本区别；
2. deep 模式如何用"多路 discovery worker + 归并 + 集中验证"组织多个 Codex 子代理；
3. 它依赖的 Codex 原生多 Agent 机制（multi-agent v2）以及外壳层的约束；
4. deep 扫描与标准扫描的编排差异在哪里。

## 1. 为什么需要 deep

标准扫描刻意保持"单遍、紧凑"：一次 discovery、一次 validation、一次 attack-path，不给阶段队列、不重复上下文。对小型仓库这很合适。但对大型/高价值代码库（大型 monorepo、安全敏感项目），单遍扫描的问题是：

- **覆盖深度不够**：一个 agent 的上下文窗口有限，逐文件翻完一遍后，深层的跨文件数据流、配置与业务逻辑交织的漏洞容易被漏掉；
- **并行度不足**：整个扫描串行在一个线程里，wall-clock 时间与 token 消耗都被单点限制。

deep 模式的回答是：**让多个互相独立的 discovery worker 并行扫，语义上归并结果，再做一次集中的验证与攻击路径分析**。它是"标准流程的外层循环"。

## 2. 全景流程

```
CodexSecurity.run(..., { mode: "deep" })
   │  skill 选择：security-scan ─► deep-security-scan
   ▼
deep-security-scan SKILL.md
   │
   ├─ 阶段1：FAN-OUT（发现）
   │    MCP: start_codex_security_deep_scan
   │    ┌────────────┐ ┌────────────┐ ┌────────────┐
   │    │ worker #1  │ │ worker #2  │ │ worker #N  │  各自独立的发现任务
   │    │ 文件分区 A │ │ 文件分区 B │ │ 文件分区 C │  （可 24h 量级长跑）
   │    └────────────┘ └────────────┘ └────────────┘
   │          └──────────┬──────────────┘
   │                      ▼
   │    semantic reducer —— 归并各 worker 的候选
   │    （去重、跨 worker 对齐同一 finding 的多个证据）
   │
   ├─ 阶段2：集中验证（一次）
   │    validation —— 对归并后的候选集统一验证
   │    attack-path —— 对 reportable/deferred 候选做可达性分析
   │
   └─ 阶段3：reporting
        组装 findings/coverage → complete_codex_security_scan → 封签
```

## 3. 两个关键机制

### 3.1 多路独立 discovery worker

deep 模式的核心是 `start_codex_security_deep_scan` 这个 MCP 工具（注册在 `.mcp.json`，`tool_timeout_sec: 86400`，即单次最长 24 小时）。每个 worker 是一个**独立的发现任务**，负责自己那块文件分区/关注面，互相之间不共享上下文——这正是"独立"的含义：不存在一个巨大的共享上下文把所有人都卡住。

外壳层通过 `onWorkerStatus` observer 持续接收每个 worker 的状态，CLI 侧渲染成"N 个 worker 各自进度"的仪表盘。

### 3.2 Semantic reducer 归并

多个 worker 各自产出的候选集会有重叠与碎片化：

- 同一个漏洞可能被多个 worker 从不同文件出发发现（重复）；
- 同一 finding 的证据被拆散在不同 worker 的候选里（碎片化）。

reducer 的职责是**语义归并**：按 finding 的身份锚点对齐、合并证据，产出一个统一的候选集，交给集中验证阶段。这一步是 deep 模式正确性的关键——如果这里不去重，后续验证会重复烧 token；如果这里不合并证据，攻击路径分析会缺少上下文。

## 4. 与标准扫描的编排差异

| 维度 | 标准扫描 | deep 扫描 |
|------|---------|-----------|
| skill | `security-scan` | `deep-security-scan` |
| discovery | 单遍、单 agent | 多路独立 worker |
| 候选归并 | 无（天然单一） | semantic reducer |
| validation | 一次，compact 模式 | 一次，集中式（对归并后的完整集） |
| attack-path | 一次，compact 模式 | 一次，集中式 |
| 覆盖声明 | coverage `repository` | coverage `deep_repository` |
| 典型成本/时长 | 低、快 | 高、可到小时/天级 |
| 外壳监控 | `onProgress` | `onProgress` + `onWorkerStatus` |

关键点：**验证、攻击路径、reporting 在 deep 模式下仍然只做一次**。deep 不是"把标准流程重复 N 遍"，而是"把发现阶段并行化，其余阶段集中化"。这样既摊薄了发现成本，又避免了对同一候选做 N 次验证的浪费。

## 5. 依赖的 Codex 原生多 Agent 机制

deep 模式的多 worker 并不是插件自己 fork 线程实现的，而是依赖 **Codex 原生 multi-agent v2**。这体现在外壳层的默认配置里（`config.ts` 的 `DEFAULT_CODEX_CONFIG`）：

```toml
features.multi_agent_v2.enabled = true
features.multi_agent_v2.max_concurrent_threads_per_session = 9
```

并且 `config.ts` 的 `validateNativeMultiAgentV2Overrides()` 会**强制**这一点：用户不能把 `features.multi_agent_v2` 关掉，也不允许用旧的 `agents.max_threads`（v1 设置），否则直接抛 `ConfigurationError`。理由（从代码推断）：deep 模式的 worker 调度依赖 v2 的原生线程/预算语义，关闭它会破坏 deep 编排的正确性。

> 这里的"多 Agent"指的是**同一个 Codex 会话里的多线程子代理**（plugin 让它们在 worker 分区上跑发现任务），不是多个独立 `codex-security` 进程。真正的多进程并行是另一回事——那是第 6 章 `bulk-scan` 的职责（多个仓库各自一个 `CodexSecurity.run()`，用多个 worker 进程并行）。

## 6. 取舍与失败模式

| 情况 | 行为/理由 |
|------|----------|
| worker 长时间运行 | 单次 MCP 工具超时 86400s，配合可恢复的 SQLite 推进 |
| worker 中途失败 | 独立 worker 失败不拖垮整体；归并阶段容忍缺失（从代码结构推断） |
| 归并后候选过多 | 集中验证仍只做一次（compact 语义），控制成本 |
| 小仓库误用 deep | 成本浪费——skill 选择由 `--mode deep` 显式触发，用户负责按规模选 |

设计理由：deep 模式是"**用钱买覆盖**"的明确选择。系统没有试图隐藏这个代价，而是把它结构化：并行发现（贵但并行）、一次归并、一次集中验证（避免重复花钱）。这也和"扫描产物必须可校验"的总原则一致——无论多贵，产出物都要过同一套封签契约。

## 7. 关键实现入口

| 职责 | 位置 |
|------|------|
| deep skill | `_bundled_plugin/skills/deep-security-scan/SKILL.md` |
| deep 启动 MCP 工具 | `_bundled_plugin/mcp/`（`start_codex_security_deep_scan`） |
| worker 状态上报 | 外壳层 `onWorkerStatus` observer（`api.ts` / `cli.ts`） |
| multi-agent v2 强制 | `sdk/typescript/src/config.ts` 的 `validateNativeMultiAgentV2Overrides()` |
| 默认并发上限 | `config.ts` 的 `DEFAULT_CODEX_CONFIG.features.multi_agent_v2`（9） |
| 工具超时 | `_bundled_plugin/.mcp.json`（`tool_timeout_sec: 86400`） |

## 小结

deep 模式把"昂贵但可并行"的发现阶段拆成多路独立 worker，用 semantic reducer 归并，再回到单次集中验证与封签。它依赖 Codex 原生 multi-agent v2，并由外壳层在配置上强制该机制可用。下一章进入插件层的核心资产：workbench SQLite 工作台如何承载所有这些状态。

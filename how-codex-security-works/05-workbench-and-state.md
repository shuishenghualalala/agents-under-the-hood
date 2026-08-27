# 第 5 章：Workbench 与状态

## 本章导读

读完这一章，你会知道：

1. 扫描的"状态"放在哪里、以什么形态存在；
2. 外壳层如何初始化它（`runWorkbench()`），插件层如何读写它；
3. 13 个 skill 是如何围绕同一套状态协同的；
4. 这套状态设计支撑了哪些能力（可恢复、可审计、多 skill 共用）。

## 1. 状态在哪：一个 SQLite 数据库

整个扫描系统的持久化状态集中在一个 SQLite 文件 `workbench.sqlite3`。它的位置由环境变量决定：

```
CODEX_SECURITY_STATE_DIR   （用户可覆盖，推荐显式设置）
   └─ 默认：$CODEX_HOME/state/plugins/codex-security
        └─ workbench.sqlite3
```

这个位置由 `codexSecurityStateDirectory()`（`runtime.ts`）解析，并被**注入到 agent 的环境变量**里（`scanPrompt()` 注入 `CODEX_SECURITY_STATE_DIR`），所以插件侧的 Python 脚本和 agent 看到的路径是同一个。

> 为什么不在内存里 / 不放在扫描目录里？因为状态的生命周期**长于一次扫描**：中断恢复、`scans`/`findings` 命令的查询、多 skill 复用，都依赖状态在进程退出后依然存在。它是"平台级"的，不属于某次扫描产物。

## 2. 外壳层如何初始化它：runWorkbench()

外壳层不直接操作 SQLite，而是通过一个 Python 命令子进程：

```
runWorkbench(args)  (runtime.ts)
   └─ python -I -B scripts/workbench_db.py <args>
       （-I：隔离模式；-B：不写 .pyc 字节码缓存）
```

外壳层只在运行时准备阶段调用它做**schema 初始化**（建表），之后的所有读写都由 agent 经 MCP 工具间接完成。也就是说：**TypeScript 层持有"数据库存在"的事实，Python 层持有"数据库长什么样"的事实，agent 持有"往里面写什么"的事实**——三层各司其职。

`-I -B` 这两个标志是安全细节：隔离模式避免加载用户 site-packages 里的意外模块，禁止字节码缓存避免在状态目录旁留下可被污染的文件（第 8 章再谈这类加固思路）。

## 3. 状态模型：六阶段 + 记录类型

状态的核心结构围绕两个轴组织：

**轴一：扫描阶段**（`workbench_constants.py`）：

```
preflight → threat_model → discovery → validation → attack_path → reporting
```

每个阶段有明确的推进工具（`update_codex_security_scan_progress`），阶段一旦完成不重开。

**轴二：记录类型**。每个阶段的产出是工作台里的一种记录，MCP 工具围绕它们读写：

| 记录 | 产生阶段 | 承载内容 |
|------|---------|---------|
| review items | discovery | 待审文件清单（分页枚举） |
| candidates | discovery | 候选漏洞（anchor、locations、instance、discovery 证据） |
| candidate_validations | validation | 每个候选恰好一条验证结论 |
| attack_paths | attack_path | reportable/deferred 候选的攻击路径 |
| draft | reporting | 语义 findings + coverage 草稿 |
| sealed artifacts | 封签后 | 规范 JSON + report.md + sarif |

MCP 工具命名与记录一一对应：`record_codex_security_discovery_candidates`、`record_codex_security_candidate_validations`、`record_codex_security_candidate_attack_paths`、`record_codex_security_scan_draft`……**一套记录类型，所有 skill 复用**——这是 13 个 skill 能协同的关键：它们不是各自为政的脚本，而是同一份状态的不同操作者。

## 4. 13 个 skill 如何围绕状态协同

`_bundled_plugin/skills/` 下有 13 个 SKILL.md。按它们在状态机里的角色可以分成几族：

| 族 | skill | 在状态里做什么 |
|----|-------|---------------|
| 扫描执行 | `security-scan`、`deep-security-scan`、`security-diff-scan` | 走完六阶段主流程 |
| 分析 | `threat-model`、`attack-path-analysis`、`finding-discovery`、`validation` | 阶段内的分析 skill（被主 skill 引用，如 `$threat-model`、`$validation`） |
| 治理 | `track-findings`、`define-security-policy`、`propose-security-hardening` | 把发现沉淀为政策/加固项（如 manifest 的 `hardening` 字段） |
| 处置 | `triage-finding`、`fix-finding`、`vulnerability-writeup` | 对已有 finding 的三方处置（分类/修复/撰写） |

注意两点：

1. **阶段型 skill 是被"引用"而不是被"并行调用"**：`security-scan` 的流程里写着"Run `$threat-model`"、"Run `$validation` once"——这是一个 agent 在自己会话里按 skill 文档分步执行，不是外层去调度另一个 skill。
2. **处置型 skill 服务于扫描之后**：`fix-finding` 对应 CLI 的 `patch` 命令（`runSkill()` 经 `codex exec` 驱动），`vulnerability-writeup` 生成报告的 writeup 部分——它们读同一份状态，产出再落回状态/产物。

## 5. 状态带来的三个能力

这套"单一 SQLite + 统一记录类型"的设计，直接支撑了系统最关键的三个非功能性目标：

| 能力 | 机制 |
|------|------|
| **可恢复** | 阶段与记录持久化在 SQLite；中断后重新打开工作台（`open_codex_security_workspace` / `get_codex_security_scan_context`）可继续推进，不重来 |
| **可审计** | 每个候选/验证/攻击路径都有记录，`scans`/`findings` 命令能查询；reporting 时"每个文件都被交代过"由 coverage 记录保证 |
| **多 skill 协同** | 一套记录类型 + 一套 MCP 工具 = 所有 skill 可组合：deep 的归并、diff 的增量、fix 的闭环都基于同一状态 |

还有一个容易被忽略的点：**userContext 的不可变性**。`update_codex_security_scan_progress` 返回的 `structuredContent.scan.userContext` 是"当前阶段的不可变、不可信分析上下文"，阶段内所有 worker 都沿用，阶段间不允许把"上一阶段的结果"当"指令"喂给下一阶段。这是把"agent 的幻觉可能被当作事实传播"这个风险摁住的设计。

## 6. 取舍与边界

| 情况 | 说明 |
|------|------|
| 为什么不用 JSON 文件当状态 | 并发 worker 写入会冲突、无查询能力；SQLite 单文件、事务化、可被 `scans` 命令查询 |
| 为什么外壳层不直接读写 | 保持"领域逻辑在插件层"的分层；TypeScript 只负责初始化与校验，不复制领域规则 |
| 为什么用子进程跑 Python | 隔离（`-I`）+ 无需在 Node 里嵌入 Python 绑定；代价是每次调用有进程启动开销，但调用频率低（agent 驱动的） |
| 状态文件安全 | 状态目录属于扫描进程可写区（加固权限 profile 里放行），见第 8 章 |

## 7. 关键实现入口

| 职责 | 位置 |
|------|------|
| 状态目录解析 | `sdk/typescript/src/runtime.ts` 的 `codexSecurityStateDirectory()` |
| 工作台初始化 | `runtime.ts` 的 `runWorkbench()`（`python -I -B scripts/workbench_db.py`） |
| 阶段常量 | `_bundled_plugin/scripts/workbench_constants.py` |
| 工作台实现 | `_bundled_plugin/scripts/workbench_db.py` |
| MCP 工具定义 | `_bundled_plugin/mcp/` + `.mcp.json` |
| 13 个 skill | `_bundled_plugin/skills/`（按族分布） |
| env 注入 | `api.ts` 的 `scanPrompt()` |

## 小结

Workbench 是插件的"记忆"：一个 SQLite 文件承载六阶段推进与所有记录类型，MCP 工具是 agent 与它之间的接口，13 个 skill 是对同一份状态的不同操作者。它让扫描可恢复、可审计、可组合。下一章离开单次扫描，看它如何被容器化、批量化成"对一批仓库的流水线"。

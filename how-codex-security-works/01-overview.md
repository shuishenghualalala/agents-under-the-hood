# 第 1 章：总览

## 本章导读

读完这一章，你会知道：

1. `codex-security` 到底是什么（以及它**不是**什么）；
2. 它要解决的核心问题是什么；
3. 整个系统按架构责任划分出的三层结构；
4. 它和普通安全扫描器（Semgrep、CodeQL、Snyk 等）的本质差异。

## 1. 它是什么

从 NPM 视角看，`@openai/codex-security` 是一个 TypeScript 库 + CLI；从用户视角看，它把"跑一遍 `codex-security scan <目录>`"翻译成了"让一个 Codex agent 去当安全分析师"。

它不是一个用规则引擎匹配漏洞的模式扫描器。它的"扫描引擎"是一个 **LLM agent**：一个被 `@openai/codex-sdk` 作为子进程启动的 Codex agent，带上一个捆绑安装的插件（`_bundled_plugin`），这个插件通过 13 个 SKILL.md 告诉 agent"你怎么一步步做安全审计"，通过一组 MCP 工具让 agent 读写一个 SQLite 工作台来推进扫描、记录候选、封签产物。

因此架构上存在一个关键事实：**扫描的主循环发生在 Codex agent 里，而不是 TypeScript 进程里**。TypeScript 层（CLI/SDK）的职责是：

- 把目标仓库、模式、权限、认证准备好（preflight / 配置加固）；
- 拉起 agent、监控进度、汇总成本；
- 对 agent 封签出来的产物做**契约校验**（schema + 指纹 + SHA-256 摘要）；
- 提供一个可审计、可恢复、可批量化的外壳（容器、CSV 批量扫描）。

## 2. 它解决什么问题

传统安全扫描器在"静态分析"上很强，但在下面这些地方有系统性短板：

1. **跨文件/跨语义理解弱**：规则引擎很难理解"这个 `exec()` 的输入来自哪个上传接口"这种跨文件数据流。
2. **误报多、需要人工二次确认**：扫描结果通常只是一堆告警，没有验证证据、没有攻击路径。
3. **修复建议空洞**：给出漏洞位置，但很少给出"改动前后 diff + 验证方法"。
4. **供应链 / 配置 / 业务逻辑盲区**：很多漏洞发生在依赖、权限配置、业务编排里，规则很难覆盖。

`codex-security` 的回答是：**不写规则，让一个具备代码理解能力的 agent 走一遍"分析师"流程**——威胁建模、逐文件发现候选、对候选做验证、攻击路径分析、给出修复建议，并且每一步都落在 SQLite 里形成可审计的证据链。

## 3. 系统边界

整个系统对外暴露三类能力，对应三种部署形态：

| 能力 | 入口 | 形态 |
|------|------|------|
| 单仓库/路径标准扫描 | `codex-security scan` | CLI，本地或 CI |
| Deep 多轮扫描 | `codex-security scan --mode deep` | CLI，更彻底的多 Agent 扫描 |
| 批量仓库扫描 | `codex-security bulk-scan --inventory inventory.csv` | CLI + Docker 容器 |
| Diff/PR 扫描 | `codex-security scan --mode diff`（refs/working_tree） | CLI |

底层统一复用的执行单元是 `CodexSecurity.run()`（SDK 核心，`sdk/typescript/src/api.ts`），四种模式只是给它喂了不同的 target / mode / skill 选择。

## 4. 三层架构

按"架构责任"而不是"目录"来划分，系统是三层：

```
┌──────────────────────────────────────────────────────────┐
│  第 1 层：外壳层（TypeScript）                             │
│  cli.ts / api.ts / runtime.ts / config.ts / contract.ts  │
│  multiscan.ts / result.ts / cost.ts                       │
│  职责：命令分发、运行时准备、配置加固、契约校验、成本统计    │
├──────────────────────────────────────────────────────────┤
│  第 2 层：插件层（Python + Markdown + 编排脚本）           │
│  _bundled_plugin/                                          │
│    skills/*.md        —— 13 个 SKILL，定义 agent 行为     │
│    scripts/workbench_db.py —— SQLite 工作台               │
│    scripts/finalize_scan_contract.py —— 封签 finalizer    │
│    mcp/server.mjs     —— MCP 服务器，暴露工具给 agent      │
│    schemas/           —— 契约 JSON Schema                 │
│  职责：真正的"扫描逻辑"（发现/验证/攻击路径/修复）           │
├──────────────────────────────────────────────────────────┤
│  第 3 层：执行层（外部进程）                                │
│  codex 二进制（@openai/codex-sdk 子进程）+ Codex agent     │
│  职责：执行扫描主循环，调用 skill 与 MCP 工具                │
└──────────────────────────────────────────────────────────┘
```

> 一个容易混淆的点：插件里的 Python 脚本（workbench、finalizer）是由 **Codex agent 通过 MCP 工具**间接调用的，不是 TypeScript 层直接调用的。TypeScript 层只有一个 `runWorkbench()` 负责初始化数据库 schema。这条调用链在后续章节会反复出现。

## 5. 和普通扫描器相比，它的设计取舍

| 维度 | 传统规则扫描器 | codex-security |
|------|--------------|----------------|
| 检测原理 | 语法/AST 规则匹配 | LLM agent 语义分析 |
| 误报 | 高，需人工二次确认 | 有 validation/attack-path 阶段，agent 自证 |
| 解释性 | 告警 + 规则 ID | markdown 报告 + 代码证据 + 修复 diff |
| 成本 | 便宜、快 | 贵、慢（LLM token）——所以才有 deep/批量/容器化 |
| 可审计性 | 规则版本可复现 | 封签产物 + SHA-256 摘要 + 身份指纹 |
| 覆盖盲区 | 依赖、配置、业务逻辑弱 | agent 可理解，但依赖 prompt/模型能力 |

这套取舍决定了整个架构的形状：因为"扫描很贵"，所以要**把每一次扫描变成可校验、可恢复、可批量化的资产**（契约 + ledger），因为"agent 需要可信环境"，所以要做**一整层安全加固**（凭据目录、权限收敛、容器沙箱）。

## 6. 关键实现入口

| 职责 | 位置 |
|------|------|
| CLI 命令定义与分发 | `sdk/typescript/src/cli.ts` |
| SDK 编排核心（run/preflight） | `sdk/typescript/src/api.ts` |
| 运行时准备（凭据/插件/工作台） | `sdk/typescript/src/runtime.ts` |
| Codex 配置合并与加固 | `sdk/typescript/src/config.ts` |
| 契约校验（schema/指纹/摘要） | `sdk/typescript/src/contract.ts` |
| 批量扫描编排 | `sdk/typescript/src/multiscan.ts` |
| 扫描结果对象 | `sdk/typescript/src/result.ts` |
| 插件清单 | `sdk/typescript/_bundled_plugin/.codex-plugin/plugin.json` |
| 扫描 skill 入口 | `sdk/typescript/_bundled_plugin/skills/security-scan/SKILL.md` |
| 工作台数据库 | `sdk/typescript/_bundled_plugin/scripts/workbench_db.py` |
| 封签 finalizer | `sdk/typescript/_bundled_plugin/scripts/finalize_scan_contract.py` |
| 容器化 | `Dockerfile`、`compose.yaml`、`docker/` |

## 小结

`codex-security` 的核心洞见是：**把"安全扫描"重新定义为"让一个有工具的 agent 走一遍分析师流程，并留下可校验的证据"**。TypeScript 层是编排与安全外壳，插件层是领域逻辑，Codex agent 是执行者。理解这个边界，后面每一章都是围绕它展开的：第 2 章讲外壳怎么被拉起，第 3~5 章讲一次扫描怎么被编排和推进，第 6~7 章讲怎么把扫描变成可批量、可校验的资产，第 8 章讲为什么它可以被信任。

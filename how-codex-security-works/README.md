# how-codex-security-works

本目录是对 [OpenAI Codex Security](https://github.com/openai/codex-security) 发布仓库的架构拆解。`codex-security` 不是一个"又一个扫描器"，而是一套"**用 Codex agent 本身当安全分析师**"的系统：一个很薄的 TypeScript CLI/SDK 外壳，负责把 OpenAI Codex agent 作为子进程拉起来，再给它装上一个捆绑的安全扫描插件（`_bundled_plugin`），让它照着插件里的 13 个 skill 和一套 SQLite 工作台（workbench）去完成威胁建模、发现、验证、攻击路径、修复建议的完整闭环，最后封签（seal）出可校验、可追溯的扫描产物。

> 写作风格参考 [how-claude-code-works](https://www.anthropic.com/engineering/claude-code-how-claude-code-works) 与同仓库的 [how-codex-works](./how-codex-works)：先说清楚"解决什么问题"，再讲"机制是什么"，最后才给出关键代码入口。

## 阅读顺序

| 章节 | 主题 | 解决的问题 |
|------|------|-----------|
| [第 1 章：总览](./01-overview.md) | 定位、核心问题、系统边界 | codex-security 是什么、它解决什么问题、三层结构如何划分 |
| [第 2 章：启动与命令分发](./02-startup-and-command-dispatch.md) | 从 `codex-security` 命令到子进程 Codex | CLI 命令如何路由、SDK 如何拉起 Codex agent、认证如何选择 |
| [第 3 章：标准扫描编排](./03-standard-scan-orchestration.md) | 一次 `scan` 的完整生命周期 | preflight → thread → agent 执行 → 产物收拢 → 结果校验 |
| [第 4 章：Deep Scan 与多 Agent](./04-deep-scan-and-multi-agent.md) | deep 模式如何并行与归并 | 多路 discovery worker、semantic reducer、集中验证 |
| [第 5 章：Workbench 与状态](./05-workbench-and-state.md) | 插件侧的 SQLite 工作台 | agent 通过 MCP 与 SQLite 交互、六阶段扫描推进 |
| [第 6 章：容器化批量扫描](./06-containerized-bulk-scan.md) | Docker + `bulk-scan` | 不可变 Git SHA、可恢复 ledger、容器安全基线 |
| [第 7 章：契约与产物](./07-contract-and-outputs.md) | sealed artifact 与指纹 | 扫描产物如何被封签、校验、可溯源 |
| [第 8 章：安全加固](./08-security-hardening.md) | 凭据、配置、沙箱 | 密钥如何不外泄、Codex 权限如何收敛、容器如何隔离 |
| [第 9 章：最小可复现实现](./09-minimal-rebuild.md) | 精简版 codex-security | 如果要重写一个最小版本，哪些组件不能省 |
| [第 10 章：安全沙箱（专题）](./10-security-sandbox.md) | 沙箱体系与纵深防御 | agent 的能力如何被逐层限制：配置权限 profile、能力 preflight、凭据隔离、容器内核沙箱 |

## 关键仓库信息

- **根目录**: `/Users/ahuamao/Documents/Codes/codex-security`
- **主要语言**: TypeScript（`sdk/typescript/`，CLI/SDK 外壳）+ Python（`_bundled_plugin/scripts/`，workbench 与 finalizer）+ Docker（`docker/`、`compose.yaml`）
- **NPM 包**: `@openai/codex-security`（v0.1.7，Apache-2.0），同时提供 `codex-security` CLI 二进制与 SDK
- **CLI 入口**: `sdk/typescript/bin/codex-security.mjs` → `dist/cli.js` 的 `main()`
- **SDK 核心类**: `src/api.ts` 的 `CodexSecurity`（编排）、`src/runtime.ts`（运行时/插件/工作台）、`src/contract.ts`（契约校验）、`src/multiscan.ts`（批量扫描）
- **插件根**: `sdk/typescript/_bundled_plugin/.codex-plugin/plugin.json`（name `codex-security`，v0.1.15）
- **关键依赖**: `@openai/codex` 0.144.6（codex 命令解析）、`@openai/codex-sdk` 0.144.6（启动 agent 线程）、`incur` 0.4.13（CLI 框架）、`ajv` 8.20.0（契约 schema 校验）、`extract-zip`/`fflate`（插件分发）、`papaparse`（批量清单）、`smol-toml`（Codex 配置）

## 约定

- 代码引用尽量使用**文件级路径**；只有对"容易误解或关键的实现细节"才会给出行号。
- "从代码结构推断"表示该结论没有直接运行证据，是基于源码结构的合理推断。
- 章节标题和正文使用中文。
- 本项目不是一个"命令行扫描器"，而是"CLI 外壳 + 捆绑 Codex 插件 + 容器化部署"的三层系统：阅读第 2、3 章时请记住，**真正的"扫描逻辑"不在 TypeScript 里，而在插件侧的 skill + Python 脚本里**，TypeScript 层负责编排、加固、校验产物。

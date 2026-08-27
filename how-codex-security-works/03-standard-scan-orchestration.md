# 第 3 章：标准扫描编排

## 本章导读

读完这一章，你会知道：

1. 一次标准 `scan` 从 preflight 到封签完成的完整生命周期；
2. 扫描主循环为什么发生在 Codex agent 里，以及它和外壳层的分工；
3. 六阶段扫描推进模型（preflight → threat_model → discovery → validation → attack_path → reporting）；
4. 插件侧的 MCP 工具如何让 agent 与 SQLite 工作台交互；
5. 产物是如何被"封签"出来、再由外壳层校验的。

## 1. 全景流程

```
外壳层（TypeScript）               Codex agent（子进程）              插件层（_bundled_plugin）
───────────────                  ─────────────────────              ─────────────────────
scanPreflightCodexConfig()
  —— 过滤密钥，选 model/effort
#prepareRuntime()
  —— 凭据目录 / 插件安装 / 工作台初始化
scanRuntimeCodexConfig()
  —— 写加固权限 profile
      │
      ▼
startThread + scanPrompt ──────►  agent 读取 security-scan SKILL.md
                                   │
      ◄── status/message/tool ──   │ 6 个阶段推进，每阶段：
      │  onProgress/onCost         │   · 通过 MCP 调用
      │                            │     open_codex_security_workspace
      │                            │     / start_codex_security_standard_scan
      │                            │   · 读/写 SQLite 工作台
      │                            │     (record_* / list_* 工具)
      │                            │   · 阶段结束时
      │                            │     update_codex_security_scan_progress
      │                            │
      │   turn 结束（agent 完成）    │  调用 complete_codex_security_scan
      │                            │  ──► finalize_scan_contract.py
      │                            │       封签 scan-manifest/findings/
      │                            │       coverage + report.md + sarif
      ▼                            │
loadContract() ◄───────────────────┘  scanDir 里的 sealed 产物
  —— Ajv schema + 指纹 + SHA-256 校验
collectResult() → ScanResult
```

## 2. Preflight：先清理，再放 agent

`CodexSecurity.run()` 一上来不是直接开跑，而是先做两件事：

1. **配置清理**（`scanPreflightCodexConfig()`，`api.ts`）：合并用户 `codexOverrides` 与默认配置，同时**过滤掉密钥类配置**（`mergedCodexConfig()` + 密钥过滤），保证 agent 看到的配置文件里没有凭据。
2. **模型选择**（`scanModelConfiguration()`）：从合并配置里挑出 `model` 与 `model_reasoning_effort`（默认 `gpt-5.6-sol` + `xhigh`），支持 profile 覆盖。

随后进入 `#prepareRuntime()`（细节见第 8 章）：隔离凭据目录 → 安装捆绑插件 → 初始化工作台。这是"**让 agent 能安全地跑起来**"的一次性准备。

## 3. 运行时配置：为扫描写一份加固的 Codex 配置

在真正启动 agent 前，外壳层会为这次扫描生成一份独立的 Codex 配置（`scanRuntimeCodexConfig()`），核心是一个名为 `codex_security_scan` 的权限 profile：

- `:root` 目录只读；
- `:workspace_roots` 可写（扫描期间要落临时产物）；
- 状态目录可写（workbench SQLite 需要写）。

配合 `approvalPolicy: "never"`，这意味着 agent 的能力被"**配置层面**"收敛到最小集：它能写的只有工作区与状态目录，其它地方只读。这是整个系统敢全自动跑的底气（第 8 章再展开）。

## 4. 主循环：agent 按 SKILL 推进六阶段

agent 被 `scanPrompt()` 指示"打开 security-scan skill 并按它执行"，并注入环境变量（状态目录、插件路径、知识库等）。真正的"扫描逻辑"全部来自 `skills/security-scan/SKILL.md`，它规定了六阶段流程（与 `workbench_constants.py` 中的阶段常量一致）：

| 阶段 | agent 做什么 | 工作台落点 |
|------|-------------|-----------|
| `preflight` | 走 config-preflight 引用，确认环境就绪 | 状态推进 |
| `threat_model` | 运行 `$threat-model` skill，写 `<context_dir>/threat_model.md` | 威胁模型 |
| `discovery` | 逐文件列出 review items，识别候选漏洞 | candidates |
| `validation` | 对候选逐个验证（compact 模式） | candidate_validations |
| `attack_path` | 对 reportable/deferred 候选做攻击路径与可达性分析 | attack_paths |
| `reporting` | 组装 findings/coverage，调 finalizer 封签 | draft → sealed |

关键约束（来自 SKILL.md）：
- **每阶段只跑一次**，不做"阶段队列 / 排名 / 反复大上下文"——标准扫描刻意保持单遍、低成本；
- **每个候选必须有恰好一条验证记录**（`record_codex_security_candidate_validations`）；
- 被拒 / 不适用 / 延后的候选要**映射进 coverage 的 disposition**，保证"每个文件都被交代过"；
- 产物的 `report.md` 和 SARIF **由 finalizer 生成，agent 不能手改**。

## 5. Agent 如何触碰世界：MCP 工具

agent 与 SQLite 工作台之间的桥梁是一个 MCP 服务器（`_bundled_plugin/mcp/server.mjs`，注册在 `.mcp.json` 的 `mcpServers.codex-security`，工具超时 86400s）。工具按用途分几组：

| 组 | 工具示例 | 作用 |
|----|---------|------|
| 会话建立 | `open_codex_security_workspace` / `start_codex_security_standard_scan` | 建立 scanId 与工作目录 |
| 进度推进 | `update_codex_security_scan_progress` | 阶段转移 + 返回不可变的 userContext |
| 上下文 | `get_codex_security_scan_context` / `update_codex_security_scan_context` | 读写 userContext（含用户提供的 URL，按不可信数据处理） |
| 发现 | `prepare_codex_security_review_items` / `list_codex_security_review_items` / `record_codex_security_discovery_candidates` | 逐页枚举文件、记录候选集 |
| 验证/攻击路径 | `list_codex_security_candidates` / `record_codex_security_candidate_validations` / `record_codex_security_candidate_attack_paths` | 读取候选、写回验证与攻击路径 |
| 封签 | `record_codex_security_scan_draft` / `complete_codex_security_scan` / `get_codex_security_completed_scan` | 提交草稿、触发封签、读取成品 |

这套 MCP 层的意义：**agent 的每一步状态变更都是可审计的、可恢复的**。扫描被中断后，重新打开工作台可以接着推进，而不是重来。

## 6. 封签（Sealing）：agent 结束，契约开始

agent 完成 reporting 后调用 `complete_codex_security_scan`，它触发插件侧的 finalizer（`scripts/finalize_scan_contract.py`）做**封签**——把 agent 的语义草稿变成不可篡改的规范产物：

```
finalize_scan_contract.py --scan-dir <scan_dir> --source-root <repo_root>
   │
   ├─ 生成 scan-manifest.json
   │    （scanId、producer、目标、范围、threatModel、
   │      artifacts[] 中每个产物的 path + sha256）
   ├─ 生成 findings.json（规范化 finding 结构）
   ├─ 生成 coverage.json（mode / surfaces / dispositions / exclusions）
   ├─ 生成 report.md（人读报告）
   └─ 生成 exports/results.sarif（机器消费）
```

封签的关键性质（第 7 章详述）：**manifest 里每个 artifact 都带 SHA-256 摘要，sealedAt 与 completedAt 对齐，身份指纹（`csf_`/`occ_`/`codex-security/v1:sha256:`）确定性派生**。也就是说，封签之后任何一份产物被改动，校验都会失败。

## 7. 校验与收拢：外壳层把关

agent 进程结束、turn 结果回来后，外壳层从 scanDir 读回三份 JSON，交给 `loadContract()`（`contract.ts`）做最后把关：

1. **Ajv2020 schema 校验**：三份文档必须符合插件 `schemas/` 里定义的 JSON Schema（模型类型来自 `models.ts`，由 schema 生成）；
2. **身份指纹校验**：producer/scanId 等指纹字段格式与一致性；
3. **摘要校验**：manifest 里声明的 artifact SHA-256 与实际文件一致；
4. **安全路径校验**：产物路径不能逃出 scanDir。

全部通过后 `collectResult()` 构造 `ScanResult`（`result.ts`），把 manifest / findings / coverage / reportPath / sarifPath / 成本一次性交给 CLI 渲染。**任何一环不通过，这次扫描都不会被当作可信产物交付。**

## 8. 失败模式与取舍

| 情况 | 行为 |
|------|------|
| preflight 配置不干净 | 在启动 agent 前失败，不烧 token |
| MCP 工具缺失（老版本 Codex） | SKILL 提供 fallback：直接 `python finalize_scan_contract.py` |
| agent 中途退出 | 事件循环收到结束，外壳层尝试读取已有产物；不足则报错 |
| 产物不满足契约 | `loadContract()` 抛错，扫描视为失败 |
| 桌面 app 与 headless 差异 | 桌面走 workspace 会话；headless（CLI/CI）走 `start_codex_security_standard_scan` |

设计理由：**扫描是昂贵的**。所以系统把"昂贵的 agent 部分"和"廉价但严格的产物校验部分"分开——agent 可以失败重跑，但一旦产出封签产物，必须是可校验的真理。这也解释了为什么 `approvalPolicy: never` + 配置级权限收敛能成立：产物可信性靠封签契约保证，而不是靠运行中的人工确认。

## 9. 关键实现入口

| 职责 | 位置 |
|------|------|
| run/preflight/事件循环 | `sdk/typescript/src/api.ts` |
| prompt 组装与 skill 选择 | `api.ts` 的 `scanPrompt()` / `skillNameFor()` |
| preflight/运行时配置加固 | `api.ts` 的 `scanPreflightCodexConfig()` / `scanRuntimeCodexConfig()` |
| 运行时准备 | `sdk/typescript/src/runtime.ts` |
| 契约校验 | `sdk/typescript/src/contract.ts` 的 `loadContract()` |
| 结果对象 | `sdk/typescript/src/result.ts` 的 `ScanResult` |
| 六阶段常量 | `_bundled_plugin/scripts/workbench_constants.py` |
| 标准扫描 SKILL | `_bundled_plugin/skills/security-scan/SKILL.md` |
| MCP 服务器 | `_bundled_plugin/mcp/server.mjs` + `.mcp.json` |
| 封签 finalizer | `_bundled_plugin/scripts/finalize_scan_contract.py` |

## 小结

标准扫描是"外壳编排 + agent 执行 + 契约收口"的一次完整协作：外壳负责把环境做干净、做安全，agent 按 skill 把六阶段跑完并落进 SQLite，finalizer 把语义结果封签成不可篡改的产物，外壳再用 schema+摘要校验把关。下一章看"贵"的代价怎么被 deep 模式的多 Agent 并行摊薄，以及为什么它需要完全不同的编排。

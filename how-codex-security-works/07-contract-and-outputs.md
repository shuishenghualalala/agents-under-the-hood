# 第 7 章：契约与产物

## 本章导读

读完这一章，你会知道：

1. 一次扫描产出哪些文件、它们各自承载什么；
2. "封签（seal）"到底封了什么，为什么产物不可篡改；
3. 契约如何被验证（schema + 指纹 + 摘要 + 安全路径）；
4. finding 的数据模型长什么样（severity / confidence / taxonomy / evidence）。

## 1. 产物清单

一次标准扫描在 scanDir 里留下（`result.ts` / finalizer 共同决定）：

| 文件 | 内容 | 角色 |
|------|------|------|
| `scan-manifest.json` | 扫描元数据 + **artifacts 摘要清单** | 契约的"信封" |
| `findings.json` | 规范化 findings 列表 | 机器可读结果 |
| `coverage.json` | 覆盖声明（mode / surfaces / dispositions） | 诚实性证明 |
| `report.md` | 人读报告 | 人类消费 |
| `exports/results.sarif` | SARIF 格式导出 | 集成到 CI/IDE |
| `hardening/hardening.md` | 加固建议（manifest 的 `hardening.portfolioPath` 指向） | 治理输出 |

三份 JSON 的结构类型定义在 `sdk/typescript/src/models.ts`——注意文件头注释："**Generated from the plugin JSON Schemas. Run `pnpm generate:models`.**"：**契约的唯一事实来源是插件 `schemas/` 里的 JSON Schema**，TypeScript 类型只是生成物。这保证了 TS 层校验与 Python 层产出共用同一套 schema。

## 2. 封签（Seal）：产物为什么不可篡改

封签发生在 `finalize_scan_contract.py`（第 3 章 6 节）。它的核心是让 manifest 变成"**证据链的锚点**"：

```
scan-manifest.json
  scan:
    id             : scanId
    producer       : { name, version }
    status         : "completed"
    startedAt / completedAt / sealedAt
    target         : { kind, targetId, displayName, remote?, revision?, ... }
    scope          : { includePaths, excludePaths, ... }
    threatModel    : { summary, assets, trustBoundaries, ... }
    hardening      : { portfolioPath: "hardening/hardening.md" }
    coverageRef    : "coverage.json"
    findingsRef    : "findings.json"
    artifacts[]    : [ { path, sha256, mediaType }, ... ]   ← 每个产物都有摘要
```

封签的三条硬约束（`loadContract()` 会逐条验证）：

1. **`sealedAt === completedAt`**：封签时刻必须等于扫描完成时刻——防止"事后补签/改签"；
2. **`artifacts[]` 里每个文件都有 SHA-256 摘要**：任何产物被改动，摘要即失配；
3. **身份指纹确定性派生**：`csf_`（候选/finding 指纹）、`occ_`（occurrence 指纹）、`codex-security/v1:sha256:`（finding 指纹算法）等字段由内容确定性生成，可跨系统复算比对。

结论：**封签之后，报告与数据的每个字节都被钉死**。这不只是"防篡改"，更是"防误改"——agent 不许手改 report.md/SARIF（SKILL 里明令），改动的唯一合法通道是重新封签。

## 3. 契约验证：loadContract()

外壳层用 `loadContract()`（`contract.ts`）对 scanDir 做验收，四层校验：

| 层 | 机制 | 失败后果 |
|----|------|---------|
| 1. Schema | Ajv2020 按插件 schemas 校验三份 JSON | 结构不合法 → 拒绝 |
| 2. 身份指纹 | 校验 `csf_`/`occ_`/`codex-security/v1:sha256:` 等指纹字段的格式与一致性 | 指纹异常 → 拒绝 |
| 3. 摘要 | manifest.artifacts 声明的 sha256 与实际文件比对 | 任一失配 → 拒绝 |
| 4. 安全路径 | 产物路径不得逃出 scanDir（防路径穿越） | 越界 → 拒绝 |

第 4 层（safe-path validation）容易被忽略但很关键：封签产物里带路径信息，如果路径字段能被注入 `../`，下游消费（SARIF 导入、报告渲染）就可能被路径穿越攻击。所以校验层把它当"外部输入"一样严格对待。

另有 **expectation validation**：`loadContract()` 也支持按调用方期望（如预期 producer 版本、预期 scanId）校验，用于 `scan` 之外消费既有产物的命令（`scans` / `findings` / `validate`）。

## 4. Finding 的数据模型

`findings.json` 里每个 finding（`models.ts` 的 `FindingsDocument["findings"]`）是一个信息完备的审计记录：

| 字段 | 含义 | 要点 |
|------|------|------|
| `identity.anchor` / `instance` | 问题锚点 | 定位到具体代码位置 |
| `fingerprints` | `{ algorithm: "codex-security/v1", primary }` | 跨重跑/跨系统稳定标识 |
| `severity` | `level`（critical/high/medium/low/informational）+ `score`/`vector`/`rationale` | 支持 CVSS 风格向量 |
| `confidence` | `level`（high/medium/low）+ `rationale` | agent 自评的可信度与理由 |
| `taxonomy` | `category` + `cwe[]` | 归类 |
| `locations[]` | `path` + `startLine`/`endLine` + `role` | 源码定位 |
| `codeEvidence[]` | `id`/`path`/`code`/`explanation` | **带证据代码片段** |
| `rootCause` | `summary` + `evidenceRefs`/`code` | 根因 |
| `remediation` | 修复建议 | 处置 |
| `validation` / `attackPath` | 验证结论 / 攻击路径（含 dataflow/reachability） | 自证过程 |
| `remediationTests` / `preventiveControls` | 验证/预防 | 治理 |
| `provenance.source` | 来源 | 溯源 |

这个模型的用意：**finding 不是一行告警，而是"定位 + 证据 + 自证 + 修复"的完整档案**——这正是第 1 章说的"agent 自证"在数据层的落地。`coverage.json` 则负责"诚实性"：每个 surface 的 disposition（`reported`/`no_issue_found`/`rejected`/`not_applicable`/`needs_follow_up`）、explicitExclusions、deferred，全部显式记录——**没扫的地方明确说没扫**，而不是默默略过。

## 5. 消费这些产物

封签产物是系统内外的共同接口：

| 消费者 | 读什么 | 目的 |
|--------|--------|------|
| 外壳层 `loadContract()` | 三份 JSON | 验收/校验 |
| CLI `scans` / `findings` | manifest / findings | 查询、对比 |
| CLI `validate` / `patch` | findings + 代码 | agent 驱动的二次处置 |
| 外部 CI | `exports/results.sarif` | 门禁、IDE 集成 |
| 人 | `report.md` / `hardening/hardening.md` | 阅读、治理 |
| `bulk-scan` | 四件套齐全性 | 判断任务是否完成（`hasArtifacts`） |

## 6. 取舍与设计理由

| 设计 | 理由 |
|------|------|
| Schema 为唯一事实来源，TS 类型是生成物 | 杜绝"Python 产出一套、TS 校验另一套"的漂移 |
| 摘要 + sealedAt 双重钉死 | 单靠 schema 挡不住"合法结构但被改动"；摘要把内容也钉死 |
| 指纹确定性派生 | 让同一漏洞在不同重跑/不同工具间可比对（去重、增量扫描的基础） |
| 报告/SARIF 由 finalizer 生成，agent 禁改 | 机器产物与人类产物同源，避免 agent 的 markdown 自由发挥污染可解析数据 |
| coverage 显式记录 disposition | "没扫就是没扫"——审计工具最怕虚假的"全覆盖"声明 |

## 7. 关键实现入口

| 职责 | 位置 |
|------|------|
| 契约数据结构 | `sdk/typescript/src/models.ts`（由 `scripts/generate-models.cjs` 生成） |
| 契约校验 | `sdk/typescript/src/contract.ts` 的 `loadContract()` |
| 结果对象/产物路径 | `sdk/typescript/src/result.ts` 的 `ScanResult` |
| 封签 finalizer | `_bundled_plugin/scripts/finalize_scan_contract.py` |
| JSON Schema 来源 | `_bundled_plugin/schemas/` |
| 生成模型脚本 | `sdk/typescript/scripts/generate-models.cjs` |

## 小结

契约层把"agent 的语义产出"变成"可校验、不可篡改、可追溯的审计资产"：schema 统一事实来源，sealedAt + SHA-256 + 确定性指纹三重钉死，loadContract 四层验收把关。finding 的完整档案模型 + coverage 的诚实声明，让每次扫描既是结果又是证据。下一章看这些可信保证的另一半——运行时与配置层面的安全加固。

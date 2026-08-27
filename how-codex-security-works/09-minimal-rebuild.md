# 第 9 章：最小可复现实现

## 本章导读

读完这一章，你会知道：

1. 如果要从零重写一个最小可用的 codex-security，哪些组件**不能省**；
2. 哪些层可以砍掉、砍掉后失去什么；
3. 推荐的实现顺序与各阶段的验证方式；
4. 为什么"封签契约"和"凭据隔离"是底线中的底线。

## 1. 最小系统的本质

前八章讲了一个三层系统。最小可复现版的本质是把它压缩到一句话：**"用 @openai/codex-sdk 启动一个 Codex agent，让它按一份 security-scan skill 跑六阶段，把结果封签成可校验产物。"**

最小系统只需要两条链：

```
链 A（执行）：CLI → CodexSecurity.run → 运行时准备 → startThread(approvalPolicy:never)
                → scanPrompt → agent 执行 security-scan skill → turn 结束
链 B（收口）：finalizer 封签 → loadContract 校验 → ScanResult 交给调用方
```

## 2. 不能省的部分（底线组件）

| 组件 | 为什么不能省 | 最小形态 |
|------|-------------|---------|
| **security-scan skill** | 没有它，agent 不知道"怎么做安全审计"——扫描逻辑全在这 | 一份 SKILL.md，定义六阶段与产物要求 |
| **封签契约（finalizer + schema + 摘要）** | 没有它，产出无法被信任——agent 的 markdown 自由发挥会污染可解析数据 | finalize + 三份 JSON + SHA-256 摘要 + sealedAt==completedAt |
| **凭据目录隔离** | 没有它，agent 会读到宿主全局凭据——这是安全事故不是缺陷 | `requireSecureCredentialHome` 的精简版（0700 + 拒符号链接） |
| **配置收敛（权限 profile + `approvalPolicy:never`）** | 没有它，全自动扫描=让 agent 拥有整个文件系统 | 一份 `codex_security_scan` profile：root 只读、workspace 可写 |
| **工作台（SQLite + MCP 工具）** | 可以极简，但不能没有：agent 需要"可审计的状态"来推进发现/验证/覆盖 | 一张 DB + 一组 record/list MCP 工具 |
| **模型选择 + 认证** | 没有它连 agent 都跑不起来 | `scanModelConfiguration` + `importAmbientAuth` |

## 3. 可以砍掉的部分（以及代价）

| 可砍 | 砍掉后失去 | 什么时候可以砍 |
|------|-----------|---------------|
| `bulk-scan` + 容器化（第 6 章） | 多仓库批量、内核级沙箱 | 只扫单仓库、信任本地环境时 |
| deep 模式（第 4 章） | 大仓库覆盖深度 | 仓库足够小、标准扫描够用时 |
| 进度 UI / 成本统计 / 失败分类 | 终端体验与可读诊断 | 作为 SDK 嵌入使用、只取 `ScanResult` 时 |
| `scans`/`findings`/`export`/`validate`/`patch` 命令 | 结果查询与二次处置 | 每次只跑 scan 并自己消费产物时 |
| 外部提供者（openrouter/fireworks） | 第三方模型后端 | 只用 OpenAI 官方端点时 |
| 13 个 skill 的完整族 | 治理/处置闭环 | 只做"发现+验证"不接治理时 |

关键判断：**上面这些全部可以砍，但第 2 节那六个不行。** 砍掉它们，你就不再是"codex-security 的迷你版"，而是"另一个用 LLM 扫代码的玩具"。

## 4. 推荐的实现顺序

按"先能跑通最贵的一环，再补外围"的顺序：

```
Step 1  最小外壳：bin → main() → CodexSecurity.run
        · @openai/codex-sdk 启动 agent 线程，approvalPolicy:"never"
        · scanPrompt 只写一行："按 security-scan skill 扫描 <target>"
        ✅ 验证：能起 agent、能收到消息事件
        （此时还没有任何插件——agent 只是裸跑）

Step 2  插件最小集：security-scan SKILL + workbench_db.py + MCP server
        · 让 agent 能 open workspace、推进阶段、record candidates
        ✅ 验证：手动调 MCP 工具能读写 SQLite

Step 3  封签链：finalize_scan_contract.py + schemas + loadContract
        · 把 agent 的草稿封签成三份 JSON + report + sarif
        · 外壳层 loadContract 校验通过才算成功
        ✅ 验证：跑通一次完整扫描，篡改 report.md 后校验失败

Step 4  安全底线：凭据目录隔离 + 权限 profile 收敛
        · requireSecureCredentialHome 精简版
        · scanRuntimeCodexConfig 写 codex_security_scan profile
        ✅ 验证：agent 尝试写 root 外目录被拒；凭据目录不可被 agent 读取

Step 5  外围：成本统计 → CLI 子命令（scans/findings）→ deep → bulk/容器
        · 每一步都可独立交付与测试
```

每一步的验证都有一条硬标准：**产物必须通过契约校验**（Step 3 起）。这正是整个系统"测试先行"的抓手——不是"看起来扫出东西了"，而是"封签 + 校验 = 成功"。

## 5. 重写时的三个最容易踩的坑

1. **让 agent 手写 report.md/SARIF**：必须由 finalizer 统一生成，否则可解析数据被 markdown 自由发挥污染，下游（SARIF 导入、去重）全乱。
2. **信任仓库目录**：检出/解压/路径都要当不可信输入处理——hooks、符号链接、`..`、zip-slip、PATH 劫持，一个都不能省（第 8 章）。
3. **把状态放内存或 JSON 文件**：丢失可恢复性与查询能力。哪怕极简，也要一个 SQLite + 阶段记录，否则 deep 与 bulk 都无从谈起。

## 6. 底线总结

最小可复现版的验收标准可以浓缩成一条：**"在无人值守下，把任意不可信仓库喂给 agent，能自动得到一份通过契约校验、且 agent 无法越权读取宿主凭据的扫描报告。"** 任何能达成这条的实现都是合格的最小版；任何省掉其中一环的实现都不算。

## 7. 关键实现入口（重写时的参考锚点）

| 最小组件 | 参考原实现 |
|---------|-----------|
| 外壳骨架 | `sdk/typescript/src/api.ts`（run / scanPrompt / collectResult） |
| 运行时准备 | `sdk/typescript/src/runtime.ts`（credential home / plugin / workbench） |
| 配置收敛 | `sdk/typescript/src/config.ts` + `api.ts` 的 `scanRuntimeCodexConfig()` |
| 契约 | `sdk/typescript/src/contract.ts` + `_bundled_plugin/scripts/finalize_scan_contract.py` |
| 工作台 | `_bundled_plugin/scripts/workbench_db.py` + `mcp/server.mjs` |
| 扫描逻辑 | `_bundled_plugin/skills/security-scan/SKILL.md` |

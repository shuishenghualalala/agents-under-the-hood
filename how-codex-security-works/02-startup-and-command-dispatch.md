# 第 2 章：启动与命令分发

## 本章导读

读完这一章，你会知道：

1. 一条 `codex-security` 命令从进程入口到业务处理的完整路径；
2. CLI 上暴露了哪些命令、各自通向哪里；
3. 最核心的 `scan` 命令如何把请求交给 SDK、再如何拉起子进程 Codex agent；
4. 认证（auth）与模型提供者是如何被选择的；
5. 外壳层的健壮性设计：进度监控、成本统计、错误脱敏与失败分类。

## 1. 进程入口与解析

进程入口是 `sdk/typescript/bin/codex-security.mjs`，它只做两件事：定位入口模块、把控制权交给 `dist/cli.js` 的 `main()`。真正的命令行解析用的是 **`incur`** CLI 框架（`Cli.create("codex-security")`），每个子命令都带参数 schema（z-validate 风格），非法参数会在进入业务逻辑前被拒掉。

```
$ codex-security scan --target ./repo --mode deep
        │
        ▼
bin/codex-security.mjs  ──►  dist/cli.js main()
                            incur Cli.create("codex-security")
                            └─ 参数 schema 校验
                            └─ 分发到对应 handler
```

## 2. 命令总览

`cli.ts` 中定义了一组命令，覆盖"扫描 / 消费结果 / 管理运行时"三类职责：

| 命令 | 职责 | 核心实现 |
|------|------|---------|
| `scan` | 对 target 执行一次扫描（标准/deep/diff） | `runScan()` → `CodexSecurity.run()` |
| `install-hook` | 安装 git hook（如 pre-push）让扫描自动触发 | runtime/CLI 辅助 |
| `scans` | 列出/比较已完成的扫描（`scans compare` 语义） | 读 workbench/产物 |
| `findings` | 查看/筛选某次扫描的 findings | 读产物 |
| `bulk-scan` | 用 CSV 清单批量扫描多个仓库 | `runMultiscan()` |
| `export` | 导出产物（如 SARIF） | 产物目录 |
| `validate` | 校验产物契约（与 `runSkill` 的 patch 流程相关） | `loadContract()` |
| `patch` | 对 finding 生成/应用修复（经 `codex exec`） | `runSkill()` |
| `login` / `logout` | 管理 Codex 凭据 | `importAmbientAuth()` 等 |
| `info` | 打印环境/运行时信息 | runtime 探测 |

> `validate` 与 `patch` 命令都走 `runSkill()`：它通过 `codex exec` 让 agent 用插件里的校验/修复 skill 去处理已有产物——也就是说，**对扫描结果的二次处理（验证、修复）本身也是 agent 驱动的**，与扫描主流程同构。

## 3. scan 命令的完整路径

`scan` 是主路径，它把"CLI 参数"翻译成"一次 Codex agent 运行"。流程如下：

```
runScan()  (cli.ts)
   │ 1. 解析 target / mode / scope / outputDir / model 等参数
   │ 2. 读取 codexOverrides，构造 CodexSecurityConfig
   ▼
CodexSecurity.run(target, { mode, outputDir, signal, ... })  (api.ts)
   │ 3. scanModelConfiguration() —— 从 merged Codex 配置选 model/effort
   │ 4. #prepareRuntime() —— 一次性运行时准备
   ▼
#prepareRuntime()  (api.ts → runtime.ts)
   │  a. codexSecurityStateDirectory() —— 确定状态目录
   │  b. prepareCodexSecurityCredentialHome() + requireSecureCredentialHome()
   │     —— 创建/校验隔离凭据目录（0700、拒符号链接…）
   │  c. bootstrapPlugin() —— 建 marketplace、安装捆绑插件、指纹校验
   │  d. runWorkbench() —— 初始化 SQLite 工作台 schema
   ▼
runScanEvents()  (api.ts)
   │ 5. scanPrompt() —— 组装给 agent 的 prompt（含 skill 选择、env 注入）
   │ 6. scanRuntimeCodexConfig() —— 写加固后的 Codex 配置（权限收敛）
   │ 7. new Codex(provider).startThread({ workingDirectory,
   │       skipGitRepoCheck, approvalPolicy: "never" })
   │ 8. thread.runStreamed() —— 启动 Codex agent 子进程，逐事件消费
   ▼
事件流（status / message / tool / turn 结束…）
   │ 9. 每个事件经 observer 派发：onProgress / onCost / onWorkerStatus
   │10. 最终 collectResult() —— 从 scanDir 读回封签产物，构造 ScanResult
   ▼
ScanResult（含 manifest / findings / coverage / reportPath / sarifPath / cost）
```

几个值得展开的点：

### 3.1 skill 的选择（`skillNameFor()`）

SDK 根据 `target` 的形状挑选扫描 skill，这是"同一套编排、多种扫描语义"的关键开关：

| target 形状 | 选择 |
|------------|------|
| 仓库 / 路径（repository、paths） | `security-scan` |
| deep 模式 | `deep-security-scan` |
| refs / working_tree 目标（diff） | `security-diff-scan` |

### 3.2 approvalPolicy: "never"

`startThread` 的 `approvalPolicy: "never"` 意味着**不向用户弹任何审批**——扫描要全自动跑完。这一点极其重要：它意味着安全边界必须靠后面的"权限收敛 + 沙箱"来兜底（见第 8 章），而不是靠"运行时问一下用户"。

### 3.3 事件消费与三类 observer

`runScanEvents()` 是个事件循环，消费 `runStreamed()` 吐出的每个事件。外壳层通过三个观察者把"agent 内部发生了什么"暴露出去：

- `onProgress`：扫描阶段推进（preflight / threat_model / discovery / validation / attack_path / reporting）；
- `onCost`：token 与金额累计；
- `onWorkerStatus`：deep 模式下各 discovery worker 的状态。

CLI 侧由 `Progress` / `ScanDashboard` 把这些渲染成终端进度 UI。

## 4. 认证与模型提供者

扫描的 agent 需要一个能调用的模型后端。外壳层不自己实现认证，而是复用 Codex 生态的机制：

- **auth 模式**：支持 ChatGPT 登录态 / API key 两种，通过 `importAmbientAuth()` 从宿主环境导入（不自己管理密钥）；
- **模型提供者**：`config.ts` 定义 `EXTERNAL_CODEX_PROVIDERS`（openrouter / fireworks），即除了 OpenAI 官方端点外，还能把 agent 指向兼容的第三方推理端点（读各自的环境变量如 `OPENROUTER_API_KEY`）；
- **隔离的凭据目录**：`prepareCodexSecurityCredentialHome()` 为每次扫描准备一个独立凭据目录，避免 agent 读到用户的全局凭据（细节见第 8 章）。

## 5. 健壮性设计

外壳层对"agent 可能失败"这件事做了系统性处理：

| 场景 | 机制 |
|------|------|
| 用户 Ctrl-C | `signal`（AbortSignal）贯穿 run → thread，可干净取消 |
| 网络/后端失败 | `classifyConnectionFailure()` 对错误分类，给出可读诊断 |
| 敏感信息泄漏进日志 | `sanitizeDiagnosticValue()` 对诊断值脱敏 |
| 扫描中断后的结果 | `collectResult()` 对 scanDir 做容错读取（如 sarif 缺失时返回 null） |
| 成本失控 | `estimateScanCost()` 基于 usage 估算，随 `onCost` 持续上报 |

这些不是边缘功能，而是"LLM 扫描必然昂贵且可能失败"这一前提的直接推论：一次失败的扫描如果不能干净取消、不能给出可读原因、不能把 token 成本说清楚，用户就不会在 CI 里用它。

## 6. 关键实现入口

| 职责 | 位置 |
|------|------|
| 进程入口 | `sdk/typescript/bin/codex-security.mjs` |
| CLI 框架与命令 handler | `sdk/typescript/src/cli.ts` |
| SDK 编排（run / preflight / 事件循环） | `sdk/typescript/src/api.ts` |
| skill 选择 | `api.ts` 的 `skillNameFor()` |
| 运行时准备（凭据/插件/工作台） | `sdk/typescript/src/runtime.ts` |
| 认证导入 | `runtime.ts` 的 `importAmbientAuth()` |
| 模型提供者表与配置合并 | `sdk/typescript/src/config.ts` |
| 成本估算 | `sdk/typescript/src/cost.ts` |
| 失败分类 / 错误脱敏 | `sdk/typescript/src/errors.ts`（`classifyConnectionFailure` 等） |

## 小结

外壳层的设计哲学是"**编排与安全边界**"：它不关心"怎么发现漏洞"（那是第 3~5 章 agent 的事），只负责把参数变成一次可信的 agent 运行，并在运行期间监控、脱敏、汇总，运行后校验产物。下一章看这次运行内部的标准扫描生命周期。

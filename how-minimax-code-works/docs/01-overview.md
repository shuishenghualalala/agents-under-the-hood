# 01. 整体架构


> **文档同步跟踪**
> - 最后同步代码：`minimax-code` commit `ff415b1`（2026-09-21）
> - 同步方式：基于该文档撰写时的源码路径与提交记录梳理

如果只看目录，MiniMax Code 像一个常见的 pnpm monorepo：15 个 packages、一个 third_party、一个 tui。但从运行时看，它是一个**三层系统**：产品层把运行时翻译成三种表面，运行时层拥有全部产品语义，而最核心的 agent 循环和沙箱根本不在第一方代码里——在 third_party 的两个 fork 里。

还有两个容易让新读者迷路的事实，本章先立起来：

- **仓库不是原生开源项目**，是 MiniMax 内部 monorepo 的「公开投影」，每个发布文件登记在 `release/public-source.json` 清单里，上游变更走逐文件审查 + 三方合并（见 `docs/source-sync.md`）。
- **有两套 local-runtime 并存**：V1（`packages/local-runtime`）不再是执行引擎，但作为宿主设施库被 V2 复用——权限引擎、skills 扫描、模型解析器都在它那里。

## 1. 产品定位

MiniMax Code 是终端编码 Agent：在项目目录里启动，用自然语言描述任务，它读代码、改文件、跑命令、跑测试。三个入口：交互 TUI（`mcode`）、无头模式（`mcode exec`，给脚本和 CI 用）、编辑器协议（`mcode acp`）。

商业形态是「托管账号 + 自带模型」双轨：登录 MiniMax 账号走 Token Plan（OAuth 设备授权），或 `mcode provider add` 接任意 OpenAI / Anthropic 兼容端点（BYOK）。默认模型 `minimax/MiniMax-M3`（512K 上下文，可选 1M）。

这类系统的工程难点不是「调一次模型」，而是把一次对话做成**可持久化、可恢复、可审计、可扩展**的长事务。理解了这一点，分层就顺理成章。

## 2. 三层架构

仓库自己的架构文档（`docs/architecture.md`）用一句话概括请求流向：`TUI / exec / ACP → CliService → local Applications → Session / Turn / Agent services → Pi / model providers / local tools`。按这条线切三层：

```text
┌─────────────────────────────────────────────────────────────┐
│ 产品层  packages/tui（约 10.5 万行）                          │
│  · CLI 解析与子命令：src/cli/（program.ts 是分发表）          │
│  · 自绘终端 UI：src/tui/（非 React/Ink，见第 13 章）          │
│  · headless 管线：src/headless/；ACP 适配：src/acp/           │
├─────────────────────────────────────────────────────────────┤
│ 运行时层                                                       │
│  · packages/local-runtime-v2（约 22 万行）                    │
│    - 进程内门面：src/local/cli-service.ts                     │
│    - 应用用例：src/application/（conversation/session/queue）  │
│    - Owner 服务图：src/service/（turn-system/session-system/  │
│      plan/plugin-system/mcp/sandbox/…），组装根 src/services.ts│
│  · packages/agent-core：PiTurnRunner + EventBridge            │
│  · packages/agent-runtime：扩展 SPI + assembleTurn            │
│  · packages/agent-tools：全部工具定义与实现                    │
├─────────────────────────────────────────────────────────────┤
│ 基座层  third_party（fork，非第一方）                          │
│  · pi-mono v0.79.1：agent 主循环 while、pi-ai 流式客户端、     │
│    bash/read/edit/write 工具引擎                              │
│  · sandbox-runtime v0.0.74（Anthropic 实验项目）：OS 级命令    │
│    沙箱 + 网络代理 + 凭据掩码                                  │
└─────────────────────────────────────────────────────────────┘
```

两条快速归位判断：

- **改 UI 不改行为**的代码在产品层；**模型和工具之间**的代码在运行时层；**循环的 while 和模型 HTTP 客户端**在基座层。
- `packages/local-runtime`（V1，约 13.8 万行）夹在中间：权限规则存储（`src/permissions/rules.ts`）、skills 根目录（`src/skills/roots.ts`）、模型解析器（`src/runtime/model-resolver.ts`）都是它的，V2 通过 `compat/v1` 适配层调用。

体感数字：local-runtime-v2 约 22.2 万行、local-runtime 约 13.8 万、tui 约 10.5 万、agent-tools 约 2.3 万，其余各包都在 1 万行以下。

## 3. 仓库性质：内部 monorepo 的公开投影

读这个仓库前必须知道这件事，否则很多工程决策会显得奇怪。证据链：

- 每个发布文件登记在 `release/public-source.json`，新增文件不加清单会被 `pnpm check:source` 拦下；源码基线钉在 `release/extraction.json` 的共享源 revision 上。
- 仓库自述（`docs/open-source-status.md`）：公开源码覆盖终端 TUI、headless CLI 与 ACP，**不含** Desktop 应用源码、内部生成的 IDL、云执行器独占实现。
- 内部 Git 历史不进来（`AGENTS.md` 明确禁止 merge 内部历史）；首个公开快照 2026-09-18 导入（commit `c59cf537`）。

这解释了两件「怪事」：为什么 `packages/` 里会出现 `agent-modules` 二级目录（内部包结构的原样投影），以及为什么代码里偶尔有「Decision v3/v5」这类内部评审编号的注释残迹。

## 4. 双运行时：V1 提供设施，V2 拥有执行

| | V1（`packages/local-runtime`） | V2（`packages/local-runtime-v2`） |
|---|---|---|
| 身份 | 旧执行引擎，现为宿主设施库 | 唯一的执行与持久化所有者 |
| 拥有 | 权限引擎/规则存储、skills 根目录与注册表、模型解析器、memory、问卷、web 抓取、旧会话读取器 | 会话/Turn/队列服务图、SQLite+JSONL 持久化、插件系统、MCP、沙箱服务、Plan/Goal、cron |
| 启动时 | 作为兼容宿主被创建，供 V2 调用 | 在 V1 之上组装全部 Owner 服务；`CliService` 只从 V2 服务图构造 |

启动顺序本身就是证据（`packages/local-runtime-v2/src/runtime.ts` 的 `createLocalRuntimeHostV2`）：数据目录迁移 → 打开 SQLite → 组装 V2 Agent Owner → **仍然创建 V1 宿主**（拿它的设施）→ 初始化 V2 服务图。TUI 侧的 legacy 回退开关已被禁用（`packages/tui/src/runtime/embedded-host.ts:47-50`）——没有「切回 V1 执行」的路径，只有「借用 V1 设施」的路径。

为什么不清掉 V1？从代码结构推断：权限规则存储、skills 多源扫描这类设施与执行引擎解耦良好，搬家收益低、三方合并冲突成本高，所以选了「执行先切走、设施后慢慢搬」的渐进路线。

## 5. 基座策略：循环和沙箱都是 fork

**agent 主循环整体来自 pi-mono**（`@earendil-works/pi-*`，v0.79.1，commit `28df940`，2026-06-16 引入）。第一方对它的关系是「vendored fork + 台账化修改」：基线导入不带修改，所有改动记录在 `third_party/pi-mono/MINIMAX_CHANGES.md`（约 28 条，每条有日期、原因、影响包、验证方式）。台账里能读出真实痛点：

- pi-ai 层修过多家 provider 的 `system`/`developer` 角色兼容（Mistral、SiliconFlow、DashScope、Kimi Coding）、Anthropic thinking 签名保留、SSE 超时 10s→30s——「统一 LLM 客户端」类库的典型维护负担；
- 工具引擎层有 CJK 安全的模糊匹配、bash 终止信号改为 SIGTERM→宽限→SIGKILL（让 trap 能执行）这类细节修正；
- 一批「接缝」式改动：暴露钩子、注入宿主 fetch（egress 控制）、可观测回调——**不改行为，只开窗户**，这是把开源库产品化的标准手法。

**命令沙箱来自 Anthropic 实验项目 sandbox-runtime**（`anthropic-experimental/sandbox-runtime` tag `v0.0.74`，本地版本 `0.0.74-mcode.2`，出处见 `third_party/sandbox-runtime/upstream.json`）。提供 macOS Seatbelt（`/usr/bin/sandbox-exec`）、Linux bwrap+seccomp、Windows 原生助手，外加本地 HTTP/SOCKS/MITM 代理做网络策略和凭据掩码。第一方改动集中在独立 unlink 作用域、每次调用净化环境变量等（`third_party/sandbox-runtime/README.md`）。重要事实：**V2 只注册了 macOS 后端**（`packages/local-runtime-v2/src/service/sandbox/initialize.ts:35-40`），Linux/Windows 上选择器直接抛 `SANDBOX_UNSUPPORTED_PLATFORM`——库支持多平台，产品只开了 macOS（详见第 8 章）。

为什么 fork 而不是依赖？从台账和 workspace 配置推断：产品要对接缝有完全控制权（安全审查、事件桥都要往循环里插桩），且公开投影要求依赖可审计，vendored fork 同时满足两者。

## 6. 包地图

15 个第一方包按职责归组：

| 职责组 | 包 | 一句话职责 |
|---|---|---|
| 产品表面 | `tui` | 三种入口、CLI 子命令、自绘终端 UI、宿主集成 |
| 进程内门面与协议 | `local-runtime-v2`（local/application 部分）、`protocol` | 门面方法、CLI 数据视图（`src/local.ts`）、Agent 配置与事件（`src/runtime.ts`） |
| Turn 与 Agent 执行 | `local-runtime-v2`（service 部分）、`agent-core`、`agent-runtime` | Owner 服务图；Turn 装配器+事件桥；扩展点 SPI |
| 工具 | `agent-tools` | 全部模型可见工具的定义与实现，含浏览器套件与托管媒体工具 |
| 宿主设施（V1） | `local-runtime` | 权限引擎与规则、skills 扫描、模型解析、memory、web 抓取 |
| 组装件 | `agent-modules`（二级包集合） | 权限检查器、MCP 连接池/命名注册、插件钩子等可复用模块 |
| 账号与凭据 | `oauth-core`、`oauth-lease-protocol`、`mcode-tools-host` | OAuth 设备流与令牌存储；子进程短时令牌租约协议与 broker |
| 配置 | `config` | 数据目录/profile、config.yaml 解析、模型目录、feature flags |
| 其他 | `browser-core`、`shared`、`agent-extension` | CDP 浏览器自动化核心；跨包共享工具；领域模块到扩展 SPI 的适配层 |

## 7. 读整体架构代码的入口

| 主题 | 入口 |
|---|---|
| 官方架构边界说明 | `docs/architecture.md` |
| 公开投影清单与源码基线 | `release/public-source.json`、`release/extraction.json` |
| V2 宿主启动（含 V1 兼容宿主） | `packages/local-runtime-v2/src/runtime.ts` |
| V2 服务图组装根 | `packages/local-runtime-v2/src/services.ts`（`createRuntimeServices`） |
| 进程内门面 | `packages/local-runtime-v2/src/local/cli-service.ts:107` |
| pi-mono fork 台账 | `third_party/pi-mono/MINIMAX_CHANGES.md` |
| 沙箱 fork 出处 | `third_party/sandbox-runtime/upstream.json`、`README.md` |
| 版本与证据基线 | `docs/open-source-status.md` |

## 8. 小结

MiniMax Code 的架构可以概括为一句话：**产品层翻译表面、运行时层拥有语义、基座层跑循环——循环和沙箱都是台账化维护的 fork，产品语义全部在自己手里。**

判断任何代码的归属，先问它在「翻译表面」（`packages/tui`）、「拥有产品语义」（`local-runtime-v2` + `agent-core/tools`）还是「跑循环」（`third_party/pi-mono`）。V1 是退休返聘的设施库，不是第二引擎。仓库本身是内部 monorepo 的公开投影，这解释了它的清单机制与保守布局。

从读源码的角度，建议按这个顺序继续下钻：

```text
packages/tui/src/index.ts                              # 进程入口（第 2 章）
packages/local-runtime-v2/src/local/cli-service.ts     # 进程内门面
packages/local-runtime-v2/src/services.ts              # 服务图接线全景
packages/local-runtime-v2/src/service/turn-system/     # Turn 与 AgentHost（第 3 章）
third_party/pi-mono/packages/agent/src/agent-loop.ts   # 循环本体 runLoop（第 3 章）
```

下一章从最外层开始：敲下 `mcode` 之后进程里按什么顺序发生什么——见 [02. 启动与命令分发](02-startup-and-dispatch.md)。

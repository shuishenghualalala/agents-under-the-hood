# 专题：Codex 的浏览器自动化插件（Codex Browser）——让 Agent 真正"操控"浏览器

> 本章导读：前几章讲的都是 Codex CLI 核心在"本地终端"里如何运行 shell、写文件、调用工具。但当模型需要打开网页、填表单、点按钮、登录、截图、上传文件时，光靠 shell 是不够的——它需要一个"看得见、点得着"的浏览器。本章拆解 Codex 的 **Browser Use 浏览器自动化插件**（`control-in-app-browser`）：它如何发现并连接本地浏览器、如何通过统一 API 控制标签页、又如何保证安全（登录凭据不落地、高风险操作先确认）。

## 1. 本章要解决的问题

浏览器自动化插件要回答四个核心问题：

1. **怎么连上浏览器？** Agent 运行在 Node REPL 沙盒里，本地浏览器（Chrome/Edge）和应用内浏览器（IAB）是三种完全不同的后端，如何统一发现、统一连接。
2. **模型怎么控制页面？** 点击、输入、滚动、截图、等待、读取状态，这些操作如何抽象成一套对模型友好的 API。
3. **怎么保证安全？** 登录密码、验证码、付款、删数据——哪些操作必须停下来问用户，哪些可以放行。
4. **怎么收拾残局？** 一轮对话结束后，哪些标签页该关、哪些该保留给下一轮。

## 2. 插件整体结构

插件是一个发布包，包含四大部分：

```
codex-browser/
├── assets/                          # 浏览器图标资源
├── docs/                            # 文档库（按需注入给模型）
│   ├── api.json                     # 完整 API 类型清单
│   ├── documents.json               # 文档注入策略（何时注入哪篇）
│   ├── *.md                         # 引导文档（安全/Playwright/确认策略等）
│   └── capabilities/                # 能力扩展文档
├── scripts/                         # 运行时脚本
│   ├── browser-client.mjs           # ★ 核心运行时入口（单文件打包）
│   ├── extension-ids.json           # 浏览器扩展 ID 与诊断配置
│   ├── chromium-browser-diagnostics.mjs  # 跨平台诊断共享库
│   └── *.js                         # 6 个诊断/运维脚本
└── skills/
    └── control-in-app-browser/SKILL.md  # skill 主指令（触发与引导逻辑）
```

## 3. 架构全景：三层模型

整个系统是一个 **Codex ↔ 浏览器后端 ↔ CDP 控制页面** 的三层架构。

```
┌──────────────────────────────────────────────┐
│  Agent 层：node_repl / agent.browsers.* API    │
│  模型通过 JS 调用，统一门面屏蔽后端差异          │
├──────────────────────────────────────────────┤
│  连接层：browser-client.mjs（浏览器池）         │
│  命名管道发现后端、session request 分发命令      │
├──────────────────────────────────────────────┤
│  后端层：extension / iab / cdp                 │
│  最终通过 CDP 协议控制浏览器页面                │
└──────────────────────────────────────────────┘
```

### 3.1 三种浏览器后端

| 类型 | 含义 | 说明 |
|------|------|------|
| `extension` | ChatGPT 浏览器扩展（Chrome/Edge） | 通过扩展 + native messaging 通道 |
| `iab` | 应用内浏览器（内嵌） | 集成在 IDE/客户端里的浏览器 |
| `cdp` | 原生 Chrome DevTools 协议 | 直连调试端口 |

### 3.2 运行时初始化（`setupBrowserRuntime`）

`browser-client.mjs` 是单文件打包产物（约 3228 行 / 1MB，Playwright、markdown-it 等被内联压缩），只导出两个函数：

```js
export { DRe as focusChromeTab, kRe as setupBrowserRuntime };
```

`setupBrowserRuntime` 的启动流程：

1. 清缓存、上报"浏览器调用开始"遥测，校验 `node_repl` 特权能力；
2. 安装"提交后钩子"，把每次代码执行结果汇总成浏览器通知；
3. 创建浏览器池，负责发现和连接所有后端；
4. 并行加载 `api.json` 和 `documents.json` 清单；
5. 构建 `agent.browsers` 代理对象，用 `Proxy` 按 capability 裁剪 API 成员；
6. 建立命令分发器，并带权限审批检查。

## 4. `browser-client.mjs` 内部类拆解

核心是一个"**聚合模型**"：每个浏览器用一个 `BrowserController` 门面，内部聚合多个职责单一的管理器。

| 类 | 职责 |
|----|------|
| `_c` | **BrowserPool**：发现/连接所有浏览器后端，提供 `list/get/getDefault/getForUrl/dispose` |
| `qp` | **API 传输**：session request 封装，承载 `executeCdp`、`getTabs`、`createTab` 等全部命令 |
| `Up` | **BrowserController**：单个浏览器的聚合门面 |
| `Dp` | **TabsManager**：标签页 create/list/finalize/get/mark |
| `Tp` | **BrowserUser**：用户标签页/历史/claim |
| `Ip` | **CUA**：坐标式计算机操作（点击/拖拽/滚动/键鼠） |
| `Np` | **UI**：鼠标移动 |
| `Rp` | **DevLogs**：console 日志收集 |
| `Pd` | **Downloads**：下载管理 |
| `kp` | **PageAssets**：页面资源打包 |
| `Bp/Fp` | **Clipboard**：剪贴板桥接 |
| `Lp` | **TabLifecycle**：会话级标签页生命周期跟踪 |
| `Op` | **Security**：命令/URL/权限审批 |
| `Ep` | **Playwright**：Playwright 命令封装 |
| `Jp` | **ApiView**：构建 agent.browsers API 视图并过滤成员 |

## 5. API 体系：`agent.browsers.*`

`docs/api.json` 完整描述了暴露给模型的接口，分层清晰：

### 5.1 浏览器发现与选择

- `agent.browsers.list()` / `get(id)` / `getDefault()` / `getForUrl(url)`

### 5.2 标签页（最核心）

- **导航**：`goto` / `back` / `forward` / `reload`
- **状态读取**：`url()` / `title()` / `screenshot()`
- **三种交互通道**：
  - `cua`：坐标式（`click(x,y)` / `move` / `type` / `scroll` / `drag`）
  - `dom_cua`：DOM 节点式（先 `get_visible_dom()` 拿节点 id，再 `click(node_id)`）
  - `playwright`：locator 定位器式（最常用）

### 5.3 PlaywrightAPI

- `locator(selector)`、`getByRole/Text/Label/Placeholder/TestId`
- `frameLocator()`（iframe 内定位）
- `evaluate()`（只读 JS 执行）
- `waitForEvent("download"/"filechooser")`、`waitForLoadState()`、`expectNavigation()`

### 5.4 Capability 能力扩展

- **浏览器级**：`viewport`（视口控制）、`visibility`（显示/隐藏浏览器）
- **标签页级**：`cdp`（原生 CDP）、`browserAuth`（安全登录）、`botDetection`（反爬上报）、`pageAssets`（资源打包）、`webmcp`（页面内工具）

## 6. 关键行为规则

### 6.1 浏览器选择（SKILL.md）

- **显式请求优先**：用户点名 IAB/Chrome/Edge → 只用那个浏览器，绝不回退；
- 有目标 URL → `getForUrl()`；无指定 → `getDefault()`；
- 绑定复用：`tab`/`browser` 变量跨调用持久，不重复获取。

### 6.2 Playwright 纪律（`playwright.md`）

- **DOM 快照驱动**：先 `domSnapshot()`，只从快照构造 locator；
- **strict mode**：点击前必须 `count()===1`，杜绝歧义；
- 失败后取新快照重建 locator，不重试同一 locator；
- 避免 `allTextContents()` / `body.textContent()` 做全页探测（跨浏览器边界极昂贵）。

### 6.3 标签页清理

- 轮次结束前 `browser.tabs.finalize({ keep })`；
- `markDeliverable()` 保留为交付物、`markHandoff()` 保留给下一轮；
- 默认清理研究/搜索/中间/重复/空标签页。

### 6.4 安全登录（`browserAuth`）

- **绝不**让用户在聊天里贴密码/验证码；
- 通过 `browserAuth.request()` 在安全表单中收集凭据；
- 凭据值永不回传模型，只在浏览器侧验证、填写、提交。

## 7. 安全策略：什么操作必须确认

`browser-safety.md` + `confirmations.md` 把操作分成四档：

| 档位 | 行为 | 例子 |
|------|------|------|
| 必须确认 | 即时阻断，先问再动 | 删数据、付款、CAPTCHA、安装扩展 |
| 预授权可用 | 初始提示明确允许才放行 | 登录、上传文件、传输敏感数据 |
| 无需确认 | 直接放行 | cookie 同意、接受 ToS、下载文件 |
| 不可执行 | 必须用户手动接管 | 绕过 paywall、修改密码最后一步 |

网页/邮件/文档等第三方内容一律视为**不可信**，只能提供事实，不能授予权限。

## 8. 文档注入机制（`documents.json`）

为避免上下文膨胀，文档按条件动态注入：

- **始终注入**：`browser-safety`、`browser-control-interruption`、`api-use-behavior`
- **按类型注入**：`tab-claiming-chrome/iab`、`tab-cleanup-*`（按浏览器类型 + API 成员）
- **按能力注入**：`visibility`、`webmcp`、`playwright`
- **按需查找**：`confirmations`、`browser-troubleshooting`、`file-uploads`、`screenshots`

## 9. 诊断运维脚本

`scripts/*.js` 是一套跨平台诊断工具链，由 `chrome-troubleshooting.md` 编排：

| 脚本 | 功能 |
|------|------|
| `installed-browsers.js` | 报告已安装的浏览器（注册表/LaunchServices/which） |
| `chrome-is-running.js` | 检测浏览器进程是否运行 |
| `check-extension-installed.js` | 检查扩展是否安装/启用（读 Preferences JSON） |
| `check-native-host-manifest.js` | 检查 native messaging manifest |
| `open-chrome-window.js` | 用指定 profile 打开浏览器窗口 |

## 10. 设计取舍

| 取舍 | 选择 | 原因 |
|------|------|------|
| 后端统一门面 | `agent.browsers.*` 屏蔽三种后端差异 | 模型只面对一套 API |
| 文档条件注入 | 按类型/能力动态注入 | 控制上下文 token 成本 |
| 三层交互 API | cua / dom_cua / playwright | 适配不同页面结构，各有适用场景 |
| 凭据不落地 | `browserAuth` 在浏览器侧完成 | 密码/验证码永不进模型上下文 |
| 聚合门面模式 | `BrowserController` 聚合多个管理器 | 职责单一、可独立替换 |

## 11. 关键实现入口

| 概念 | 主要文件 |
|------|----------|
| 运行时入口 | `scripts/browser-client.mjs` |
| 浏览器池 | `browser-client.mjs` 中的 `_c` |
| API 传输 | `browser-client.mjs` 中的 `qp` |
| API 类型清单 | `docs/api.json` |
| 文档注入策略 | `docs/documents.json` |
| Playwright 纪律 | `docs/playwright.md` |
| 安全与确认策略 | `docs/browser-safety.md`、`docs/confirmations.md` |
| 安全登录 | `docs/capabilities/tab/browserAuth.md` |
| 浏览器选择引导 | `skills/control-in-app-browser/SKILL.md` |
| 浏览器扩展配置 | `scripts/extension-ids.json` |

## 12. 小结

Codex Browser 插件把"AI 控制浏览器"做成了**三层解耦的工程系统**：

- **协议层**：命名管道 + CDP，跨平台发现连接三种后端；
- **API 层**：`agent.browsers.*` 统一门面，屏蔽后端差异，提供 Playwright / CUA / DOM-CUA 三套交互通道；
- **引导层**：通过 `SKILL.md` + 条件化文档，把安全规则、Playwright 纪律、标签页清理等"怎么用"的规范动态注入给模型。

这套设计的关键在于：**能力完整（什么都能做）与行为受控（不乱做）之间，靠统一 API 门面 + 分级安全策略 + 动态文档注入来取得平衡。**

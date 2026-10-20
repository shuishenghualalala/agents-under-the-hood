# 第 5 章：渲染层应用结构——一套 Web 代码库的两个宿主

> 本章导读：Cue 的渲染层不是为桌面端精简过的壳，而是**完整的 manus.im Web UI 原样打包**（674 个混淆 chunk、约 97MB）。本章讲清这套渲染层的页面地图、状态组织与桌面适配方式——理解它的关键不是「桌面端有什么」，而是「桌面端从 Web 版继承了什么、又怎么兜住差异」。

| 小节 | 回答的问题 | 读者将掌握 |
|---|---|---|
| 5.1 页面地图 | 渲染层有哪些页面、谁能进壳 | 文档路由双白名单 |
| 5.2 状态组织 | 71 个 Redux slice 如何分工 | 状态版图 |
| 5.3 国际化与静态资产 | 24 种语言与 PDF.js | 非代码资产的用途 |
| 5.4 同一套代码的两个宿主 | isDesktop 分支模式 | Web/桌面共享架构 |

## 5.1 页面地图

渲染层是 Next.js 静态导出，主进程对它实施**双层白名单**：哪些文档能被整页加载（文档白名单），哪些路径允许壳内 client-side 导航（导航白名单）。

| 路径 | 用途 | 壳内行为 |
|---|---|---|
| `/` | 未登录着陆页 | 小窗模式（768×680）整页加载 |
| `/login` `/welcome` `/onboarding` | 登录与首次引导 | 小窗模式整页加载 |
| `/app/agents`、`/app/agents/{id}` 等 | 已登录工作区（对话） | 大窗（1200×800）+ **仅这些子路由允许壳内导航** |
| `/app/artifacts`、`/app/plugins`、`/app/skills` 等其他 `/app/*` | Web 版功能页 | **壳内不导航**，交给系统浏览器打开 manus.im 对应页 |
| `/screen-live-viewer` | 远控 viewer | 由主进程开独立窗口加载（第 3 章），导航锁死 |
| `/cloud-computer` | 云电脑管理/SSH 终端 | 可整页加载；web 端另有 `/cloud-computer/:id` 路由 |
| `/verify-im` | IM 账号绑定校验 | app link `bind-im-user` 映射进入 |

主窗口的初始 URL 由登录态决定：已登录直达 `/app/agents`，未登录停在 `/`。这套白名单的设计含义：**壳只承接「对话工作区」这一种核心体验，其余功能宁可跳出浏览器也不在壳内凑合**——壳内每多一个页面就多一分导航被劫持的面。

## 5.2 状态组织

Redux Toolkit，全量扫描到 **71 个 slice**。按职责分版图：

| 版图 | 代表 slice |
|---|---|
| 会话核心 | `sessions`、`chat`、`websocket`、`sessionSwitch`、`navbar`、`ui`、`notification`、`auth` |
| Agent/任务 | `agents`、`cascadeJobs`、`mapReduce`、`automation`、`timeline`、`experiment` |
| 产物/文件 | `fileManagement`、`filePreviewer`、`fileShare`、`codeViewer`、`officePreviewer`、`pptSlideEditor`、`aiCanvas`、`aiVideo`、`videoEditor` |
| 桌面/设备 | `myComputer`、`screenLive`、`myBrowser`、`tunnel`、`phone`、`meeting` |
| 项目/团队/计费 | `project`、`projectSkills`、`team`、`enterpriseTeam`、`knowledge`、`connectors`、`agentWallet`、`addon` 系列 |

数据获取走 RTK Query（`reducerPath: "api"`，tagTypes 只有 `Sessions` 和 `Uploads`）。slice 清单本身就是一张 Manus 产品功能地图——大量 slice（meeting、phone、webDev、agentWallet）在桌面端未必有入口，但代码全量在场。

## 5.3 国际化与静态资产

- **登录页**有独立的 l10n 目录（`agents-login-l10n/`），24 个语言 JSON——登录页需要在小窗里秒开，不等主 bundle。
- **主渲染层**用自研的英文-key 词典，每种语言一个懒加载 chunk，全包约 25 个大翻译 chunk、每块约 9500 条。
- `out/pdf-assets/` 是 **PDF.js 运行时**（worker + openjpeg/qcms 等 wasm 编解码器），支撑应用内 PDF 预览——也解释了渲染层 CSP 里 `'wasm-unsafe-eval'` 为什么存在。
- `out/video-entry/` 只是两条营销预览 mp4，非功能页面。

## 5.4 同一套代码的两个宿主

渲染层代码库里遍布 `isDesktop` / `isAgentsDesktop` 分支：选 REST baseUrl（桌面端直打 `api.manus.im`，网页开发环境走相对路径）、决定是否出现「Control computer」入口、切换更新 UI 的数据源（Cue 的 `__desktopUpdater` 桥与旧 Manus 桌面端的 `__apis` 桥双兼容，后者在 Cue 里恒为 undefined）。

这个「一份代码、两个宿主」的模式解释了渲染层里大量的**死分支**：为 Web 版写的本地能力 fast-path（第 6 章）、为旧桌面端写的 updater 分支（第 8 章），在 Cue 里都是能执行但永远走不到的代码。逆向阅读时这是主要的干扰源——**看到一个 API 面，必须先找到它的赋值方，才能确认它在这个产品里活着**。

## 5.5 小结

- **你该带走什么**：渲染层 = 完整 Manus Web UI + 桌面适配分支；壳用双层白名单把它约束在「对话工作区」内；71 个 slice 是产品功能地图，但活着的功能由分支条件决定。
- **下一步**：第 6 章回答一个安全敏感的问题——云端 Agent 怎么访问你的本地文件，以及这条链路是否真的如表面那样需要你的授权。

## 关键实现入口

| 主题 | 位置 |
|---|---|
| 文档/导航白名单 | `dist/main.cjs`（`rendererRoutes`，约 :22956-23016） |
| 主窗口初始路由与小窗模式 | `dist/main.cjs`（约 :25100、:25156） |
| 路由表（含 web 版 cloud-computer） | `out/_next/static/chunks/68702-*.js` |
| Redux slice 全量 | 全 chunk 扫描 `{name:"...",initialState` |

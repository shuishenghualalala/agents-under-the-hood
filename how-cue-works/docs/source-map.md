# 源码索引（证据锚点）

> 本表集中收录全书引用的证据位置。逆向产物路径：`~/rea-analysis/cue-src`（解包源码）、`~/rea-analysis/cue-overview.json`（REA 证据图谱，68MB）。渲染层 chunk 文件名带内容哈希，1.0.3 版本的哈希值如下，其他版本需重新定位。

## 主进程（dist/main.cjs，2MB esbuild 产物）

| 主题 | 锚点 |
|---|---|
| 模块清单 | 全文检索 `// backend/`：16 个模块（AuthSession / BrowserLogin / CredentialStore / DesktopUpdates / ComputerWindows / WebWindows / PreviewUpdater / UpdateCheckpoint / UpdatePolicyTest / appProtocol / ipc / main / rendererCors / rendererPermissions / rendererStorageMigration / rendererWindow / urlPolicy） |
| 环境配置 | 约 :16323-16328，`webOrigin` / `rpcOrigin`（prod: manus.im / api.manus.im） |
| token 存储 | `// backend/CredentialStore.ts` 起（约 :16331）：session cookie `session_id`，httpOnly+secure+sameSite:lax，365 天；legacy `session.enc` 登录后删除 |
| 登录流 | `// backend/AuthSession.ts` + `BrowserLogin.ts`（约 :23100-23320）：nonce → cue:// 回调 → generateTempAuthCode → exchangeTokenByCode → userInfo |
| 请求标头注入 | 约 :23241-23247：`authorization` + `x-client-id/type/version/locale/timezone` |
| Connect-RPC transport | :22718 `createConnectTransport({baseUrl: rpcOrigin})` |
| 127 个 RPC service 清单 | 约 :22750-22900 `createClientRpcClients` |
| 远控窗口族 | `// backend/ComputerWindows.ts`（约 :23513-23670）：open/data/fit/close/finishClose/fullscreen |
| IPC handler 注册 | `// backend/ipc.ts`（约 :24434-24533）：43 个频道 |
| `playback`/`playing-sessions` | :24466-24491（聚合 + 广播；渲染层无调用方） |
| 更新对话框 "prompt" | 约 :23955-24067（证明主进程无 prompt 组装的负证据检索范围） |

## preload（dist/preload.cjs，源码级可读）

| 桥对象 | 行 |
|---|---|
| `agentsDesktop`（auth/window/updater/recording/debug/device/billing/system） | :13-95 |
| `__desktopUpdater` | :96-113 |
| `desktopAuthStorage` | :114-119 |
| `__screenLiveViewer` | :120-146 |

## 渲染层（out/_next/static/chunks/，混淆）

| 主题 | 锚点 |
|---|---|
| ScreenLiveViewer（LiveKit 连接、输入队列、光标校对、剪贴板/录制 topic） | `45722-50c1947e8a45d0bb.js` 模块 88145（约 105KB） |
| 云电脑 SSH 终端（xterm.js + `wss://.../api/cloud-pc/ws/ssh`） | `app/cloud-computer/page-81b9551d79ff46ae.js` 模块 41086 |
| 前端环境配置（chatWebsocketUrl 等） | `68702-da6a498ff4614569.js` |
| WS 封装与 `join_session` 握手 | `8948` / `70109` / `53116` 相关 chunk |
| REST baseUrl 选择 + RTK Query 认证注入 | `93000` chunk（`getSessionV2` / `prepareHeaders`） |
| addon SSE 断点续传（last-event-id） | `40921` chunk |
| 打开远控的三个调用点 | `53116` chunk：`openScreenLiveViewer`（偏移约 4916764 / 2543185 / 1247796） |
| `/api/desktop/*` 路由表 + fast-path 死代码 | `93000` chunk（`fileLocations`）；`45722` 模块 98448；`68702` 模块 69124（`window.__apis` 只有读取无赋值） |
| 逐次审批消息 | `53116` chunk（`resolve_local_execution_approval` 等） |
| 技能录制识别（`skill-recorder` MCP 工具） | chunk `45722`（`[computer_use.meta/v1]` 前缀） |
| 录制协议状态机 | chunk `45722` 模块 88145（`manus.screen_live.skill_recording.v1`，方法 open/status/start/stop/readArtifact/cleanup/cancel/uploadArtifact/completeTranscription） |
| 旁白录制（MediaRecorder 60s 分段 → audioTranscription） | chunk `45722`（`audio/webm;codecs=opus`） |
| 录制交付页 | chunk `app/screen-live-viewer/page-*.js` 模块 51208 |
| 更新 QA 钩子消费端 | chunk `76662`（`cue-version-policy:*:reminder`） |
| 安装前协调回答方 | chunk `93759`（`onPrepareRestart` handler） |
| Redux 71 slice | 全 chunk 扫描 `{name:"...",initialState` |
| i18n | `out/agents-login-l10n/` 24 语言 JSON；主渲染层约 25 个翻译 chunk、每块约 9500 条 |

## 主进程补充（第 6-8 章锚点）

| 主题 | 锚点 |
|---|---|
| 唯一本地 HTTP listener（更新器代理，随机端口+Basic 密码） | :15462 起 |
| `DesktopService` proto（CONNECT_FOLDER / SET_ALLOW_CMD） | 约 :22347 |
| device-id 文件 | 约 :16344 |
| `rendererRoutes` 文档/导航白名单 | 约 :22956-23016 |
| `urlPolicy` | 约 :23016-23106 |
| WebWindows 全禁权限 + sandbox | 约 :23350-23356 |
| 远控窗口录制联动 / `closeForUpdate` | 约 :23562-23563、:23651-23669 |
| 更新栈四模块 | :23683-24139（PreviewUpdatePolicy / PreviewUpdater / UpdateCheckpoint / DesktopUpdates） |
| task-idle 自动安装硬编码禁用 | 约 :23763-23765、:24578-24582 |
| `appProtocol` 单 handler + CSP nonce | 约 :24155-24226 |
| `rendererStorageMigration` | 约 :24228-24290 |
| `rendererCors` CORS 代答 | 约 :24586-24648 |
| `rendererPermissions` 默认拒绝 | 约 :24650-24696 |
| 弹窗拦截 | 主窗口约 :25095-25100；远控窗口约 :23602 |
| webReturnToken 换发与 partition 隔离 | 约 :24745、:24824 |
| 更新源配置 | `/Applications/Cue.app/Contents/Resources/app-update.yml`（generic，`https://download.cue.im/`） |

## REA 证据图谱（cue-overview.json）

| 字段 | 内容 |
|---|---|
| `normalized_result.statistics` | 720 文件 / 685 解析成功 / 5456 模块 / 33684 findings / 0 解析失败 |
| `normalized_result.summary.ipc` | 43 literal 频道、35 main handler、35 渲染层调用全部配对 |
| graph 节点 | 45 个 ipc-handler、43 个 ipc-channel、5 个 context-bridge-api、2 个 browser-window、1 个 electron-main（入口 dist/main.cjs） |

## 证据缺口登记

| 缺口 | 说明 |
|---|---|
| 云端上下文管理机制 | 闭源服务端，客户端逆向不可达 |
| 被控端输入执行逻辑 | 被控端不在此 asar，仅从 viewer 协议推断 |
| `/api/desktop/*` 设备侧执行器 | 云端请求如何落地到本机执行的组件不在包内，仅确认到「请求发往云端」 |
| chat WS 具体路径 | 握手细节在运行时初始化，静态包内只见 host |
| `agents:recording:setActive` 渲染端调用点 | 主进程语义明确，调用点未逐字定位 |

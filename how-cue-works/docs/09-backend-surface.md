# 第 9 章：后端接口面——WS / REST / Connect-RPC

> 本章导读：本章是参考手册性质的一章，把客户端能触达的云端接口面完整列出来，并标注每个接口的运行时用途。读对话机制看第 4 章，读远控看第 3 章，读本机能力桥看第 6 章；本章回答的是「这个客户端一共能和云端说哪些话」。

| 小节 | 内容 |
|---|---|
| 9.1 环境配置 | prod / dev 的地址与内部代号线索 |
| 9.2 REST 清单 | 33 个端点，按用途分组 |
| 9.3 Connect-RPC 清单 | 127 个 service，按域分组 |
| 9.4 实际使用面 | 打包清单 ≠ 实际调用面 |

## 9.1 环境配置

客户端内置 prod / dev 两套环境，切换由打包时的构建标志决定：

| 用途 | prod | dev |
|---|---|---|
| 网页 origin | `https://manus.im` | `https://vida.butterfly-effect.dev` |
| RPC / REST / WS | `https://api.manus.im`（WS 为 `wss://`） | `https://vidabiz.butterfly-effect.dev` |
| 反馈 | `https://cue.im/feedback` | — |
| 更新源 | `https://download.cue.im/` | — |
| 恢复工具下载 | `https://download.manus.im/recovery/mac/...` | — |

旁证：Sentry DSN 指向 `sentry.prod.ops.butterfly-effect.dev`——「butterfly-effect」是 Manus 的内部代号（其公司主体即 Butterfly Effect）。

## 9.2 REST 清单

全部 33 个端点，桌面端直打 `https://api.manus.im`（baseUrl 选择逻辑：桌面/生产环境用绝对地址，网页开发环境用相对路径）。按用途分组：

| 分组 | 端点 |
|---|---|
| 会话/聊天 | `getSessionV2`（历史游标分页）、`getSessionOutline`、`getSessionFilesV2`、`getAgentDirectMessages`、`deleteSession`、`createTaskDraft`、`scheduleTask` |
| 文件/上传 | `getPresignedUploadUrl`、`uploadComplete`、`getDownloadUrl` |
| 执行环境 | `startSandbox`、`getSandboxStatus`、`requestVncOtp`、`wakeMobilePreview`、`getMobilePreviewStatus`、`getOnlineMyBrowserClients` |
| 分布式任务 | `getMapReduceTool`、`listCascadeJobs`、`getCascadeJobDetail`、`getCascadeJobEvents`、`updateCascadeJobSummaryReaction` |
| 本机能力（云端中继） | `/api/desktop/file-locations`、`list-directory`、`search-files`、`screen-capture`、`workspace-git-info`、`switch-workspace-git-branch`、`session-workspace/list|read`、`addon-preview` |
| 其他 | `/api/v1/resource-assets/`、`/api/internal/fixUrlMeta`、`/api/internal/resetDailyQuota` |

注意 `/api/desktop/*` 这组的名字有迷惑性：它**不是**本机端口上的服务，而是 `api.manus.im` 的云端 endpoint，按 `deviceId` 把请求中继到已注册设备（完整机制与授权模型见第 6 章）。

## 9.3 Connect-RPC 清单

127 个 service 的 proto 客户端全量打包进应用（路径形如 `/session.v1.ProjectService/GetProject`），按域归组：

| 域 | 代表 service |
|---|---|
| Agent 核心 | `AgentService`、`AgentsService`、`AgentsSandboxService`、`AgentChannelsService(+Sandbox)`、`AgentComputerService`、`AgentGroupsService(+Sandbox)` |
| 会话/项目 | `ProjectService`、`ProjectSandboxService`、`SessionPublicService`、`SessionCollaboratePublicService`、`KnowledgeService`、`SkillService` 系列（含 Team/Project 变体） |
| 沙箱/云电脑 | `SandboxService`、`SandboxRuntimeService`、`DurableSandboxConfigService`、`E2BConfigService`、`SandboxPolicyService`、`CloudPCService`、`DataCenterService`、`TelecomService(+Sandbox)` |
| 远控授权 | `DeviceAuthorizationService`（14 个方法：startScreenLive / keepScreenLive(45s) / stopScreenLive / 授权码创建-兑换-注册 / 授权授予-列表-批量吊销 / 登录设备管理） |
| 桌面/设备 | `DesktopService`（设备注册与文件夹授权：CONNECT_FOLDER / SET_ALLOW_CMD）、`McoDesktopService`、`UserCorsService` |
| 触发器/自动化 | `TriggersService(+Sandbox)`、`ScheduleSandboxService`、`MindpilotService(+Public)` |
| 用户/团队/计费 | `UserService`、`UserAuthService(+Public)`、`SubscriptionService`、`StripeLinkService`、`TeamService` 系列、`Enterprise*Service`、`SSOService`、`ComplianceService` |
| 其他 | `Connectors*`（6 个）、`NotificationService(+Public)`、`ImageService`、`FileService`、`VideoAnalysisService`、`ElevenLabsService`（语音转写）、`TunnelService`、`OpenapiService/V2/Oauth`、`OAuth2Service`、`UserApiProxyService`（旁白转写走这里）等 |

值得注意的名字：`E2BConfigService` 暗示沙箱基础设施与 E2B 有关或兼容；`McoDesktopService` 的 MCO 含义不明（推断为内部项目代号，无证据）。

## 9.4 实际使用面

打包清单不等于调用面。静态可确认的实际使用：

- **主进程**：只调 3 个 RPC 方法，全部在登录链路（`generateTempAuthCode` / `exchangeTokenByCode` / `userInfo`）。
- **渲染层**：主要使用 Project / Session / Agent / Desktop / DeviceAuthorization 几个系列；其余 service 客户端是 protobuf-es 代码生成的产物，随依赖树整体打包进来。

换言之，127 个 service 是 Manus 整个 RPC 契约的快照，不是 Cue 的能力清单。对逆向者它是免费的**服务端 API 地图**；对理解 Cue 本身，第 3、4、6、8 章用到的那十几个方法就是全部。

## 9.5 小结

- **你该带走什么**：三层协议分工（WS 推事件、REST 查数据、RPC 管资源）；`/api/desktop/*` 是云端中继而非本地服务；127 个 RPC service 清单是了解 Manus 服务端版图的最佳索引。
- **下一步**：第 10 章反向工程——如果今天让你复刻一个 Cue，最小必要组件是什么。

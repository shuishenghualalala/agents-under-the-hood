# 第 3 章：电脑控制——LiveKit 远控的完整链路

> 本章导读：Cue 的「控制电脑」不控制本机，而是实时观看并操作**另一台装了 Cue 的设备**或**云端沙箱电脑**。本章回答三个问题：画面怎么来、输入怎么去、凭什么允许你控制。

本章结构如下——先立设计目标（3.1），再讲三个核心抽象（3.2），然后走一遍从点击到控制的完整生命周期（3.3），最后落在窗口管理与边界取舍（3.4-3.5）：

```mermaid
flowchart LR
    A["3.1 设计目标"] --> B["3.2 核心抽象"]
    B --> C["3.3 完整链路"]
    C --> D["3.4 viewer 窗口管理"]
    D --> E["3.5 边界与取舍"]
    E --> F["3.6 小结"]
    A -.->|为什么不能本地控制| B
    B -.->|三个角色如何配合| C
    C -.->|主进程侧的生命周期| D
    D -.->|为什么这样设计| E
```

## 3.1 设计目标

先给结论性事实：对主进程全量检索，**不存在任何本地截屏或本地输入注入代码**（无 `desktopCapturer`、`capturePage`、CGEvent、robot 类调用）。这个「无能」是刻意的：

1. **被控对象在别处**。Manus Agent 的工作环境是云端沙箱电脑；「控制电脑」的本质是把云电脑的屏幕搬到本地窗口。本机只是显示器和键盘。
2. **传输要过广域网**。画面帧率和输入延迟直接决定可用性，需要成熟的实时媒体栈，而不是自己造 WebSocket 传 JPEG。
3. **控制需要被授权**。操作另一台设备是敏感能力，必须有审批与吊销机制。
4. **主进程不碰媒体**。视频解码、输入采集全部在渲染层完成，主进程只管理窗口——媒体栈崩溃不拖累壳。

## 3.2 核心抽象

远控体系由三个角色组成，每个角色只懂一件事：

| 角色 | 位置 | 运行时职责 |
|---|---|---|
| **ScreenLiveViewer 窗口** | 本地（主进程 `ComputerWindows` 管理） | 一个专用 BrowserWindow，加载本地 `/screen-live-viewer` 页面，导航被锁死在该路径 |
| **LiveKit Room** | `*.livekit.cloud`（WebRTC） | 媒体房间：被控端以 `desktop-<deviceId>` 身份发布屏幕轨道，viewer 订阅；输入走 data channel |
| **DeviceAuthorizationService** | api.manus.im（Connect-RPC） | 控制面的授权与令牌签发：发 LiveKit 地址和 token、审批授权码、心跳保活 |

分辨被控端类型的钥匙是 `deviceId` 形状：普通设备是设备 ID；云电脑/Agent 沙箱带 `isCloudComputer: true` 或 `long:` 前缀加 `sessionId`。viewer 对此不做区分——协议完全相同。

## 3.3 完整链路：从点击到控制

这是全章的主图。注意两条失败分支：授权码审批和窗口复用。

```mermaid
sequenceDiagram
    participant U as 用户（主窗口页面）
    participant M as 主进程
    participant V as viewer 页面
    participant API as api.manus.im
    participant LK as LiveKit Cloud
    participant T as 被控端 desktop-&lt;deviceId&gt;

    U->>M: IPC open {deviceId, deviceName, sessionId?}
    alt 同设备窗口已存在
        M-->>U: restore + focus（不新建）
    else 新建
        M->>V: 创建 BrowserWindow，加载 /screen-live-viewer
        Note over M: deviceId 存主进程 Map<br/>（key=webContents.id，不走 URL 参数）
    end
    V->>M: IPC data（取回设备信息）
    V->>API: startScreenLive {targetDeviceId, requestControl:true}
    alt 返回 device_authorization_required
        API-->>V: 需要授权
        V->>U: 弹授权对话框（被控端输入 6 位数字码审批）
        U->>API: 审批通过后重试
    end
    API-->>V: {url, controlToken, viewerId}
    V->>LK: room.connect(url, controlToken)
    Note over V,LK: 区域回退：预探测各 region 延迟后选区
    LK-->>V: 订阅被控端屏幕轨道（多屏经 data 消息切换）
    V->>M: 首帧到达 → IPC fit（窗口贴合画面分辨率）
    loop 每 45 秒
        V->>API: keepScreenLive 心跳
    end
    U->>V: 鼠标/键盘（DOM 事件）
    V->>LK: publishData(JSON 输入事件, 仅发被控端)
    LK->>T: data channel 送达
    T-->>LK: 光标位置回报（screen-live-cursor，1s 心跳）
    LK-->>V: 校对本地光标
    U->>M: 关窗
    M->>V: 拦截 close → close-requested（先让页面收拾）
    V->>API: stopScreenLive
    V->>M: IPC finish-close → 真正销毁窗口
```

链路上值得展开的三段：

**输入通道是「队列 + 校对」，不是直通**。DOM 事件（pointer/wheel/key/paste/IME composition）转成 JSON 后进入一个输入队列：`move_abs` 按 16ms 节流合并、`scroll` 增量限幅 ±4、队列满 128 条即拒绝（背压）。移动和点击事件盖 `{epoch, sequence}` 时间戳，与被控端回传的光标位置流（1 秒心跳）比对——viewer 能检测「我的输入是否已经生效」。断线时自动补发全部 keyup/mouse_up（`releaseAll`），防止按键卡在被控端。

**多屏与音频走带外协商**。被控端 participant 的 metadata（JSON）声明自己的能力：控制权限、输入可用性、平台、屏幕列表（`screen:0` 等轨道名）。viewer 用 `request_screens` / `select_screen` data 消息切屏；音频开关走独立的 RPC 方法 `manus.screen_live.set_audio`。

**剪贴板与技能录制是独立 data topic**。剪贴板走 `manus.screen_live.clipboard.v1`（双向各一个 topic），技能录制走 `manus.screen_live.skill_recording.v1`——与输入事件流互不阻塞。

## 3.4 viewer 窗口管理（主进程侧）

主进程对远控窗口的管理体现了「窗口即会话」的建模：

- **去重复用**：以 `deviceId + sessionId` 为键，已有窗口则聚焦而非新建。
- **参数不走 URL**：设备信息存主进程 Map（键为 webContents.id），viewer 启动后 `invoke` 取回。URL 里不放敏感参数，也不会被渲染层篡改。
- **关闭三段式**：用户点关闭 → 主进程 `preventDefault` 并通知页面 `close-requested` → 页面停录/断流后 `finish-close` → 才真正销毁。录制中的窗口还会被额外延迟 2 秒，并且**阻塞自动更新重启**（提示用户先结束录制）。
- **自适应尺寸**：viewer 报告远程画面分辨率后，主进程把窗口缩放到恰好容纳画面加工具栏（上限 1200×800）。

**证据缺口**（如实说明）：preload 和主进程完整实现了 `playback` / `playing-sessions` 通道（渲染层上报「正在播放哪些会话」，主进程聚合去重后广播），但 1.0.3 打包的渲染层**没有任何调用方**——属性名不会被混淆，这是可靠负证据。推断为网页版共用代码或后续版本预留。

## 3.5 边界与取舍

| 选择 | 替代方案 | 取舍理由（推断） |
|---|---|---|
| LiveKit WebRTC | 自建 WebSocket 传帧 | 拿到自适应码率、TURN 穿透、区域调度；输入复用同一连接的 data channel，免鉴权二次握手 |
| 区域延迟预探测 | 固定区域 | LiveKit 按项目分区域，客户端实测延迟选区，牺牲首次连接数百毫秒换稳定低延迟 |
| 授权码在被控端审批 | 主控端自助 | 操作他人设备的信任锚在被控者手里；6 位数字码可电话口述 |
| 主进程只管窗口 | 主进程代理媒体 | 媒体栈崩溃隔离；但也意味着 viewer 页面积较大（105KB 混淆 chunk），能力面集中在渲染层 |

**证据边界**：被控端（发布画面、执行输入的一端）不在这个 asar 里，其对输入事件的消费方式只能从 viewer 端协议形状推断。

## 3.6 小结

- **你该带走什么**：远控 = 「主进程管窗口 + 渲染层跑 LiveKit + 服务端发令牌」三方协作；输入是带背压、带校对、断线自愈的 data channel 队列；授权以被控端 6 位码为信任锚。
- **下一步**：第 4 章回答另一个核心问题——Agent 对话的上下文在哪管理。剧透：不在本地，而「为什么本地敢是空的」恰恰是这个架构最诚实的地方。

## 关键实现入口

| 主题 | 位置 |
|---|---|
| IPC handler 注册 | `dist/main.cjs`（`// backend/ipc.ts`，约 :24434-24533） |
| 远控窗口族 | `dist/main.cjs`（`// backend/ComputerWindows.ts`，约 :23513-23670） |
| viewer 页面（LiveKit 连接/输入队列） | `out/_next/static/chunks/45722-*.js` 模块 88145 |
| 云电脑 SSH 终端页 | `out/_next/static/chunks/app/cloud-computer/page-*.js` |
| 桌面端打开远控的调用点 | `out/_next/static/chunks/53116-*.js`（三处 `openScreenLiveViewer`） |

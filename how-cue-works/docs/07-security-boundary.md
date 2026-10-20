# 第 7 章：安全边界——壳的防御体系

> 本章导读：一个把网页、远控、文件能力装进 Electron 的应用，安全边界的设计决定了它的上限。本章系统盘点 Cue 主进程设置的每一道防线：URL 策略、协议 handler、CSP、权限处理器、窗口管控。总体评价先行：**默认拒绝、白名单收紧、凭证不出壳**。

本章按「URL -> 协议 -> 页面 -> 权限 -> 窗口」的攻击路径顺序组织：

| 小节 | 防的是什么 | 机制 |
|---|---|---|
| 7.1 URL 策略 | 恶意链接进壳 | 分类校验 + 白名单 |
| 7.2 `cue://` 协议 | 回调劫持、路径穿越 | 单 handler + 严格参数校验 |
| 7.3 CSP 与存储 | XSS、数据残留 | nonce 化 CSP、协议迁移 |
| 7.4 权限与 CORS | 页面越权拿设备能力 | 默认拒绝 + 主进程代答 |
| 7.5 窗口管控 | 弹窗/导航逃逸 | 一律拦截重定向 |

## 7.1 URL 策略：所有入口的分类闸

主进程对所有进出壳的 URL 做分类校验（`urlPolicy`），规则要点：

- **网页 URL**：必须 http/https 且**禁止 userinfo**（`https://user:pass@host` 形态直接拒绝）——防止凭据藏在 URL 里泄露。注意这里**没有域名白名单**：打开系统浏览器不需要白名单，因为风险留在了壳外。
- **`cue://` 回调**按用途精确匹配：`oauth/desktop-callback`（nonce 必须 64 位 hex、token/error 互斥、token 上限 32768 字符）、`billing/return`（精确路径、无 query）、`wallet/callback`、`connectors/return`。
- **跨壳回跳**（`webReturn`）：returnTo 必须是 manus.im 源 + 路径在导航白名单 + 携带与当前一次性 `webReturnToken` 相等的参数。token 每次登录换发，外部浏览器会话按 token 做 storage partition 隔离——回跳页面之间互相看不见对方的存储。

## 7.2 `cue://` 协议：只有一个 handler

`cue://desktop`（dev 环境为 `cuedev://`）**不是通用 RPC 协议**，只挂了一个静态文件 handler，职责是把 `out/` 里的渲染层页面喂给窗口。它的校验链值得当作范本：

1. host 必须精确等于 `desktop`，禁止 userinfo；
2. 路径禁止 `\`、`..`、`:`，解析结果必须落在 `out/` 根内，越界返回 403/404；
3. `.html` 响应注入**每个请求随机生成的 18 字节 nonce** 到 `<script nonce>`，并附完整 CSP；
4. `.txt` 带 `_rsc` 参数时以 `text/x-component` 返回（Next.js RSC 流）。

## 7.3 CSP 与存储

渲染层文档的 CSP：

```
default-src 'self';
script-src 'self' nonce-<随机> 'wasm-unsafe-eval' https:;
style-src 'self' 'unsafe-inline' https:;
img-src/font-src https: data: blob:;
connect-src 'self' https: wss:;
frame-src 'self' https: blob:; object-src 'none'; base-uri 'self'
```

读法：脚本要么来自自身、要么带本次请求的一次性 nonce——注入的内联脚本没有 nonce 就无法执行；`'wasm-unsafe-eval'` 是为 PDF.js 的 wasm 编解码器开的口子（第 5 章）；`connect-src https: wss:` 与主进程的 CORS 代答（7.4）配套。

存储侧有一次安静的迁移：旧版渲染源是 `agents-app://desktop`，新版改名 `cue://desktop`。主进程用一个隐藏的无 Node 沙箱 WebContentsView 抓旧源的 localStorage 快照，写入新源同名存储，落一个 marker 文件防重复执行——换协议不丢用户数据。

## 7.4 权限与 CORS

**权限处理器默认拒绝**。前提校验（必须是主 frame 且文档来自渲染层）不过就拒；过了也只放行三样：

| 权限 | 策略 |
|---|---|
| `clipboard-sanitized-write`、`fullscreen` | 恒允许 |
| `media`（仅音频） | 先过 macOS 系统级麦克风授权（`systemPreferences.askForMediaAccess`），再允许 |
| 摄像头、屏幕采集等其余一切 | 拒绝 |

**外部网页窗口（WebWindows）全禁**：权限处理器恒拒，且 `sandbox:true`、无 preload、禁止 webview 附着——壳内打开的 manus.im 页面拿到的是比渲染层更弱的运行环境。

**CORS 由主进程代答**，这是「渲染源是 `cue://` 自定义协议」的必然代价：浏览器对非 http 源的跨域请求一律拦截，所以主进程拦截所有 https 请求，当且仅当发起者是指定渲染层、Origin 头是 `cue://desktop`、资源类型匹配时，在响应里注入 `Access-Control-Allow-Origin: cue://desktop` 等头。需要诚实指出这是最宽的一道口子：**渲染页面由此获得向任意 https 源发带凭证跨域请求的能力**——设计使然，代价由 CSP、nonce 和权限防线共同兜住。

## 7.5 窗口管控

所有 `window.open` 与弹窗都被接管：

- 主窗口与远控窗口的 `setWindowOpenHandler`：**一律 deny**，把目标 URL 转交系统浏览器——壳内不生出不受控的新窗口。
- WebWindows（壳内 manus.im 页）：允许 `about:blank` 与 http(s) popup，但生出来后继续被导航监管。
- `agents:open-external`：`sms:` 直接交系统（构造短信，body 有字符白名单）；manus.im 源开壳内 WebWindows；其余一律系统浏览器。

## 7.6 小结

- **你该带走什么**：纵深防御的实际形态——URL 分类闸（7.1）、协议单 handler + 路径约束（7.2）、nonce 化 CSP（7.3）、权限默认拒绝 + CORS 代答（7.4）、窗口弹出一律拦截（7.5）；最宽处是 CORS 代答与无白名单的系统浏览器跳转，两者都把风险推出了壳的信任边界。
- **下一步**：第 8 章看两个「运行中」的机制——自动更新如何与用户工作共存，技能录制如何把一次远控操作变成可复用的资产。

## 关键实现入口

| 主题 | 位置（`dist/main.cjs`） |
|---|---|
| URL 策略 | `urlPolicy`，约 :23016-23106 |
| 文档/导航白名单 | `rendererRoutes`，约 :22956-23016 |
| `cue://` 协议 handler 与 CSP | `appProtocol`，约 :24155-24226 |
| 存储迁移 | `rendererStorageMigration`，约 :24228-24290 |
| CORS 代答 | `rendererCors`，约 :24586-24648 |
| 权限处理器 | `rendererPermissions`，约 :24650-24696；WebWindows 全禁约 :23350-23356 |
| 弹窗拦截 | 主窗口约 :25095-25100；远控窗口约 :23602 |
| 外链打开 | `agents:open-external` 约 :24404；`open-browser` 约 :24416 |

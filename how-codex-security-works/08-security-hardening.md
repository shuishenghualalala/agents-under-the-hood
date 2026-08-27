# 第 8 章：安全加固

## 本章导读

读完这一章，你会知道：

1. 整个系统的威胁模型是什么（一个能写文件、能调 LLM 的 agent，要喂给它不可信代码）；
2. 凭据如何被隔离（credential home 严格校验）；
3. Codex 配置如何被清理与收敛（密钥过滤、权限 profile、插件所有权）；
4. 插件分发与工具定位如何防投毒（zip 安全、可信 PATH）；
5. 各层加固如何叠加成纵深防御。

## 1. 威胁模型

`codex-security` 的处境很特殊：它要**把一份不可信的代码库喂给一个能读写文件、能发起网络请求、能调 LLM 的 agent**。安全上要防的不是"用户攻击系统"，而是：

- **恶意仓库通过 agent 逃逸**：仓库里放 `post-checkout` hook、`.git` 里的恶意配置、路径名/符号链接攻击，让 agent（或它调用的 git/python/node）执行意外代码；
- **凭据泄漏**：agent 读到宿主/CI 的全局凭据，或通过配置注入把密钥带进 prompt 传给 LLM 或第三方；
- **配置投毒**：用户配置里被塞入 `__proto__`/`constructor` 原型污染、或覆盖掉插件加载配置、或禁用掉安全机制（multi-agent v2）；
- **路径穿越**：产物/scope 里的路径逃出允许目录。

每一层加固都是针对其中一类威胁。

## 2. 凭据隔离：requireSecureCredentialHome()

外壳层为扫描准备一个**独立的凭据目录**（`prepareCodexSecurityCredentialHome()`），而不是直接复用用户的 `~/.codex/auth.json`。`requireSecureCredentialHome()`（`runtime.ts`）用最严格的语义校验这个目录：

| 校验 | 针对的威胁 |
|------|-----------|
| 目录 mode 必须 0700 | 同机其他用户读凭据 |
| **拒绝符号链接** | 符号链接把凭据目录指到别处 |
| **dev/inode 固定**（目录在创建后 dev+inode 不得变化） | TOCTOU：创建后被换绑 |
| Windows 上用 SDDL / icacls 收 ACL | 平台等价的访问控制 |

配合 `.mcp.json` 里声明的 `env_vars` 白名单（只放行 `CODEX_*`、`AWS_*`、代理等明确需要的变量），agent 的环境是**最小化、被审核过**的。

## 3. 配置清理与收敛

`config.ts` 对"agent 将看到的 Codex 配置"做了三道处理：

### 3.1 密钥过滤（preflight）

`scanPreflightCodexConfig()` 在启动前合并用户 overrides 与默认配置，并**过滤掉密钥类配置项**——agent 拿到的配置文件里不该有任何凭据明文。默认配置 `DEFAULT_CODEX_CONFIG` 刻意保持"无密钥、无危险项"：model、effort、features（plugins/goals/multi_agent_v2）。

### 3.2 原型污染防护

`validateOverrideKeys()` 递归拒绝 `__proto__` / `constructor` / `prototype` 键。合并时用 `deepMerge` + `structuredClone`（干净的对象，不继承原型链），杜绝把恶意配置当"普通 JSON"合并进最终 TOML。

### 3.3 插件所有权

`validateOverrides()` 强制一条原则：**插件加载配置（`plugins`/`marketplaces`）归 codex-security 所有**——用户 overrides 里一旦出现这两项，直接抛 `ConfigurationError`。同理禁止关闭 `features.plugins`、禁止降级 multi-agent v2（见第 4 章）。理由：允许用户改插件加载=允许用户替换掉捆绑的安全插件；允许关 plugins=agent 连 MCP 工具都没有了。

### 3.4 加固的权限 profile（runtime）

`scanRuntimeCodexConfig()` 生成运行期配置，写入名为 `codex_security_scan` 的权限 profile：

```
:root              → 只读        （agent 不能改系统）
:workspace_roots   → 可写        （落临时产物）
状态目录           → 可写        （workbench SQLite）
其余               → 默认拒绝
```

配合 `approvalPolicy: "never"`，agent 的能力被收敛到"只能写它该写的"。这里的设计关键是：**安全边界在配置层面，而不是"弹窗问用户"**——自动化扫描没法每步问人。

`writeCodexConfig()` 用原子写：先写临时文件（mode 0600）→ fsync → rename，失败即清理临时文件。保证配置要么是旧的、要么是新的，绝不留下半截 TOML。

## 4. 插件分发与工具定位

### 4.1 安全解压

插件以 zip 分发（`_bundled_plugin` 会被打成 zip 走 marketplace 安装）。`extractPluginZip()`（`runtime.ts`）是安全解压：**校验 CRC32、拒绝反斜杠路径**——防 zip-slip（条目名 `../../evil` 或 Windows 反斜杠逃逸写出目录）。

### 4.2 可信 PATH 定位

`resolveCodexCommand()` / `resolveTrustedExecutable()`（`runtime.ts` 或 `trusted-executable.ts`）在**可信 PATH** 上定位 `codex`、`git`、`python` 等可执行文件。这针对 PATH 劫持：如果当前目录被恶意仓库控制，`PATH` 里出现同名脚本就会执行恶意代码。注意 git 检出时也走 `resolveTrustedExecutable("git", ...)`（第 6 章），因为批量扫描的检出动作本身就在执行一个不可信仓库的 fetch。

### 4.3 隔离的 Python

workbench 子进程用 `python -I -B`（隔离模式 + 禁字节码缓存），避免 `site-packages` 里被塞进恶意模块、避免状态目录旁产生可被污染的 `.pyc`。

## 5. 纵深防御总览

各层加固针对不同层级的威胁，叠加成纵深：

| 层 | 机制 | 章节 |
|----|------|------|
| 进程外隔离 | `python -I -B`、可信 PATH 定位 codex/git/python | 本 章 |
| 配置层 | 密钥过滤、原型污染防护、插件所有权、权限 profile 收敛 | 本 章 |
| 文件系统 | 凭据目录 0700/拒符号链接/dev-inode 固定、zip 防穿越、产物路径校验 | 本 章 + 第 7 章 |
| 执行层 | git hooks 禁用、`GIT_TERMINAL_PROMPT=0`、credential helper 白名单 | 第 6 章 |
| 内核层 | 非 root UID、deny-by-default seccomp、AppArmor、Landlock | 第 6 章 |
| 产物层 | 封签契约 + SHA-256 + 指纹 | 第 7 章 |

一个反复出现的模式：**"安全=明确允许 + 明确拒绝"而非"黑名单"**。seccomp 白名单、权限 profile 默认拒绝、env 白名单、credential host 白名单——系统对所有"agent 能碰到的东西"都先假定不可信，再逐个放行。

## 6. 取舍与边界

| 设计 | 理由 / 代价 |
|------|------------|
| 配置层收敛代替交互审批 | 自动化必需；代价是权限配置必须正确，错了没有人工兜底 |
| 插件所有权归系统 | 防替换捆绑插件；代价是用户不能自定义插件加载 |
| 多重内核隔离 | 批量扫描输入最不可信，值得内核级成本；单仓库扫描可只用配置层 |
| 严格路径/URL 校验 | 一劳永逸挡住一大类注入；代价是约束了合法用法的自由度 |

## 7. 关键实现入口

| 职责 | 位置 |
|------|------|
| 凭据目录严格校验 | `sdk/typescript/src/runtime.ts` 的 `requireSecureCredentialHome()` |
| 插件安装与解压 | `runtime.ts` 的 `bootstrapPlugin()` / `extractPluginZip()` |
| 可执行文件定位 | `runtime.ts` 的 `resolveCodexCommand()` / `trusted-executable.ts` 的 `resolveTrustedExecutable()` |
| 配置合并/清理/校验 | `sdk/typescript/src/config.ts`（`mergedCodexConfig` / `validateOverrideKeys` / `validateOverrides`） |
| 加固权限 profile | `sdk/typescript/src/api.ts` 的 `scanRuntimeCodexConfig()` |
| 原子写配置 | `config.ts` 的 `writeCodexConfig()` |
| env 白名单 | `_bundled_plugin/.mcp.json` 的 `env_vars` |
| 容器内核安全 | `docker/`（seccomp / apparmor / entrypoint） |

## 小结

codex-security 的信任模型是"**先假定不可信，再逐层放行**"：凭据目录严格隔离，配置先滤密钥再收敛权限，插件分发防 zip-slip、工具定位防 PATH 劫持，容器再加内核级白名单。因为扫描必须全自动（`approvalPolicy: never`），安全边界只能靠这些机制"预置"而不是"运行中询问"。这整套加固与第 7 章的产物契约一起，共同回答了"为什么可以信任一次全自动扫描的结果"。

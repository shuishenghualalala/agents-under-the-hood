# 第 10 章：安全沙箱（专题）

## 本章导读

读完这一章，你会知道：

1. 这个项目的"沙箱"到底由哪些机制组成，以及它们各自限制什么；
2. 沙箱是如何分层的（配置层 → 能力 preflight → 凭据/进程级 → 容器内核级）；
3. 每种形态（单仓库扫描 / 批量容器扫描）各启用哪几层；
4. 为什么敢在 `approvalPolicy: "never"`（全自动、不弹审批）下跑扫描。

> 本章是**专题**：把散落在第 6 章（容器安全基线）和第 8 章（安全加固）里的沙箱机制，统一成"体系"视角。第 6、8 章负责机制细节，本章负责把它们串成一条纵深防御链。

## 1. 先厘清边界：什么算沙箱，什么不算

项目里有几个容易混淆的机制，先分清楚：

| 机制 | 是不是沙箱 | 它实际做什么 |
|------|-----------|-------------|
| `codex_security_scan` 权限 profile | ✅ 是 | 限制 agent 的文件系统读写范围（配置层） |
| 容器 seccomp / AppArmor / Landlock | ✅ 是 | 限制进程的系统调用与内核访问（内核层） |
| 凭据目录隔离 / env 白名单 | ⚠️ 半沙箱 | 不是"限制能力"，是"隔离敏感资源"，属于沙箱配套 |
| `preflight/capability-profiles.toml` | ❌ 不是 | 检查运行时**能力是否满足**扫描需求，是"沙箱前置检查" |

一句话：**沙箱 = 限制 agent 能碰到的世界**（文件系统、系统调用、网络凭据）；能力 preflight = 确认运行环境本身够不够格，二者经常一起出现但职责不同。

## 2. 沙箱体系总览：四层纵深

```
┌─────────────────────────────────────────────────────────────┐
│ 第 4 层  容器内核级（仅批量扫描形态启用，最强）                │
│   非 root UID 10001 + deny-by-default seccomp                │
│   + 可选 AppArmor + Landlock 回退 + 只读根文件系统            │
├─────────────────────────────────────────────────────────────┤
│ 第 3 层  凭据 / 进程级隔离（所有扫描启用，防泄漏）             │
│   隔离凭据目录（0700/拒符号链接/dev-inode 固定）              │
│   + MCP env 白名单 + python -I -B + 可信 PATH + git 禁 hooks │
├─────────────────────────────────────────────────────────────┤
│ 第 2 层  能力 Preflight（扫描前的就绪检查）                    │
│   capability-profiles.toml：delegated_workers / goals /      │
│   多 Agent 容量 / max_depth 是否达标，不达标给 remediation    │
├─────────────────────────────────────────────────────────────┤
│ 第 1 层  配置层沙箱（每次扫描的核心边界）                      │
│   codex_security_scan 权限 profile：                         │
│   :root 只读、:workspace_roots 可写、状态目录可写、凭据只读    │
│   + allow_login_shell:false + 删除用户 sandbox_mode          │
└─────────────────────────────────────────────────────────────┘
```

越往下越基础、每次扫描必有；越往上越强、只在攻击面最大的形态启用。

## 3. 第 1 层：配置层沙箱（`codex_security_scan`）

这是所有扫描的核心边界，实现于 `scanRuntimeCodexConfig()`（`sdk/typescript/src/api.ts`，profile 常量 `codex_security_scan` 定义于同文件）：

```ts
delete hardened["sandbox_mode"];               // 不许用户自定义沙箱模式
allow_login_shell: false,                      // 禁登录 shell
default_permissions: SCAN_PERMISSION_PROFILE,  // = "codex_security_scan"
permissions: {
  "codex_security_scan": {
    filesystem: {
      ":root": "read",                        // 系统根目录只读
      ":workspace_roots": "write",            // 扫描目标工作区可写
      [stateDirectory]: "write",              // workbench SQLite 目录可写
      [protectedCredentialHome]: "read",      // 受保护凭据目录只读
    },
  },
},
```

三个关键点：

1. **白名单式**：profile 只显式放行四类路径，其余默认拒绝——agent 能写的只有"它该写的"（工作区临时产物 + 状态库）。
2. **`allow_login_shell: false`**：agent 不能用登录 shell，压缩了"借 shell 逃逸"的通道。
3. **`delete hardened["sandbox_mode"]`**：用户配置里带来的 `sandbox_mode` 会被**强制移除**——沙箱模式由系统钉死，用户不能换成更宽松的模式。

这一层配合第 2 章的 `approvalPolicy: "never"`（不弹审批）成立：因为能力已经在配置层被收敛到最小集，所以**不需要运行中询问用户**。安全边界是"预置"的，不是"运行时问出来的"。

## 4. 第 2 层：能力 Preflight（`capability-profiles.toml`）

`_bundled_plugin/preflight/capability-profiles.toml` 定义三个 profile：`security_scan` / `deep_security_scan` / `security_diff_scan`，通过 `[[routes]]` 按 skill 路由。它检查的是**运行时能力**是否满足安全执行扫描的需求：

| capability | 含义 |
|-----------|------|
| `delegated_workers` | 是否有委托 worker 可用（影响发现的覆盖路径） |
| `goal_tools` / `goals_enabled` | 长扫描能否保住完成标准、可恢复 |
| `usable_worker_slots_6/8` | 多 Agent 并发容量是否 ≥6/8 线程 |
| `agent_depth_2` | `agents.max_depth` 是否支持 ≥2 层 |

每条要求带 `severity`（warn / suggest）与 `reason`，并给 remediation 补丁（如 `features.goals = true`）。**它不是沙箱**，而是"沙箱前置检查"：在能力不足的环境里，agent 要么走更窄的路径（`severity: warn`），要么被提示调整配置，避免"环境撑不起完整扫描流程却硬跑，结果不可靠"。

## 5. 第 3 层：凭据 / 进程级隔离（防泄漏）

沙箱之外的"配套隔离"，目标是**agent 碰不到不该碰的敏感资源**：

| 机制 | 证据（文件级） | 防什么 |
|------|---------------|--------|
| 隔离凭据目录：mode 0700、**拒符号链接、dev/inode 固定**（防 TOCTOU）、Windows SDDL/icacls | `runtime.ts` 的 `requireSecureCredentialHome()` | agent 读到宿主/CI 全局凭据 |
| MCP env 白名单（只放行 `CODEX_*`/`AWS_*`/代理等） | `_bundled_plugin/.mcp.json` 的 `env_vars` | 敏感环境变量被带进 agent 上下文 |
| 工作台 Python 用 `python -I -B`（隔离模式 + 禁字节码缓存） | `runtime.ts` 的 `runWorkbench()` | site-packages 投毒、状态目录旁被污染 |
| 可信 PATH 定位 codex/git/python | `trusted-executable.ts` 的 `resolveTrustedExecutable()` | PATH 劫持（当前目录同名恶意脚本） |
| git 检出禁 hooks、`GIT_TERMINAL_PROMPT=0` | `multiscan.ts` 的 `checkoutRevision()` | 仓库自带 git hook 执行恶意代码 |

## 6. 第 4 层：容器内核级沙箱（批量扫描）

批量扫描把**一堆外部仓库的不可信代码**喂给 agent，是攻击面最大的形态，所以容器形态（`Dockerfile`、`compose.yaml`、`docker/`）再叠内核级防线：

| 机制 | 证据 | 效果 |
|------|------|------|
| 非 root | `compose.yaml` 的 `user: ${CODEX_SECURITY_USER:-10001:10001}` | 逃逸后的权限最低化 |
| **deny-by-default seccomp** | `docker/codex-security-seccomp.json` 的 `defaultAction: "SCMP_ACT_ERRNO"` + 显式 syscall 白名单 | 未白名单的系统调用**默认被拒**（不是黑名单兜底） |
| 可选 AppArmor | `compose.apparmor.yaml` 的 `apparmor=codex-security-container`；`docker/codex-security.apparmor` 里 `deny @{PROC}/* w` 等 | 禁止写 /proc 等敏感内核路径，纵深防御 |
| **Landlock 回退** | `docker/entrypoint.sh`：受限 Ubuntu 主机（AppArmor 非 enforce）自动注入 `--codex features.use_legacy_landlock=true` | 无 seccomp/AppArmor 环境用 Landlock 兜底，跨环境可用 |
| 只读根文件系统 | `compose.yaml` 的 `read_only: true` | 容器内文件系统默认不可写 |
| 凭据白名单 | `docker/git-credential.sh`（credential helper 只认白名单 host） | agent 无法向任意 host 泄漏凭据 |

注意 seccomp 的策略方向：`SCMP_ACT_ERRNO` 意味着**默认拒绝一切**，只有白名单里的 syscall 放行——这是"明确允许"哲学（见第 8 章）在内核层的体现，而不是传统的"黑名单禁几个危险 syscall"。

## 7. 四层如何协同：什么形态启用哪几层

| 形态 | 启用的沙箱层 |
|------|-------------|
| 单仓库/路径标准扫描 | 第 1 层（配置）+ 第 3 层（凭据/进程） |
| deep 扫描 | 同上（deep 的并行发生在 Codex 会话内，不改变沙箱层） |
| 批量容器扫描 | 第 1 + 2 + 3 层全部，再叠第 4 层（内核级） |

纵深防御的意义：**单层失效不致命**。配置层出错（比如某个路径漏收敛）时，还有凭据隔离挡着泄漏；容器里逃逸出一个进程时，还有非 root + seccomp 白名单挡着内核访问。每一层都假定上层可能被攻破。

## 8. 与第 6、8 章的关系

| 章节 | 侧重 | 与本章的分工 |
|------|------|-------------|
| 第 6 章「容器化批量扫描」 | 容器安全基线的机制细节 | 本章只讲"第 4 层是什么"，机制细节见第 6 章 |
| 第 8 章「安全加固」 | 凭据/配置/分发加固 | 本章的"第 1、3 层"是第 8 章内容在"沙箱"视角下的重排 |
| 本章 | 把各层统一成"纵深防御体系" | 回答"项目有没有沙箱、沙箱怎么构成" |

## 9. 关键实现入口

| 职责 | 位置 |
|------|------|
| 配置层沙箱（权限 profile） | `sdk/typescript/src/api.ts` 的 `scanRuntimeCodexConfig()`（profile 常量 `codex_security_scan` 在同文件） |
| 能力 preflight | `_bundled_plugin/preflight/capability-profiles.toml` |
| 凭据目录隔离 | `sdk/typescript/src/runtime.ts` 的 `requireSecureCredentialHome()` |
| env 白名单 | `_bundled_plugin/.mcp.json` 的 `env_vars` |
| 可信 PATH | `sdk/typescript/src/trusted-executable.ts` |
| seccomp 策略 | `docker/codex-security-seccomp.json` |
| AppArmor profile | `docker/codex-security.apparmor`（启用见 `compose.apparmor.yaml`） |
| Landlock 回退 | `docker/entrypoint.sh` |
| 非 root / 只读 fs | `compose.yaml` |
| git 凭据白名单 | `docker/git-credential.sh`（TS 侧见 `multiscan.ts` 的 `buildGitHubCredentialArgs()`） |

## 小结

这个项目的沙箱不是单一机制，而是**四层纵深**：配置层权限 profile 白名单式收敛文件系统（每次扫描必有），能力 preflight 确保环境够格，凭据/进程级隔离挡住泄漏，容器内核级（seccomp/AppArmor/Landlock + 非 root）在攻击面最大的批量形态里兜底。因为沙箱边界是"预置"的，系统才敢在 `approvalPolicy: "never"` 下全自动跑扫描——**安全不是靠运行时问人，而是靠层层白名单把 agent 的世界钉死**。

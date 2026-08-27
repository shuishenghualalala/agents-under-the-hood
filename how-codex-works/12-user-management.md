# 第 12 章：用户管理 — 身份认证、工作空间、数据隔离

> 本章导读：Codex CLI 是一个本地优先的命令行工具，但它也支持登录 OpenAI 账户以获得云端能力。本章解释用户的**身份如何建立与验证**、**本地数据存放在哪里**、**工作空间目录结构是怎样的**，以及**多用户/多项目之间的隔离机制**。

## 12.1 设计目标

Codex 的用户管理围绕以下目标设计：

| # | 目标 | 说明 |
|---|------|------|
| 1 | **本地优先** | 未登录也能使用完整本地功能，登录只解锁云端能力 |
| 2 | **多身份源** | 支持 OpenAI OAuth 登录、API Key、Agent Identity JWT 三种认证方式 |
| 3 | **单目录原则** | 所有用户数据集中在 `~/.codex`，环境变量 `CODEX_HOME` 可覆盖 |
| 4 | **项目级隔离** | 每个项目通过 `.codex/config.toml` 拥有独立的配置层 |
| 5 | **凭证安全** | 认证令牌可存储在系统密钥链（Keychain）或本地加密文件中 |

## 12.2 全景架构

```text
┌──────────────────────────────────────────────────────┐
│                   认证方式                              │
│  ┌─────────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ OAuth 登录   │  │ API Key  │  │ Agent Identity   │ │
│  │ (浏览器授权) │  │ (环境变量)│  │ (JWT / Ed25519)  │ │
│  └──────┬──────┘  └────┬─────┘  └────────┬─────────┘ │
│         │              │                  │            │
│         ▼              ▼                  ▼            │
│  ┌──────────────────────────────────────────────────┐  │
│  │               AuthManager                         │  │
│  │  - 统一凭证管理                                   │  │
│  │  - Token 刷新                                     │  │
│  │  - 多种存储后端（Keyring / File）                  │  │
│  └────────────────────┬─────────────────────────────┘  │
│                       │                                  │
│                       ▼                                  │
│  ┌──────────────────────────────────────────────────┐  │
│  │               ~/.codex/                            │  │
│  │  auth.json    → 认证凭证                           │  │
│  │  config.toml  → 用户配置                           │  │
│  │  state_5.sqlite → 会话元数据                       │  │
│  │  memories_1.sqlite → 记忆数据                      │  │
│  │  ...                                               │  │
│  └──────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

## 12.3 用户身份认证

### 12.3.1 三种认证机制

#### A. OAuth 登录（`codex login`）

登录流程由 `codex-rs/login/` crate 实现：

1. **启动本地 HTTP 服务器** (`server.rs`) 监听随机端口
2. **打开浏览器** 跳转到 OpenAI OAuth 授权页
3. **接收回调** — OpenAI 通过重定向将授权码发回本地服务器
4. **交换 Token** — 用授权码 + PKCE 换取 access_token / refresh_token
5. **持久化凭证** — 写入 `auth.json` 和/或系统密钥链

关键文件：
- `codex-rs/login/src/server.rs` — OAuth 本地服务器
- `codex-rs/login/src/device_code_auth.rs` — 设备码登录（无浏览器场景）
- `codex-rs/login/src/auth/manager.rs` — AuthManager 核心逻辑
- `codex-rs/login/src/auth/storage.rs` — 凭证持久化

#### B. API Key 认证

支持三种环境变量：
- `CODEX_ACCESS_TOKEN` — 直接使用的 access token
- `OPENAI_API_KEY` — OpenAI API key
- `CODEX_API_KEY` — Codex 专用 API key

API Key 登录调用 `login_with_api_key()` / `login_with_access_token()`，将凭证存储到 `auth.json`。

#### C. Agent Identity（JWT + Ed25519）

这是 Codex App Server 模式使用的认证方式（`codex-rs/agent-identity/` crate）：

1. 生成 Ed25519 密钥对
2. 用私钥签名 Agent Identity 断言
3. 向 OpenAI Auth API 注册 Agent，获得 `agent_runtime_id`
4. 后续请求携带 JWT，包含 `account_id`、`chatgpt_user_id`、`plan_type` 等声明

关键结构：
```rust
pub struct AgentIdentityJwtClaims {
    pub iss: String,           // https://chatgpt.com/codex-backend/agent-identity
    pub aud: String,           // codex-app-server
    pub agent_runtime_id: String,
    pub account_id: String,
    pub chatgpt_user_id: String,
    pub email: Option<String>,
    pub plan_type: AuthPlanType,
}
```

### 12.3.2 凭证存储

凭证存储在 `$CODEX_HOME/auth.json`，结构如下：

```rust
pub struct AuthDotJson {
    pub auth_mode: Option<AuthMode>,          // oauth | api_key | agent_identity
    pub tokens: Option<TokenData>,             // access_token, refresh_token
    pub agent_identity: Option<AgentIdentityStorage>,
    pub personal_access_token: Option<String>,
    pub bedrock_api_key: Option<BedrockApiKeyAuth>,
    // 可选字段：last_refresh, openai_api_key 等
}
```

存储后端策略：
- `AuthCredentialsStoreMode::Keyring`（默认）— 使用系统密钥链（macOS Keychain / Linux Secret Service）
- `AuthCredentialsStoreMode::File` — 明文存储在 `auth.json` 中
- 回退逻辑：Keyring 不可用时自动降级到 File

## 12.4 工作空间：`CODEX_HOME` 目录结构

### 12.4.1 定位规则

```
CODEX_HOME 环境变量 → 如果设置且指向存在的目录，则使用该目录
否则 → ~/.codex/（默认）
```

实现位于 `codex-rs/utils/home-dir/src/lib.rs` 的 `find_codex_home()` 函数。

### 12.4.2 目录内容

```
~/.codex/                          ← CODEX_HOME
├── config.toml                    ← 用户级全局配置
├── auth.json                      ← 认证凭证
├── managed_config.toml            ← 企业 MDM 托管配置（macOS）
├── AGENTS.md                      ← 全局 Agent 指令
├── AGENTS.override.md             ← 本地覆盖指令
├── state_5.sqlite                 ← 会话元数据（ThreadMetadata）
├── logs_2.sqlite                  ← 运行日志
├── goals_1.sqlite                 ← 目标管理
├── memories_1.sqlite              ← 记忆持久化
├── queue_1.sqlite                 ← 用户提交队列
├── thread_history_1.sqlite        ← 会话历史
├── memories/                      ← 记忆文件目录
├── plugins/                       ← 插件目录
│   └── <plugin-name>/
├── skills/                        ← Skills 目录
└── environments/                  ← 环境配置
```

### 12.4.3 配置分层（Config Layer Stack）

Codex 的配置是分层叠加的，从通用到具体：

```
 1. System Config       → 系统级默认值（内嵌二进制）
 2. Managed Config      → 企业 MDM 托管配置（macOS 偏好设置）
 3. User Config         → ~/.codex/config.toml
 4. Project Config      → <project-root>/.codex/config.toml
 5. CLI Overrides       → 命令行参数（--model, --profile 等）
 6. Thread Config       → 当前会话的运行时配置
```

每一层会覆盖上一层相同键的值。实现位于 `codex-rs/config/src/loader/mod.rs`。

### 12.4.4 项目级配置

项目可以在其根目录下创建 `.codex/config.toml` 来覆盖全局配置：

```
<project-root>/
└── .codex/
    └── config.toml       ← 项目级配置（工具权限、模型选择等）
```

检测逻辑（`codex-rs/config/src/loader/mod.rs:1226`）：从当前工作目录向上遍历，寻找 `.codex/config.toml`，同时检查 `git rev-parse --show-toplevel` 返回的仓库根目录。

## 12.5 数据隔离机制

### 12.5.1 用户级隔离

| 隔离维度 | 实现方式 |
|----------|----------|
| 文件系统 | 每个用户拥有独立的 `~/.codex/` 目录，由 `CODEX_HOME` 环境变量控制 |
| 凭证 | `auth.json` 绑定到单个用户，切换用户需重新登录 |
| SQLite 数据库 | 所有数据库文件（state/logs/memories/goals/queue）位于 `CODEX_HOME` 下，天然按用户隔离 |

### 12.5.2 项目级隔离

- 每个项目通过 `.codex/config.toml` 拥有独立的**配置视图**
- 配置合并时，项目层覆盖用户层 → 不同项目可以使用不同的工具、模型、权限策略
- 会话元数据（ThreadMetadata）包含 `cwd` 字段，可按工作目录过滤会话列表

### 12.5.3 会话级隔离

- 每个 Thread（会话）有唯一的 `ThreadId`
- 会话元数据存储在 `state_5.sqlite` 的 `threads` 表中
- 会话之间不共享运行时状态
- 会话可通过 fork 创建派生会话，但 fork 链记录在 `thread_spawn_edges` 表中

### 12.5.4 沙箱隔离（执行级）

对于命令执行，Codex 使用多层次的执行沙箱：
- **Linux**: Bubblewrap（用户命名空间 + 挂载命名空间）
- **macOS**: Seatbelt sandbox profiles
- **Windows**: Windows Sandbox / AppContainer

这些沙箱机制在进程级别隔离用户的操作，详见第 7 章（待写）。

## 12.6 关键实现入口

| 功能 | 模块 | 关键文件 |
|------|------|----------|
| Codex Home 定位 | `codex-rs/utils/home-dir` | `src/lib.rs` |
| Agent Identity | `codex-rs/agent-identity` | `src/lib.rs` |
| OAuth 登录 | `codex-rs/login` | `src/server.rs` |
| 凭证管理 | `codex-rs/login/src/auth` | `manager.rs`, `storage.rs` |
| 配置分层加载 | `codex-rs/config` | `src/loader/mod.rs` |
| 状态数据库 | `codex-rs/state` | `src/sqlite.rs`, `src/runtime.rs` |
| 会话存储 | `codex-rs/thread-store` | `src/local/mod.rs` |
| 记忆系统 | `codex-rs/memories` | `read/src/lib.rs`, `write/` |
| 密钥链存储 | `codex-rs/keyring-store` | `src/lib.rs` |
| 秘密管理 | `codex-rs/secrets` | `src/lib.rs`, `src/local.rs` |

## 12.7 小结

Codex 的用户管理体系围绕"本地优先 + 可选云端"设计：

1. **认证** — 三种方式（OAuth、API Key、Agent Identity）覆盖从个人用户到企业托管的不同场景
2. **工作空间** — 单一 `CODEX_HOME` 目录承载所有用户数据，通过 `CODEX_HOME` 环境变量可完全重定位
3. **数据存储** — 六类 SQLite 数据库 + 文件系统目录，覆盖会话元数据、日志、记忆、目标、队列
4. **隔离** — 用户级（独立目录）、项目级（配置分层覆盖）、会话级（Thread ID + 独立状态）、执行级（沙箱）

这种设计让 Codex 既能作为纯本地 CLI 工具使用，也能无缝对接 OpenAI 的云端能力，同时保持用户数据的可移植性和可管理性。

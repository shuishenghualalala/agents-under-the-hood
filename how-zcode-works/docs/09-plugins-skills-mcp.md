# 09. 插件、技能与 MCP：一个目录约定撑起的扩展生态

> **文档同步跟踪**
> - 最后同步代码：`ZCode` commit `872ad960de`（2026-09-21）
> - 同步方式：基于该文档撰写时的源码路径梳理，关键论断已回到源码抽查

ZCode 的扩展体系没有发明新格式：插件是 `plugin.json` + 约定目录，技能是带 frontmatter 的 markdown，MCP 是行业标准协议。它的设计重心不在"格式"而在**信任模型**——四种来源（内联、官方、商店缓存、远程镜像）的插件怎么被发现、第三方目录里的 hook 怎么获得信任、插件 MCP 服务器怎么防止仿冒官方、技能扫描在哪里拒绝符号链接。读完你会理解五类插件贡献物、技能的三级根解析、MCP 的三种连接与 OAuth 栈、以及扩展点与第 03 章权限系统的咬合方式。

本章结构如下——先看插件（9.1-9.3），再看技能与命令（9.4-9.5），然后是 MCP（9.6），最后收在全体系的信任模型与取舍（9.7-9.8）：

```mermaid
flowchart LR
    A["9.1 设计目标"] --> B["9.2 插件发现与装载"]
    B --> C["9.3 插件商店"]
    C --> D["9.4 技能"]
    D --> E["9.5 自定义命令"]
    E --> F["9.6 MCP"]
    F --> G["9.7 信任模型"]
    G --> H["9.8-9.10 取舍与小结"]
```

## 9.1 设计目标

扩展体系围绕三个目标设计。

**第一，生态兼容优先。** 插件 manifest 认三个目录名——自己的 `.zcode-plugin/`，外加 `.claude-plugin/` 与 `.codex-plugin/` 两个兼容目录；用户技能根同时认 `~/.zcode/skills` 与 `~/.agents/skills`；MCP 配置兼容 `~/.agents/mcp.json`。既有生态的资产可以近乎零成本迁入。

**第二，扩展物一律走主通道。** 插件贡献的 agent、命令、技能、hook、MCP 服务器，装载后分别汇入第 02-03 章已经拆过的主通道——注册表、钩子运行器、权限服务。扩展体系自己没有第二条执行路径。

**第三，信任要具体，不要笼统。** "项目目录里的东西不可信"太粗。ZCode 的信任判定精确到机制：hook 按声明摘要逐个信任、插件 MCP 的 `official` 出身宿主强制改写、CUA 权威看注册表身份不看名字、插件技能扫描拒绝符号链接。每个边界都有对应的注入手段被堵死。

## 9.2 插件：发现、装载与五类贡献物

**manifest 与贡献物**。插件根下 `plugin.json` 声明能力，可贡献的东西恰好五类：

| 贡献物 | 来源 | 装载后去向 |
|--------|------|-----------|
| agent（子代理 profile） | `agents/` 目录 + manifest 声明 | 第 04 章 profile 体系 |
| command（slash 命令） | `commands/` 目录 + manifest | 自定义命令表（9.5） |
| skill | `skills/` 目录 + manifest | 技能表，命名空间化为 `plugin:skill` |
| hook | manifest hooks 声明 | 钩子运行器（第 03 章），需过工作区信任门 |
| MCP 服务器 | manifest `mcpServers` + 根下 `.mcp.json` | MCP 配置合并，命名空间化为 `plugin:<name>:<server>` |

manifest 明确列出**声明了也不支持**的键（channels、lspServers、outputStyles、settings）——解析器直接拒绝，避免"以为配置了其实没生效"的静默失败。

**四个发现来源**，各有默认信任级：

```text
config.plugins.dirs 显式配置目录   → inline 来源，默认启用
随包捆绑的官方插件根              → official 来源，默认禁用（白名单例外）
官方市场缓存扫描                  → official 来源
installed_plugins.json 安装记录    → cache 来源（用户主动安装的）
```

插件稳定 ID 是 `名字@市场`，官方市场有固定 ID；用户配置可以按 ID 启停、给插件传选项（带类型与敏感级标注的 userConfig）。卸载官方内置插件只写"抑制"记录不删文件——恢复路径随时可以把官方插件重新种回来。

**市场的六种来源**：url、github、git、npm、file、directory。git/github 来源用稀疏检出只拉子目录，市场清单缓存到本地存储，安装/更新/卸载/启停全部走统一的 RPC 服务暴露给各宿主（桌面商店 UI 与 CLI 命令共用同一套操作）。远程工作区还有一层 plugin-sync：把已装插件打包镜像到远程主机，按插件 ID 去重、路径包含校验防逃逸。

## 9.3 插件商店

商店是市场机制的 UI 面：桌面设置里有完整的商店列表视图——搜索、已安装条、公开/个人分区、精选位、分类折叠。商店条目是市场清单顶层的策展数据（featured 列表），加上每个插件的 listing 元数据契约：展示名（带 i18n）、图标、分类、作者、主页、隐私政策、示例 prompt、是否要求付费套餐。官方插件通过内置定义表种子化商店条目（CDN 图标 + i18n 键），内容型官方插件（如技能生成器）默认启用。

关键分工：**市场清单管"有哪些、怎么装"，listing 契约管"怎么展示"**——两者解耦后，第三方市场不需要理解商店 UI 的展示逻辑，只提供标准 manifest 即可上架。

## 9.4 技能：SKILL.md 的三级解析

技能是一个含 `SKILL.md` 的目录，frontmatter 三件事：`name`（缺失时回退目录名）、`description`（必填，**1024 字符上限**）、可选 `when_to_use`。正文默认 **100KB 上限**。少数"安全键"（name/description/when_to_use/license/metadata）决定技能能否自动加载，未知键不告警也不拦。

**根解析是三级且逐层向上**：

```text
1. 配置的额外根
2. 用户级：~/.zcode/skills + ~/.agents/skills（兼容）
3. 项目级：从 cwd 逐层向上到 worktree 根，
   每层的 .zcode/skills + .agents/skills
4. 插件技能根（命名空间化 plugin:skill）
```

项目级逐层向上的语义与 `.gitignore` 的直觉一致：monorepo 的子包可以有自己的技能，根仓库的技能全局可见。按路径可以禁用单个技能（skillOverrides）。

**符号链接信任边界**是不对称的，这是本节最重要的安全细节：用户级根**跟随**符号链接（这是受支持的导入方式），插件作用域的扫描**从不跟随**——目录级、子目录级、文件级一律拒绝。原因直白：用户自己放的链接代表用户意图，插件市场拉下来的代码里的链接不代表。

**到模型的通路**有两条：一是 Skill 工具（入参技能名 + 可选参数），返回 `<skill_content>` 包裹的正文与基目录，支持 `${ZCODE_SKILL_DIR}` 等变量展开；二是**技能清单注入**——可用技能的名字 + 描述（每条 250 字符封顶、总预算 20K 字符，超限降级为只列名字）作为 meta-user 附件进上下文，而不是塞进静态 system prompt。模型"知道有哪些技能"靠清单，"用某个技能"靠调工具。

## 9.5 自定义命令与 slash 体系

自定义命令与技能同构：markdown 文件 + frontmatter（`allowed-tools`、`argument-hint`、`description`），根目录解析与技能完全镜像（`~/.zcode/commands`、逐层 `.zcode/commands`、插件 commands）。

**shell 展开**是命令的独特能力：正文里的行内反引号 `` !`cmd` `` 或 ``` ```! ``` 围栏块会在发送前执行替换——**30 秒超时、输出 128KiB 封顶**，支持 `${ZCODE_SESSION_ID}`、`${ZCODE_PLUGIN_ROOT}` 等上下文变量。这让命令可以预取环境信息（当前分支、测试清单）拼进 prompt。

保留名规则：内置命令名与别名、`compress`、`plan` 不允许被自定义命令占用；对协议层可见的内置 prompt 命令只有 `/init`（生成写 AGENTS.md 的 prompt）。另外有一个容易混淆的点被模型可见的说明主动澄清：用户输入的 `/<词>` 如果命中的是技能名，会路由到 Skill 工具——**slash 面是命令与技能共用的入口**。

## 9.6 MCP：三种连接、完整 OAuth、同步服务

**配置形状**：stdio（command/args/cwd/env）、Streamable HTTP（url/headers/oauth）、SSE（同前）三种服务器配置；公共字段有启停、`isolation`（session 级或 workspace 级）、协议版本、超时。宿主内部的来源与出身字段对公共配置**拒绝注入**——外部配置文件不能声称自己是官方服务器。

**工具桥接**在核心侧极薄：描述符统一命名 `mcp__<server>__<tool>`（非法字符清洗为下划线）注册进主注册表，安全标注从 annotations 映射（readOnlyHint→低风险、destructiveHint→高风险、其余中风险），默认强制审批 + network 域（第 03 章已详述）。服务器的连接、进程池、传输实现在适配器层。

**OAuth 栈是完整实现**而非占位：授权码流程（内存 PKCE、动态客户端注册、刷新令牌）、client_credentials、按服务器/URL/客户端/作用域哈希的凭据存储、租约与刷新。官方 MCP 服务另有凭据通道（平台登录 JWT + 编码套餐门控），下发失败有结构化的 `official_*` 原因码与发放审计。

**mcp-sync 服务**管配置的跨机同步：读写用户级 MCP 配置（`~/.zcode/cli/config.json` 的 mcp.servers 键、`.agents/mcp.json` 兼容）、列出本地候选、主机间导入导出去重；真实连接与工具列举委托给 Agent 进程做——配置服务不自己养连接。

## 9.7 信任模型：每个边界一个具体机制

扩展体系的信任设计值得逐条列出，因为每条都对应一个**具体的攻击面**：

| 边界 | 机制 | 堵住的注入手段 |
|------|------|--------------|
| 项目 hook 默认不可信 | 信任存储按工作区身份 + 每条 hook 的声明摘要记录；信任状态持久化；存储损坏**失败关闭** | 克隆陌生仓库自带 hook 自动执行 |
| 项目 MCP 服务器 | 未显式连接过的项目级服务器状态为 untrusted，使用前必须显式连接 | `.zcode/` 里塞 MCP 服务器静默生效 |
| 插件 MCP 的官方出身 | 宿主**改写**出身字段；插件 `.mcp.json` 写的 `official` 无效 | 插件自称官方服务器骗凭据 |
| CUA 权威判定 | 从内存插件注册表解析（配置对象身份 + 适配器写的权威 env），**绝不按名字通配**；源码注释原话："server/tool 名可以被第三方仿冒，因此绝不能用名称 wildcard 表达这一权限" | 起一个叫 `computer-use` 的假服务器拿 CUA 权限 |
| 插件技能符号链接 | 插件作用域扫描全拒绝 | 商店插件经链接逃逸出技能根 |
| 命令 shell 展开 | 30s 超时 + 128KiB 输出上限 + 上下文变量白名单 | 命令展开时执行任意长任务/巨量输出 |
| 子代理 MCP 通配 | 允许规则形如 `mcp__server__*`，按服务器维度收敛 | 逐工具放行导致的规则爆炸与遗漏 |

这张表的整体哲学：**权限决定"能做什么"，信任决定"配置是否算数"**——两套机制正交，缺一不可。

## 9.8 设计取舍

**为什么认 Claude/Codex 的插件目录名？**
扩展生态的冷启动靠兼容而非迁民。目录名兼容的成本接近零（就是多扫两个路径），收益是存量插件与技能可以直接被 ZCode 加载。这是把"格式战争"直接跳过的实用主义。

**为什么技能清单进 meta-user 附件而不进 system prompt？**
技能列表随安装变化，进静态 system prompt 会污染缓存前缀（第 02 章的缓存分段）；meta-user 附件在缓存边界之外，加删技能不影响 stable 段。20K 预算与降级到只列名字，则是承认"清单本身也可能撑爆上下文"。

**为什么 hook 信任按"声明摘要"逐条记，而不是按项目一次性信任？**
项目代码是会变的：今天信任了 `.zcode/hooks/` 里的三条 hook，下周 pull 下来第四条——如果信任按项目粒度，新 hook 自动获得执行权。按声明摘要逐条记，任何 hook 内容变更都会产生新摘要、回到待信任状态。粒度选在"内容"而不是"位置"。

**为什么插件 MCP 要命名空间化三层（plugin:name:server）？**
命名冲突在扩展体系里是权限漏洞：两个插件都叫 `git-helper` 的 MCP 服务器，其中一个的放行规则会命中另一个。全链路命名空间化（技能、MCP、命令同理）让"谁的东西"编码在名字里，规则匹配天然按来源隔离。

**为什么 mcp-sync 把真实连接委托给 Agent 进程？**
连接状态（进程池、OAuth 租约）的生命周期属于 Agent 运行时。配置服务如果自己养连接，就会出现"配置说连了、运行时没连"的双头状态。委托一次，真相一份。

## 9.9 关键实现入口

| 责任 | 主要文件 |
|------|------|
| 插件发现/装载/贡献物枚举 | `apps/zcode-cli/packages/adapters/src/plugins/index.ts`、`plugin-components.ts`、`mcp.ts` |
| 市场清单与安装记录 | `apps/zcode-cli/packages/adapters/src/plugins/marketplace.ts` |
| 插件操作（装/卸/启停/商店总览） | `apps/zcode-cli/packages/bootstrap/src/plugins.ts` |
| 官方插件定义与捆绑 | `apps/zcode-cli/packages/bootstrap/src/app/official-plugin-definitions.ts`、`bundled-plugins.ts` |
| 插件 RPC 服务 | `packages/services/src/plugins/plugins.ts` |
| 插件远程镜像 | `packages/services/src/plugin-sync/pluginSyncService.ts` |
| 技能解析/扫描/根 | `apps/zcode-cli/packages/adapters/src/skills/index.ts`、`scan.ts`、`roots.ts` |
| Skill 工具与清单注入 | `apps/zcode-cli/packages/core/src/tool/handlers/skill.ts`、`core/src/context/sections/skills.ts` |
| 自定义命令与 shell 展开 | `apps/zcode-cli/packages/bootstrap/src/custom-commands.ts`、`custom-command-shell-expansion.ts` |
| slash 保留名 | `apps/zcode-cli/packages/bootstrap/src/slash-command-surface.ts` |
| MCP 配置契约 | `apps/zcode-cli/packages/contracts/src/interfaces/mcp.port.ts` |
| MCP 工具注册与命名 | `apps/zcode-cli/packages/core/src/mcp/index.ts`、`name.ts` |
| MCP 传输与 OAuth | `apps/zcode-cli/packages/adapters/src/mcp/`（`oauth-interactive.ts` 等） |
| 官方 MCP 凭据 | `packages/services/src/official-mcp/officialMcpCredentials.ts` |
| MCP 配置同步 | `packages/services/src/mcp-sync/mcpSyncService.ts` |
| hook 信任存储 | `apps/zcode-cli/packages/bootstrap/src/workspace-hook-trust-cli.ts` |
| 商店 UI | `packages/ui/src/settings/PluginStoreListView.tsx` 等 |

## 9.10 小结

扩展体系可以概括为"**约定目录 + 主通道汇入 + 逐边界信任**"：

- 插件：五类贡献物（agent/command/skill/hook/MCP）全部汇入既有主通道，四个发现来源 + 六种市场来源 + 商店 UI，兼容 Claude/Codex 目录约定；
- 技能：SKILL.md 三级逐层解析，清单走 meta-user 注入、调用走 Skill 工具，插件作用域拒绝符号链接；
- MCP：三种连接 + 完整 OAuth + 配置同步，工具统一命名进主注册表、默认强审批；
- 信任：hook 按声明摘要、MCP 按显式连接、官方出身宿主改写、权威判定看身份不看名字——每条边界堵一个具体注入手段。

至此九章拆解收束。回头看全书主线：第 01 章的"一份内核三种宿主"在第 02-03 章落成运行时与工具管道，在第 04-06 章长出多智能体、压缩与记忆，在第 07-08 章被桌面与远程宿主点亮，最终在第 09 章向生态开放——而开放的方向，始终被同一条权限与信任的边界约束着。

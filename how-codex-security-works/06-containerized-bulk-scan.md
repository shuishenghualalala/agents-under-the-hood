# 第 6 章：容器化批量扫描

## 本章导读

读完这一章，你会知道：

1. `bulk-scan` 解决什么问题，与单仓库扫描是什么关系；
2. CSV 清单的校验纪律（不可变 Git SHA）；
3. 批量扫描的恢复机制（ledger / 幂等 / 输出锁）；
4. 并行 worker 与 git checkout 的隔离策略；
5. 容器镜像与运行时的安全基线（非 root、seccomp、AppArmor、Landlock）。

## 1. 它解决什么问题

单仓库扫描解决"扫描一个仓库"。批量扫描（`bulk-scan`）解决"**对一批仓库的固定版本做可重复、可恢复、可并行的扫描**"——典型场景是组织级供应链审计：把一批仓库的特定提交列进清单，逐个检出、扫描、汇总，中途断了能续上，结果可追溯。

它对外暴露两个形态：

| 形态 | 入口 | 适用 |
|------|------|------|
| 裸机批量 | `codex-security bulk-scan --inventory inventory.csv ...` | 已有多机/CI runner |
| 容器化批量 | `docker run` + `compose.yaml`（+ `compose.apparmor.yaml`） | 统一环境、安全基线 |

核心编排在 `multiscan.ts` 的 `runMultiscan()`：对清单里的每个仓库，**各自跑一次完整的 `CodexSecurity.run()`**（复用第 3 章的单扫描流程），再汇总。所以 bulk-scan 不是新引擎，是"单扫描引擎 + 编排层"。

## 2. CSV 清单的校验纪律

`parseInventory()`（`multiscan.ts`）对输入极严格，这是"可追溯"的地基：

| 字段 | 校验 |
|------|------|
| `id` | `/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/`，必须唯一（用于产物目录名） |
| `revision` | **必须是完整不可变 Git SHA**：40 或 64 位 hex——拒绝分支名、短 SHA |
| `repository` | 本地路径或 Git URL；URL 仅允许 `https:`/`ssh:`，**禁止内嵌凭据、query、fragment** |
| `scope` | 必须待在仓库内：拒绝绝对路径、`..`、反斜杠、空字节 |
| `mode` | 仅 `standard` / `deep` |

其中 `revision` 全量 SHA 这条最硬：它保证了"**这次扫的是这个提交，而不是某个会漂移的分支**"。配上检出后的二次校验（见下），产物就能声明为"针对 SHA X 的审计"。

## 3. 检出：可信 git + 版本钉死

`checkoutRevision()` 为每个仓库做一次干净检出：

1. 清空 `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` 等环境变量，防止继承宿主的 git 状态；
2. `GIT_TERMINAL_PROMPT=0`（不弹交互）、`GIT_LFS_SKIP_SMUDGE=1`（不拉 LFS）；
3. 用 `resolveTrustedExecutable("git", ...)` 在**可信 PATH** 上定位 git（避免 PATH 劫持，见第 8 章）；
4. 每条 git 命令强制 `-c core.hooksPath=/dev/null`（禁 hooks）；
5. `init` → `fetch --depth=1 --no-tags`（只取目标提交）→ `checkout --detach FETCH_HEAD`；
6. **二次校验**：`git rev-parse HEAD` 必须等于清单里的 SHA，否则抛错。

凭据上，如果给了 `--github-host`，会注入 `buildGitHubCredentialArgs()` 生成的 credential helper（`credential.<origin>.helper = !gh auth git-credential`），即**只允许向白名单 host 提供 gh 凭据**，且先清空原有 helper 再注入，杜绝凭据被任意 host 读取。

## 4. 恢复机制：ledger、幂等、输出锁

批量扫描时长可能是小时级，中断恢复必须零损失。`runMultiscan()` 用三件套实现：

### 4.1 可恢复 ledger（`results.jsonl`）

每个任务的每次尝试都会**追加一行 JSON 收据**（`status`/`attempt`/`outputDir`/`cost`/`error`），每行独立落盘并 `sync()`。重启后 `readReceipts()` 重建状态：已完成且有完整产物的任务**跳过**（`skipped`），其余进入待办。文件末尾如果有半行（崩溃时写入一半），会被截断修复。

### 4.2 幂等的产物布局

```
output/
  manifest.json          ← 任务清单（与清单严格一致，防换清单）
  results.jsonl          ← 恢复 ledger
  .lock/owner.json       ← 输出锁（pid）
  checkouts/{id}/        ← 工作检出（用完即删）
  artifacts/{id}/attempt-N/
      scan-manifest.json / findings.json / coverage.json / report.md
```

`hasArtifacts()` 要求**四件产物齐全**才算完成（`REQUIRED_ARTIFACTS`）。`ensureManifest()` 用 `flag: "wx"` 创建：已存在且内容不一致 → 报错，防止把 A 清单的结果混进 B 清单的输出目录。

### 4.3 输出锁

`acquireLock()` 用 mkdir `.lock`（原子）拿锁，锁里有 `owner.json` 记录 pid；发现锁属于**已死进程**时，把锁目录改名成随机后缀再删除（避免误删并发进程的活锁），然后重试。这把"多个 supervisor 同时写一个输出目录"的风险关掉了。

## 5. 并行 worker 与隔离

- **并行模型**：`workers` 个 worker 并发，每个 worker 持有自己的 `CodexSecurity` 实例（`createSecurity(config)`），从共享待办队列取任务。一个任务失败会按 `maxAttempts` 重试（每次 `attempt-N` 独立产物目录），重试耗尽才记 `failed`。
- **目录隔离**：每个任务用独立 `checkouts/{id}` 与 `artifacts/{id}/attempt-N`；scope 会被 realpath 校验不得逃出 checkout 根。
- **cleanup**：无论成败，任务结束都删除 checkout——避免跨任务的状态污染。

> 注意区分两类并行：**单仓库 deep 扫描**靠 Codex 原生 multi-agent v2（同会话多线程，第 4 章）；**多仓库 bulk** 靠本章的进程级 worker 并行。二者正交，可叠加。

## 6. 容器化：Dockerfile 与安全基线

`bulk-scan` 的容器化形态（`Dockerfile`、`compose.yaml`、`docker/`）把上一节的安全纪律再往上叠一层：

| 层面 | 机制 | 理由 |
|------|------|------|
| 镜像 | 两阶段构建（build → runtime，runtime 不携带构建链） | 缩小攻击面 |
| 用户 | 非 root，固定 UID 10001 | 容器内逃逸风险最小化 |
| 系统调用 | **deny-by-default seccomp**（`docker/codex-security-seccomp.json`） | 白名单式限制 syscall，而非黑名单 |
| MAC | 可选 AppArmor（`compose.apparmor.yaml` + `docker/codex-security.apparmor`） | 纵深防御，叠加在 seccomp 之上 |
| 回退 | **Landlock**（无 seccomp/AppArmor 支持的主机） | 跨环境可用性 |
| 凭据 | git credential helper 只认白名单 host（`docker/git-credential.sh`） | agent 无法向任意 host 泄漏凭据 |
| 入口 | `docker/entrypoint.sh` 做环境探测/配置注入 | 统一入口，行为可预期 |

设计理由：**批量扫描是"用不可信输入（一堆外部仓库的代码）驱动一个能写文件、能调 LLM 的 agent"**——这是攻击面最大的形态。所以安全基线的强度与风险成正比：单仓库扫描靠配置级权限收敛（第 8 章），批量容器把防线推进到内核级（seccomp/AppArmor/Landlock + 非 root）。

## 7. 取舍与失败模式

| 情况 | 行为 |
|------|------|
| 某个仓库检出失败 | 该任务按 `maxAttempts` 重试，其余任务不受影响 |
| 某个仓库扫描失败（如覆盖率不完整） | 记 `failed` 收据，继续下一个；结果汇总里有明细 |
| 中途 Ctrl-C | `signal` 贯穿，已完成的收据保留，重启可续 |
| 输出目录被别的进程占用 | 输出锁（含 stale-lock 清理）阻止并发写坏 |
| 换了清单却复用旧输出目录 | `ensureManifest` 内容比对失败 → 报错，不静默混数据 |
| 覆盖率不完整 | 任务视为失败（`completeness !== "complete"`），宁可失败不交半成品 |

## 8. 关键实现入口

| 职责 | 位置 |
|------|------|
| 批量编排 | `sdk/typescript/src/multiscan.ts` 的 `runMultiscan()` |
| CSV 解析与校验 | `multiscan.ts` 的 `parseInventory()` |
| 检出与版本钉死 | `multiscan.ts` 的 `checkoutRevision()` |
| git 凭据白名单 | `multiscan.ts` 的 `buildGitHubCredentialArgs()` |
| 恢复 ledger / 输出锁 | `multiscan.ts` 的 `readReceipts()` / `acquireLock()` |
| 容器镜像 | `Dockerfile`（两阶段） |
| 编排与 AppArmor 变体 | `compose.yaml` / `compose.apparmor.yaml` |
| 运行时脚本 | `docker/entrypoint.sh`、`docker/git-credential.sh` |
| 内核安全 | `docker/codex-security-seccomp.json`、`docker/codex-security.apparmor` |
| CI | `.github/workflows/container-ci.yml`、`container-release.yml` |

## 小结

批量扫描把单扫描引擎包装成"可恢复、可并行、可容器化"的流水线：不可变 SHA 钉死可追溯性，ledger + 幂等产物 + 输出锁保证断点续跑，多 worker 摊薄 wall-clock，内核级安全基线托底不可信输入。下一章回到每次扫描的产物本身，看契约如何让"产出即真理"。"

# 第 6 章：LLM 能力层——流式词汇表与适配器

> 本章导读：`packages/llm/` 是 harness 与模型提供方之间的 seam。本章回答：为什么 dsh 有一套自己的流式词汇表而不是透传 OpenAI 格式、适配器要遵守什么契约、一次模型请求从 agent loop 到 chunk 落日志的完整路径、以及重试与 token 计量挂在哪。建议先读[第 4 章](04-核心循环.md)（循环）与[第 5 章](05-会话日志与持久化.md)（日志）。

## 6.1 这层解决什么问题

agent loop、会话日志、UI、token 计量、压缩……所有消费方都不想关心 OpenAI/Anthropic/DeepSeek 各家的线缆协议。LLM 层的职责是把"对话历史 + 工具 schema + 采样参数"翻译成**提供方无关的流式词汇表**，让消费方只面向一套稳定契约编程，把各家差异隔离在适配器内部。

包族分工：

| 包 | 角色 |
|---|---|
| `llm/llm` | Service Definition + 共享词汇：`LlmRuntime`（`ctx.llm`）、`StreamChunk`/`Message` 类型、`BlockAssembler`、`LlmError`、重试策略 |
| `llm/llm-deepseek` | Provider：直接 fetch + SSE 的 DeepSeek 适配器（参考实现） |
| `llm/llm-pi-ai` | Provider：封装 `@earendil-works/pi-ai` 的多提供方"孪生"适配器 |
| `llm/llm-retry` | Consumer：在 `agent/request-error` 上执行重试策略 |
| `llm/token-meter` | Consumer：`ctx.tokenMeter`，从日志回放计量 |

两个适配器并存不是浪费：手写 HTTP 的 deepseek 与库封装的 pi-ai 用**两个实现共同验证协议约定**（官方笔记称之为 twin adapters），pi-ai 同时充当打开多提供方的可选后端。

## 6.2 为什么需要自有流式词汇表

这是本章最重要的设计决策。透传 OpenAI 格式看似简单，但会崩在四个地方：

1. **持久化与回放保真**：原始 chunk 逐条写入会话日志（`assistant/chunk`），日志是 fork/恢复/transcript/遥测的唯一事实源。若透传线缆格式，换提供方后历史无法统一回放。
2. **编译期完备性**：`StreamChunk` 是封闭可辨识联合，消费方 `switch` 以 `assertNever` 结尾——新增 chunk 变体时，所有消费方编译报错，没有一个处理点能被漏掉。
3. **提供方差异归适配器**：DeepSeek 的 `prompt_tokens` 含缓存命中，适配器负责扣除成"互不相交计数"；pi-ai 返回已解析的工具参数，适配器在 `block-end` 重新 stringify 回原始 JSON。消费方永远看到同一种语义。
4. **错误规范化**：抛出或带内错误统一成带稳定 code 的 `LlmFailure`，消费方按 code 路由，**绝不依赖提供方文本**。

代价是每个适配器都要做双向翻译（序列化 + chunk 翻译），且新模态（如 image）需要适配器、UI、压缩、回放四方同时支持才准入词汇表。

## 6.3 核心抽象

### StreamChunk：封闭流式协议

| chunk | 载荷 |
|---|---|
| `block-start` | `index` + `blockType` |
| `text-delta` / `reasoning-delta` | `index` + `text` |
| `tool-call-delta` | `index` + `id` + 可选 `name` + `argumentsDelta`（**原始 JSON 片段**） |
| `block-end` | `index` + 组装好的完整 `ContentBlock`（消费方无需自行拼 delta） |
| `usage` | `TokenUsage`：input/output/cacheRead/cacheWrite/reasoning，**计数互不相交** |
| `finish` | `reason: stop \| tool-calls \| max-tokens \| aborted{failure} \| error{failure}` + 可选 `replayState` |

`reasoning`（思考）与可见文本是不同的块类型；工具参数全程是原始 JSON 字符串（不是解析后的对象）；交错块靠 `index` 关联。`block-end` 携带完整块是刻意的——token-meter 的"精确重计价"等下游消费者直接受益。

### 适配器契约（两个实现共同验证）

- `usage` 必先于 `finish`，`finish` 后无任何分片；
- 错误只有两条路径：`stream()` 抛出（传输/协议，抛带稳定 code 的 `LlmError`），或以 `finish{kind:'error'|'aborted', failure}` 结束（提供方带内错误）；
- **一次适配器调用 = 一次提供方尝试**——适配器禁用库内重试（pi-ai 侧 `maxRetries: 0`），重试决策上移到 agent 层（见 6.5）；
- 必须遵守 `options.signal`；不支持的请求字段抛 `UNSUPPORTED` 而非静默丢弃；
- 空 completion 映射为 `EMPTY_RESPONSE` 错误 finish；
- `replayState`（适配器私有的无损 JSON，用于续跑优化）只在"历史路由与目标路由当前由同一适配器实例持有"时才传递，否则被剥离。

### LlmRuntime（ctx.llm）

关键方法：`registerAdapter`（返回带原子 `replace()` 的注册句柄）、`prepareCall`（返回一次性 `PreparedLlmCall`，把适配器注册与解析结果绑定——**防 HMR 中途换适配器**）、`stream`。事件：`llm/stream` waterfall（`next()` 到达适配器流）与 `llm/adapters-updated` emit。

## 6.4 一次模型请求的完整生命周期

承接[第 4 章](04-核心循环.md)的步骤流程，聚焦 LLM 侧：

```text
1. buildRequest()    从已记录的 request/header 折叠出 LlmCallConfig 种子
2. agent/request (waterfall)   插件可替换 call config（provider/model/effort/采样）
                               ——但不能改消息（模型可见⟺已记录）
3. prepareCall()     解析模型元数据、物化适配器默认值、拒绝不支持的显式
                     effort（UNSUPPORTED_REASONING_EFFORT），返回深冻结句柄
4. 记录请求头        request/header（含 system 渲染与权威工具顺序）写入日志
5. 深冻结 + 标记     markAgentLoopRequest：循环构建的请求到达 llm/stream 时
                     已冻结且带进程本地标记，listener 只读
6. llm/stream (waterfall)   listener 可调 next() 委托到适配器，或自行
                     yield chunk 短路（llm-replay 快照回放就是这样实现的）
7. 适配器边界        adapterStream()：选适配器、剥离非本实例的 replayState、
                     调 adapter.stream()；适配器侧的任何错误被规范化为
                     终态 error/aborted finish chunk——middleware 与消费方
                     异常则仍然抛出
8. 逐 chunk 落日志   for await：session.append('assistant/chunk') + 喂 BlockAssembler
9. 失败路径          finish 为 error|aborted → agent/request-error (waterfall)
                     → llm-retry 决策重试则 continue，否则抛 LlmError
10. 成功路径         createAssistantMessage（usage + sourceEventSeqs 指回 chunk）
                     → 有 tool-call 块则进入工具流水线
```

**取消传播**：DeepSeek 适配器把调用方 signal 与消费方 controller 经 `AbortSignal.any` 融合，另有 idle watchdog（默认 5 分钟无数据即 `TIMEOUT`）；`LlmRuntime` 边界再把抛出归一为 `aborted` finish。循环在迭代每个 chunk 前 `signal.throwIfAborted()`。

## 6.5 重试与计量：两个 Consumer 的设计

**llm-retry** 挂在 `agent/request-error` waterfall 上：先写持久 `llm/retry` 事件 → 可取消等待（指数退避 500ms→10s，jitter 0.1）→ 写 `llm/retry-started` → 返回 `{kind:'retry'}`（不调 `next()`，接管恢复）。要点：

- 默认可重试 code：`EMPTY_RESPONSE / RATE_LIMIT / SERVER / TIMEOUT / TRANSPORT`；
- 尊重提供方的 `retry-after`（超过 `maxDelayMs` 则放弃）；
- **重试次数从会话日志推导**（按 policyKey 查历史 `llm/retry` 事件）——天然跨重启持久，不需要内存计数器；
- 因为一次适配器调用 = 一次尝试，所以每次重试都是有编号、有日志事件的持久步骤——重试语义本身可审计。

**token-meter** 不在流上挂钩，而是回放会话日志：fold `request/header`/`step/*`/`assistant/message`，以 usage 为锚点（当最近一次成功调用的规范请求信封匹配 `requestHeader` 且总量不低于启发式锚点时复用其 usage，否则对完整信封与表层做启发式重定价）。压缩（[第 5 章](05-会话日志与持久化.md)的 pressure 触发）就是它的主要消费者。

## 6.6 设计取舍

**注册捕获 vs 每请求解析**：连接事实/凭证每次操作重新解析（配置变更下一请求即生效，支持凭据热轮换）；retry policy 与注册绑定（进行中的失败恢复策略不可被路由替换改变）。两者用注册句柄的原子 `replace()` 调和。

**为什么错误要分"抛出"与"finish 错误"两条路径**：传输/协议错误（连不上、SSE 断裂）无法表达为流的一部分，只能抛；提供方带内错误（HTTP 200 后的错误帧）是流的合法终态。消费方（循环、重试）对两者的处理不同：后者有完整上下文（可能带 usage），前者没有。统一成一种会丢失这个区分。

## 6.7 关键实现入口

| 文件 | 职责 |
|---|---|
| `packages/llm/llm/src/types.ts` | StreamChunk、ContentBlockMap、GenerateOptions、FinishReasonMap |
| `packages/llm/llm/src/index.ts` | LlmRuntime（注册表、llm/stream waterfall、prepareCall、失败归一化）、LlmAdapter 抽象类 |
| `packages/llm/llm/src/message.ts` | Message/MessageSourceMap/ContextForm |
| `packages/llm/llm/src/assembler.ts` | BlockAssembler：chunk→块 的唯一共享 fold |
| `packages/llm/llm/src/retry-policy.ts`、`error.ts` | 重试策略解析、规范错误 code |
| `packages/llm/llm-deepseek/src/{adapter,serialize,translate}.ts` | DeepSeek 适配器三件套 |
| `packages/llm/llm-pi-ai/src/{adapter,stream,replay}.ts` | pi-ai 多提供方适配器 |
| `packages/llm/llm-retry/src/index.ts` | agent/request-error 上的重试执行器 |
| `packages/llm/token-meter/src/index.ts` | ctx.tokenMeter |
| `packages/test-support/llm-replay` | 无 key 快照回放（llm/stream 短路的活例） |

## 6.8 小结

LLM 层的系统模型：**一套封闭流式词汇表是稳定契约，适配器把所有提供方差异翻译成它，每次调用是一次可审计的尝试，重试与计量都是从日志派生的 Consumer**。下一章看模型的"手"：工具系统与执行世界。

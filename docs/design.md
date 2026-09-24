# 架构设计

[返回 README](../README.md) · [开发指南](development.md) · [API 参考](api.md)

本文描述内部机制与设计取舍。字段和示例见 [配置参考](configuration.md)，运行、权限与迁移步骤见 [运行与维护](operations.md)。

## 概述

本地 LLM API 路由代理。客户端的 API 基础地址指向 Routelet，虚拟模型名映射到多个真实 provider，默认只调用固定模型；开启 failover 后按优先级路由，故障时自动转移到下一优先级。

> 当前实现状态：仅支持 Messages API 兼容 Provider。本文中的 Chat Completions 协议转换属于后续路线图，未实现的协议类型会在配置加载阶段被拒绝。

``` text
Agent → Routelet (本地 FastAPI) → Provider A   (优先级 1)
                               → Provider B   (优先级 2, 故障转移)
                               → Provider C   (优先级 3, 故障转移)
```

## 核心模块设计

### 1. config.py — 配置加载

**职责：**

- 使用 `tomllib` 加载 TOML 配置文件
- `os.path.expandvars()` 对 `api_key` 等字段做 `${ENV_VAR}` 插值
- Pydantic 校验结构完整性
- 校验 Provider/实际模型引用、重复引用、非负价格和 sticky pin
- 运行时启动允许未解析 `${ENV_VAR}`，用于新环境先打开 dashboard 配置
- 默认严格解析在密钥环境变量未设置时失败，并标明具体 Provider；启动路径显式允许未解析密钥
- 使用结构化 `ConfigError`，可复用解析层不调用 `sys.exit()`

配置字段、默认值和完整示例统一见 [配置参考](configuration.md)。领域模型分成配置文件与运行时两层：

| 层次 | 主要类型 | 职责 |
| --- | --- | --- |
| 配置文件 | `ConfigDocument`、`ProviderDef`、`ActualModelDef`、`ModelRef`、`VirtualModelDef` | 保留 Provider 模型目录、有序引用和固定模型 |
| 运行时 | `AppConfig`、`VirtualModelConfig`、`ProviderConfig` | 合并连接信息、实际模型价格和候选优先级，供路由直接消费 |

**TOML 解析要点：**

解析分为两层：`ConfigDocument` 忠实表达配置文件中的 Provider 目录和 `ModelRef`；`build_runtime_config()` 通过 `(provider, model)` 索引合并 Provider 连接设置、`ActualModelDef` 价格和数组下标，生成 Router 直接消费的完整 `ProviderConfig`。路由层不反查原始配置。

### 1.1 api/config.py — 配置一致性与引用完整性

- `GET /api/config` 返回规范结构并脱敏 API key；Provider 与模型子端点分别返回实际模型目录和有序 `ModelRef`。
- `PUT /api/config` 在接触现有文件前完成候选字段校验、TOML 序列化回读和运行时配置构建。
- 候选准备完成后，通过同目录临时文件原子替换 `config.toml`，再切换 Router 与日志配置。
- 写盘、Router 切换或日志重配置失败时恢复旧文件和旧运行时，并清理临时文件。
- 比较现有配置与候选配置，识别本次删除的 Provider/实际模型，并在校验前清理候选配置中的对应引用。保留其他候选的顺序，删除因清理而失去全部候选的虚拟模型；删除的 pin 在 failover 模式清除，在 sticky 模式改用首个剩余候选。引用移除和目录项删除或替换可在同一事务内保存，无关的悬空引用仍由校验拒绝。

### 2. routing.py — 路由引擎

**职责：**

- 接收虚拟模型名 + Messages API 请求体
- failover 模式按 priority 顺序遍历 Provider 链；sticky 模式仅选择固定模型
- 每次尝试：调用 provider → 成功则返回 → 失败则判断是否重试
- failover 模式全部失败返回 502 + 聚合错误

关闭自动故障转移（sticky）时，Provider 异常保留完整 Provider HTTP 错误正文、状态码和响应头元数据。Router 记录失败，对限流错误更新短冷却，再抛出透传异常，由 API 返回原始正文及必要的端到端错误头；故障转移模式继续使用原有错误分类。sticky 流式请求不使用提前发送 HTTP 200 的预取超时，收到 Provider 响应后才能确定响应状态；流内 SSE error 帧原样转发一次，仍记为失败。无 Provider 响应的传输错误及本地拒绝继续生成 Router 错误。

预取超时策略读取已通过配置代次校验、实际开始调用的模式，不提前读取可变配置。尚未选定 Provider 或实际使用 sticky 时等待首块。failover 提前发送响应头后，若热重载要求重选到不同路由模式，则结束当前流并报告错误，避免进入无法兑现原始 HTTP 状态透传的 sticky 调用；已经开始的 Provider 调用仍按其原有模式完成。

HTTP / 传输错误的重试分类与对外响应见 [API 参考](api.md#路由模式与错误)。failover 模式按错误分类更新熔断或短冷却；sticky 模式关闭自动熔断，仍保留限流冷却与并发保护，不尝试备用候选。切换到 sticky 时清除已有熔断状态。

`provider_gate.py` 管理每个 Provider 的并发槽、队列与冷却。热重载时以配置代次区分真实在途 I/O 与尚未调用 Provider 的旧请求：前者按原配置完成，后者（包括排队请求）重新读取最新配置并选择 Provider。重新选择不增加 Provider 调用计数，也不允许旧 URL、密钥或并发限制继续进入新的调用。

### 3. circuit_breaker.py — 熔断器

Per-provider 熔断器，防止持续向故障 provider 发送请求。

**状态机：**

``` text
CLOSED ──(连续失败达阈值)──→ OPEN
  ↑                            │
  │                            └──(recovery_timeout 后)──→ HALF_OPEN
  │                                                            │
  └──(探测成功)────────────────────────────────────────────────┘
HALF_OPEN ──(探测失败)──→ OPEN
```

- **CLOSED**: 正常状态，请求通过
- **OPEN**: 熔断状态，请求被跳过，等待恢复超时
- **HALF_OPEN**: 半开状态，允许一次探测请求

**参数：**

- `failure_threshold` (默认 5): 连续失败次数阈值，达到后熔断
- `recovery_timeout` (默认 600s / 10 分钟): 熔断后等待恢复的时间

**熔断触发策略：**

- 401/403 (认证/权限错误) → 立即熔断，恢复超时后进入半开探测
- 429/529 (限流/过载) → 进入 Provider 短冷却，不累计熔断失败次数
- 5xx 与瞬态连接/超时错误 → 连续失败达阈值后熔断

**集成方式：** 仅 failover 模式使用熔断器。Router 在候选枚举阶段只读取熔断状态；进入 Provider 容量槽后、真实 Provider 调用前通过 `try_acquire()` 原子领取一次性许可。HALF_OPEN 同一时刻只发出一个探测许可，成功、失败、取消与关闭都会消费或释放；许可携带代际，旧在途请求的迟到结果不会覆盖较新的熔断裁决。

### 4. providers/ — 适配器

#### base.py — 抽象接口

```python
class BaseProvider(ABC):
    def __init__(self, config: ProviderConfig, http_client: httpx.AsyncClient): ...

    @abstractmethod
    async def send(self, request: dict) -> dict:
        """非流式请求，返回完整响应 JSON"""

    @abstractmethod
    async def send_stream(self, request: dict) -> AsyncIterator[bytes]:
        """流式请求，yield SSE 原始字节"""
```

#### Messages API 直通适配器

最简单的适配器：

- 替换请求中的 `model` 为真实模型名
- 设置 `x-api-key` header
- **非流式**: `POST {base_url}/v1/messages` → 返回 JSON
- **流式**: `POST {base_url}/v1/messages` (stream=true) → 直接 yield SSE bytes

适用场景：提供 Messages API 兼容接口的 Provider。

#### 规划：Chat Completions 协议转换

Messages API 和 Chat Completions API 双向转换。

**请求转换 (Messages API → Chat Completions)：**

| Messages API 字段 | Chat Completions 字段 | 转换逻辑 |
| --- | --- | --- |
| `model` | `model` | 替换为真实 Chat Completions 模型名 |
| `system` (string/array) | `messages[0]` | 插入 `{"role": "system", "content": ...}` |
| `messages[].role` | `messages[].role` | 直接映射 (user/assistant) |
| `messages[].content` (array) | `messages[].content` (array) | text 块直接映射；image 块转 `image_url` data URL；tool_use 转 `tool_calls`；tool_result 转 `{"role": "tool"}` |
| `tools[]` | `tools[]` | `{"name":..., "input_schema":...}` → `{"type":"function","function":{"name":...,"parameters":...}}` |
| `tool_choice` | `tool_choice` | 格式映射 |
| `max_tokens` | `max_tokens` | 直接映射 |
| `temperature` | `temperature` | 直接映射 |
| `stream` | `stream` | 直接映射 |

**响应转换 (Chat Completions → Messages API)：**

| Chat Completions 字段 | Messages API 字段 | 转换逻辑 |
| --- | --- | --- |
| `choices[0].message.content` | `content[]` | 字符串 → `[{"type":"text","text":"..."}]` |
| `choices[0].message.tool_calls[]` | `content[]` | 每个 tool_call → `{"type":"tool_use","id":...,"name":...,"input":...}` |
| `choices[0].finish_reason` | `stop_reason` | `"stop"`→`"end_turn"`, `"tool_calls"`→`"tool_use"`, `"length"`→`"max_tokens"` |
| `model` | `model` | 替换回虚拟模型名 |
| `usage` | `usage` | 直接映射 input/output tokens |

**流式 SSE 转换：**

Chat Completions 流式格式：

``` text
data: {"object":"chat.completion.chunk","choices":[{"delta":{"role":"assistant"},"index":0}]}
data: {"object":"chat.completion.chunk","choices":[{"delta":{"content":"Hello"},"index":0}]}
data: {"object":"chat.completion.chunk","choices":[{"finish_reason":"stop"},"index":0}]}
data: [DONE]
```

Messages API 流式格式：

``` text
event: message_start
data: {"type":"message_start","message":{"id":"...","model":"...","content":[],"role":"assistant",...}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{...}}

event: message_stop
data: {"type":"message_stop"}
```

计划先实现非流式转换，再实现流式转换；以上均为设计草案，当前未实现。

### 5. app.py — FastAPI 应用

端点、模型列表格式、请求示例与错误契约见 [API 参考](api.md)。

**`POST /v1/messages` 处理流程：**

1. Router 以 50 MiB 上限有界读取请求体（包括 chunked 请求），随后解析 JSON，并校验 `model` 为非空字符串、`stream`（若提供）为布尔值
2. 提取 `model` 字段，并把协议版本与 beta 功能请求头作为仅供 Provider 使用的内部元数据 → 查找虚拟模型对应的 provider 链
3. 未找到 → 返回 400 + 已知模型列表
4. `stream: true` → 受管 `StreamingResponse(routing.send_stream(...), media_type="text/event-stream")`；完整 SSE 事件逐个校验后才转发，首个有效事件前允许故障转移，初始注释和跨数据块的半个事件不锁定 Provider；无论正常结束、发送失败还是取消都主动关闭内层流
5. `stream: false` → `JSONResponse(routing.send(...))`

流式 Provider 正常结束时还会校验是否交付过数据事件，以及是否残留缺少结束空行的数据帧；空流和不完整帧按协议错误处理，不能关闭熔断器或写入成功记录。预取阶段及时发现时返回 HTTP 502；响应头已发送后则使用 SSE error 报告错误。

流式调用记录的职责随响应体开始消费而从端点交给包装器。预取期间取消、发送响应头失败、消费中断都提交一次取消记录；未启动响应体时由关闭回调回收已获取的资源并记录，避免依赖未启动生成器的 `finally`。

### 6. main.py + cli/ — 单一启动入口

`routelet`、`python -m routelet.main` 与 `python -m routelet.cli` 均调用同一个启动入口；不再注册管理子命令或独立的 Dashboard 命令。`run` 返回整数退出码，进程入口 `main` 抛出 `SystemExit`。

- `cli/app.py` 使用标准库 argparse 解析配置、数据库、监听地址、静态文件和浏览器选项。
- `paths.py` 统一解析用户数据目录与显式路径，启动与热重载共享相同的日志路径规则；具体规则见 [运行与维护](operations.md#数据目录与路径)。
- 状态文件通过统一的权限辅助函数在写入正文前以私有权限打开；配置原子替换、SQLite 初始化和日志轮转延续该机制，避免短暂暴露敏感内容。平台与显式路径边界见 [文件权限](operations.md#文件权限)。
- `cli/config_io.py` 在首次运行时以排他写入方式创建 `~/.routelet/config.toml` 空配置，不覆盖已有文件；默认加载 `~/.routelet/.env`，校验配置，允许尚未解析的 Provider 密钥。旧启动目录的数据需手工迁移，不会随工作目录变化自动读取。
- `cli/server.py` 创建 Router 应用，在所有 API 路由之后挂载 Dashboard，使用单个 Uvicorn 服务监听同一端口。只有监听成功后才打开浏览器，`--no-browser` 可禁用；打开失败仅提示 URL，不中止服务。
- `dashboard.py` 使用 StaticFiles 提供构建资源，为前端历史路由返回 `index.html`。未知 API 和缺失资源仍返回 404，不会返回 SPA 页面，也不会读取静态目录外的文件。

配置、统计、调用记录、Provider 与模型通过页面和现有 API 管理。程序启动前检查前端资源，缺失时给出构建提示并退出。原有应用 lifespan 仍负责初始化与关闭数据库、记录队列和 Provider 连接，Ctrl+C 统一停止服务。

### 7. recording.py + db.py — 调用记录持久化

`attempt` 在每次真正开始 Provider 调用时递增，并在配置热重载后的重新路由中继续累计；本地排队、冷却和熔断跳过不算 Provider 调用。成功、失败和取消记录共享同一请求的累计计数，未调用 Provider 时为 `0`。

流式和非流式用量进入计费与记录前，共用 token 字段校验：只接受 SQLite 有符号整数范围内的非负整数，排除布尔值，忽略其他值和非对象 usage。有效字段独立保留；非流式 Provider 响应正文不受此校验修改。

统计查询通常使用 SQLite 原生 `SUM`；遇到明确的整数溢出时，仅将 token 聚合替换为 Python 大整数聚合重试，保留分组、筛选及全空值语义。单次费用先按每百万 token 缩放价格，避免乘法中间值溢出；持久化费用或汇总费用非有限时，公开 API 将该费用表示为 `null`，面板显示 `—`，数据库原值保持不变。

`CallRecorder` 将请求路径与 SQLite I/O 隔离：请求完成后先把正文序列化为最多 256 KiB 的有效 JSON，再通过非阻塞 `submit()` 写入进程内有界队列。超限正文保存带 `_truncated`、原始 UTF-8 字节数和文本预览的截断信封；输入 token 仍在截断前估算。单个后台 writer 再调用 `CallStore`，使用 `aiosqlite` 顺序写入。writer 随 FastAPI lifespan 启动，关闭时在有限时间内排空已接收记录，然后才关闭数据库。

调用记录被定义为尽力而为的观测数据。队列已满、SQLite 写入失败或关闭排空超时时只记录结构化错误，不改变正常、错误或流式 API 响应；这些情况下允许缺失对应调用记录。提交时的 `request_id` 会随队列项进入后台 writer，用于关联 `call_record.failed` / `call_record.cancelled` 与原请求。该存储不承担严格计费或审计账本职责。

**表结构：**

```sql
CREATE TABLE IF NOT EXISTS calls (
    id              TEXT PRIMARY KEY,        -- UUID, 请求唯一标识
    timestamp       TEXT NOT NULL,           -- ISO 8601 时间戳
    virtual_model   TEXT NOT NULL,           -- 虚拟模型名 (如 "fast-route")
    provider_name   TEXT,                    -- Provider 配置名 (如 "provider-a")
    provider_type   TEXT,                    -- 最终成功的 Provider 协议类型
    provider_model  TEXT,                    -- 真实模型名 (如 "model-a-fast")
    provider_url    TEXT,                    -- 实际调用的 API 端点
    attempt         INTEGER DEFAULT 1,       -- 实际开始的 Provider 调用次数
    latency_ms      INTEGER,                 -- 总耗时 (毫秒)

    -- 请求信息
    request_body    TEXT,                    -- 请求体 JSON 或 256 KiB 有界截断信封
    request_tokens  INTEGER,                 -- 输入 token 估算 (消息长度)

    -- 响应信息
    status          TEXT NOT NULL,           -- success / error
    error_type      TEXT,                    -- 错误类型 (rate_limit, server_error, timeout, etc.)
    error_message   TEXT,                    -- 错误详情
    response_body   TEXT,                    -- 非流式响应 JSON 或 256 KiB 有界截断信封

    -- Token 用量 (从响应中提取)
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cache_read_tokens   INTEGER,            -- Messages API cache 读取
    cache_write_tokens  INTEGER,            -- Messages API cache 写入

    -- 最终成功模型的价格快照 (USD / 1M Token；未配置为 NULL)
    input_price_per_million        REAL,
    output_price_per_million       REAL,
    cache_read_price_per_million   REAL,
    cache_write_price_per_million  REAL,

    -- 按上述快照与 Token 用量计算的最终费用
    cost_usd        REAL,
    failover_details TEXT                  -- 失败尝试链 JSON
);

CREATE INDEX IF NOT EXISTS idx_calls_timestamp ON calls(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status);
CREATE INDEX IF NOT EXISTS idx_calls_metrics_model ON calls(
    virtual_model, status, input_tokens, output_tokens, cache_read_tokens,
    cache_write_tokens, cost_usd, latency_ms
);
CREATE INDEX IF NOT EXISTS idx_calls_metrics_real ON calls(
    provider_name, provider_model, status, input_tokens, output_tokens, cost_usd
);
CREATE INDEX IF NOT EXISTS idx_calls_metrics_day ON calls(
    DATE(timestamp), status, input_tokens, output_tokens,
    cache_read_tokens, cache_write_tokens, cost_usd
);
```

**写入语义：**

- 请求路径不等待 SQLite 提交；有界队列满时立即丢弃新记录，避免观测背压阻塞模型调用。
- 单条记录写入失败不会终止后台 writer，后续记录仍会继续处理。
- 非流式与流式请求都在最终 Provider 成功后写入其结构化 `provider_name`、`provider_model` 和四类价格快照；首选失败后的 failover 使用最终成功模型的数据。
- `cost_usd = Σ(token_count × (price_snapshot or 0)) / 1_000_000`。未配置价格的快照保持 `NULL`，只在公式中按 0；显式价格 0 保持为 0。
- 没有实际成功模型的失败调用不写入价格，四类字段保持 `NULL`。
- 价格是调用发生时的快照；修改当前配置只影响后续调用，不回写历史记录。

**Schema 兼容性：** 当前版本不兼容缺少价格快照字段的旧数据库，也不对缺失的表字段执行增量迁移。`CallStore` 在执行任何 DDL 前通过只读连接检查现有 `calls` 表；不兼容时列出缺失字段并拒绝启动；检测失败不会修改或覆盖原文件。兼容数据库上的统计索引会自动更新。备份与重建步骤见 [数据库兼容性](operations.md#调用记录数据库兼容性)。

### 8. api/metrics.py — 数据查询 API

通过 `CallStore` 提供累计指标、按虚拟模型 / Provider / 实际模型分组、每日趋势和分页调用查询。统计使用覆盖索引读取状态、Token 和费用，按 UTC 日期的表达式索引支持每日分组；索引不含调用正文。列表仅取摘要字段，详情端点再读取正文与故障转移链，减少列表查询的数据量。真实模型分组和筛选始终使用独立的 Provider / model 字段。端点和参数见 [API 参考](api.md#调用记录与统计)。

### 9. monitoring.py — 日志

使用 `structlog` 记录结构化日志，覆盖请求全生命周期。以下为事件示意：

``` text
# 请求级别
{"event": "request.start", "request_id": "abc123", "model": "fast-route", "stream": true, "timestamp": "..."}
{"event": "request.end", "request_id": "abc123", "status": "success", "latency_ms": 1234, "attempt": 1}

# Provider 级别
{"event": "provider.try", "request_id": "abc123", "provider": "provider-a", "model": "model-a-fast", "priority": 1}
{"event": "provider.fail", "request_id": "abc123", "provider": "provider-a", "error": "HTTP 429", "retry": true, "latency_ms": 50}
{"event": "provider.try", "request_id": "abc123", "provider": "provider-b", "model": "model-b-pro", "priority": 2}
{"event": "provider.success", "request_id": "abc123", "provider": "provider-b", "status_code": 200, "latency_ms": 1200}

# Token 用量
{"event": "token.usage", "request_id": "abc123", "input": 1500, "output": 300, "cache_read": 0, "cache_write": 0, "cost_usd": 0.015}

# 故障转移
{"event": "failover", "request_id": "abc123", "from": "provider-a:model-a-fast", "to": "provider-b:model-b-pro", "reason": "rate_limit"}
{"event": "failover.exhausted", "request_id": "abc123", "attempts": 3, "errors": [...]}

# 系统级别
{"event": "server.start", "host": "127.0.0.1", "port": 9456}
{"event": "server.shutdown", "reason": "SIGTERM", "pending_requests": 0}
```

请求相关日志携带 `request_id`，后台记录 writer 也保留提交时的上下文。日志路径、轮转和存储失败事件见 [运行与维护](operations.md#日志与调用记录)。

### 10. Dashboard (Vue) — 模型路由工作台

Vue 3 + TypeScript + Vite + Pinia + Vue Router，通过同源 `/api/*`、`/health` 和 `/v1/messages` 使用后端能力。生产静态资源随 Python 分发；开发服务器仅绑定 127.0.0.1。界面从信息架构、视觉与交互重新实现，采用独立的页面、领域逻辑、状态和基础组件。

以下路径相对于 `dashboard/`：

- `src/pages/`：总览、调用记录、Routers、Providers、请求实验室、系统设置和 404 页面。
- `src/domain/`：后端类型、规范配置默认值、结构化模型引用、级联删除、发布校验、日期 / 金额 / CSV 辅助函数。
- `src/state/`：完整配置快照与内存草稿、监控批次、通知。配置不写入 localStorage，只有主题偏好保存在浏览器。
- `src/services/`：支持超时与取消的 HTTP 客户端、按完整事件解码的 SSE 缓冲器。
- `src/ui/`：原生 dialog 弹窗、调用检查器、配置编辑器、命令搜索、SVG 趋势图和通用状态组件。
- `src/styles/workbench.css`：浅色 / 深色语义色板、布局、交互反馈、移动适配与减少动画支持；`cyberpunk.css`：两套主题共享的赛博朋克字体、切角、网格和控件外观。本地 `public/fonts/` 包含 Oxanium 可变字体及 OFL 许可，无外部字体请求。

总览区分累计指标和局部趋势窗口；SVG 图表通过 ResizeObserver 保持窄屏标签可读，并提供可展开的每日数据表。模型身份在内部始终使用独立 Provider / model 字段，名称中的斜杠不参与解析。调用记录查询使用 URL 筛选、取消与请求代次检查，旧请求不能修改新的筛选结果或页码。

配置以一次完整 GET 读取 Provider、模型、Router 和 Server，统一编辑内存草稿。发布前读取并比较服务器快照，再提交整个候选配置；本地更新期间禁止重复发布。密钥保留、可选价格与零价格遵循后端契约。删除模型或 Provider 同步清理候选引用及 pin，保留剩余顺序，空 Router 一起删除。发布成功后的重新读取失败不会把已经提交的修改标为未保存，也不会在状态中保留新输入的明文密钥。

前端的冲突检查是尽力保护，不替代服务端 compare-and-swap：GET 与 PUT 之间仍有并发窗口，脱敏后的密钥也无法表达完整版本。当前后端接口未引入版本或 ETag。

监控批次以 `Promise.allSettled` 分别结算端点，成功项独立更新、失败项保留旧值并列出原因；只有 `/health` 决定服务连接状态。

请求实验室仅使用已发布配置，发送按钮直接调用同源 Messages API。它支持流式 UTF-8 解码、跨网络块 SSE、原始事件查看、中止和完整结束事件检查；异常流会显示失败而非误报完成。不执行自动 Provider 探测或示例请求。

弹窗使用原生 showModal 实现焦点约束，支持 Esc、遮罩关闭、编辑放弃确认、滚动锁定与焦点恢复。页面支持快捷搜索、键盘调整 Router 候选顺序、明暗主题和移动端导航。

浏览器测试使用隔离的模拟 API，不接触真实 Provider 或凭据。配置与展示领域测试使用 bun:test；Playwright 覆盖从空配置接入到发布、跨页面草稿、冲突 / 失败、筛选并发、请求实验室和响应式布局。操作说明见 [Dashboard 使用说明](dashboard.md)，测试命令与环境见 [开发指南](development.md#测试与检查)。

## 关键设计决策

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 配置格式 | TOML | Python 标准库支持，项目统一 (pyproject.toml)，可读性好 |
| 路由状态 | 有状态 (熔断器) | 每请求独立遍历 provider 链，但通过熔断器跳过持续故障的 provider |
| 流式故障转移 | 按完整 SSE 事件校验后转发 | 首个有效事件前允许切换 Provider，交付后不拼接另一家的响应；流中断后的重试由客户端决定 |
| HTTP 客户端 | 单实例复用 | 进程级 httpx.AsyncClient，按 base_url 自动连接池隔离 |
| 调用记录 | 有界内存队列 + 单后台 writer | 观测存储故障不改变模型响应；队列满或 SQLite 故障时允许丢失记录 |
| 配置热更新 | 原子写盘 + 运行时回滚 | 候选配置先完整验证；文件、Router、日志任一步失败都恢复旧状态 |
| Provider 接口 | 统一 Messages API 格式 | routing.py 不感知后端协议，规划中的 Chat Completions 适配器负责内部转换 |
| 熔断策略 | Per-provider 三态 | 401/403 立即熔断；5xx/瞬态传输错误连续失败后熔断；429/529 仅短冷却；600s 后单探测半开 |

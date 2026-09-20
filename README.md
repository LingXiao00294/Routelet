# Agent Router

本地 LLM API 路由代理，将虚拟模型名映射到多个 provider，按优先级进行故障转移。

```
Claude Code → router (本地 FastAPI) → 智谱 API      (优先级 1)
                                    → 火山引擎      (优先级 2, 故障转移)
                                    → DeepSeek      (优先级 3, 故障转移)
```

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 可选：配置环境变量
cp .env.example .env   # 可先跳过，之后也可通过 dashboard 填写 API key

# 3. 创建路由配置
cp config.toml.example config.toml   # 可直接启动，之后通过 dashboard 添加 provider

# 4. 启动 router
uv run agent-router serve

# 5. 单独启动 dashboard（另一个终端）
uv run agent-router dashboard

# 6. 验证 router
curl http://127.0.0.1:9456/health
```

router 默认监听 `http://127.0.0.1:9456`，dashboard 默认监听 `http://127.0.0.1:5173` 并代理到 router。

## 安全边界

Router 不校验客户端传入的 Anthropic token，Dashboard 还能读取调用详情、修改配置和重置熔断器；`calls.db` 会保存请求与响应正文（单项超过 256 KiB 时保存带 `_truncated` 标记的有界预览），其中仍可能包含提示词、模型输出和其他敏感数据。请限制配置文件、数据库、日志及备份的文件权限，并按自身保留策略清理。

CLI 默认拒绝绑定 `0.0.0.0`、`::` 或其他非回环地址。只有在受信网络或已配置鉴权与 TLS 的反向代理之后，才应显式添加 `--allow-remote`；该开关只确认风险，不会为服务增加鉴权。Router 与 Dashboard 都应保持相同的网络边界。

也可以先构建 dashboard，再安装成用户级工具：

```bash
cd dashboard
bun install
bun run build
cd ..

uv tool install .
agent-router serve
agent-router-dashboard
```

## CLI

CLI 基于 Typer 实现。`agent-router` 默认等价于 `agent-router serve`，旧启动方式仍然可用。Dashboard 已从 router 中拆出，可以用 `agent-router dashboard` 子命令或独立的 `agent-router-dashboard` 命令启动。

```bash
uv run agent-router --help

# 启动 router
uv run agent-router
uv run agent-router serve -c config.toml --db calls.db --host 127.0.0.1 --port 9456

# 启动 dashboard
uv run agent-router dashboard --router-url http://127.0.0.1:9456
uv run agent-router-dashboard --router-url http://127.0.0.1:9456

# 配置管理
uv run agent-router config init
uv run agent-router config validate -c config.toml
uv run agent-router config show -c config.toml --format json

# 路由排查
uv run agent-router providers -c config.toml
uv run agent-router models -c config.toml
uv run agent-router doctor -c config.toml --db calls.db

# 调用记录
uv run agent-router stats --db calls.db
uv run agent-router calls list --db calls.db --limit 20 --status error
uv run agent-router calls show <call-id> --db calls.db --format json
```

`config init` 默认从 `config.toml.example` 生成 `config.toml`，目标文件已存在时不会覆盖；确需覆盖时添加 `--force`。`config validate`、`doctor` 和 `serve` 默认加载 `.env`，可用 `--no-env-file` 跳过。`serve` 允许 `api_key = "${ENV_VAR}"` 暂未解析，以便新环境先启动后端和 dashboard；实际路由时这类 provider 会被跳过。`config validate` 仍按严格模式检查，适合在正式使用前确认环境变量和配置完整。`dashboard` 默认查找安装包内或源码目录下已构建的 `dashboard/dist`；找不到时需要先执行 `cd dashboard && bun install && bun run build` 后重新安装，或通过 `--dist` 指向构建目录。

### 配合 Claude Code 使用

修改 `~/.claude/settings.json`，添加以下配置将 Claude Code 的 API 请求指向本地路由代理：

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:9456",
    "ANTHROPIC_AUTH_TOKEN": "dummy",
    "ANTHROPIC_MODEL": "opus-router",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "opus-router",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "sonnet-router",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "haiku-router",
    "CLAUDE_CODE_SUBAGENT_MODEL": "opus-router"
  }
}
```

> **注意**：`ANTHROPIC_AUTH_TOKEN` 可以设为任意非空值（如 `dummy`），router 不会校验 token。三个 `DEFAULT_*_MODEL` 需与 `config.toml` 中定义的虚拟模型名一致。

## 配置

### config.toml

```toml
[server]
host = "127.0.0.1"
port = 9456
log_level = "debug"

# Provider 连接设置与实际模型目录
[providers.zai]
type = "anthropic"
api_key = "${ZAI_API_KEY}"
base_url = "https://api.z.ai/api/anthropic"

[providers.zai.models."glm-5.2"]
# 可选模型费用，单位 USD / 1M Token；不填的快照为 NULL，计费时按 0
input_price_per_million = 1.4
output_price_per_million = 4.4
cache_read_price_per_million = 0.26
cache_write_price_per_million = 0  # 显式 0 与未配置的 NULL 不同

[providers.deepseek]
type = "anthropic"
api_key = "${DEEPSEEK_API_KEY}"
base_url = "https://api.deepseek.com/anthropic"

[providers.deepseek.models."deepseek-v4-pro"]

# 虚拟模型只保存有序的结构化引用；数组顺序就是路由优先级
[models.opus-router]
pinned_model = { provider = "zai", model = "glm-5.2" }
models = [
  { provider = "zai", model = "glm-5.2" },
  { provider = "deepseek", model = "deepseek-v4-pro" },
]
```

`${ENV_VAR}` 会自动从环境变量或 `.env` 文件展开。未设置时不会阻止 `serve` 启动，方便先打开 dashboard 修改配置；包含未解析 key 的 provider 在实际请求路由时会被跳过，全部 provider 都不可用时返回明确错误。支持 `type = "anthropic"`（Anthropic Messages API 兼容 provider）。

当前版本仅实现 `anthropic` 类型。`openai` 协议转换仍在规划中；配置加载和 Dashboard 都不会再接受一个运行时无法调用的 `openai` 类型。

实际模型及价格只在对应 Provider 的 `models` 目录下定义一次。虚拟模型的 `models` 数组只能引用目录中已有的 `{ provider, model }`，数组顺序会在运行时生成从 1 开始的优先级；sticky 模式还必须提供位于该数组中的结构化 `pinned_model`。同一虚拟模型不能重复引用同一个实际模型。

四类价格均可选：未配置的价格在运行时保持 `None`，调用快照写入 SQLite `NULL`，费用计算时才按 `0`；显式配置 `0` 时快照保留为 `0`。正常写入的成功调用会保存最终实际使用的 Provider、模型、四类价格快照、Token 用量和 `cost_usd`，因此后续调价不会改变历史调用的解释结果。失败调用没有成功模型时，主 Provider 与四类价格快照保持 `NULL`，实际尝试过的 Provider、模型与错误仍保存在故障转移明细中。示例数值仅用于说明格式，请以 Provider 的实际价格为准。

> **Breaking change**：旧版 `[[models.<name>.providers]]`、显式 `priority`、引用上的价格字段以及 `pinned_provider` 不再读取，也不会自动迁移。升级时请先把实际模型移入 `providers.<provider>.models`，再把虚拟模型改为上面的有序 `models` 与结构化 `pinned_model`；程序遇到旧格式会返回可操作的配置错误，不会改写原文件。

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `GET` | `/v1/models` | 列出虚拟模型（Anthropic 格式） |
| `POST` | `/v1/messages` | 聊天接口，支持 `stream: true/false` |
| `GET` | `/api/metrics/summary` | 调用概览统计 |
| `GET` | `/api/metrics/by-model` | 按虚拟模型分组统计 |
| `GET` | `/api/metrics/by-provider` | 按 provider 分组统计 |
| `GET` | `/api/metrics/by-real-model` | 按 Provider + 真实模型复合分组统计，返回独立 `provider`、`model` 字段 |
| `GET` | `/api/metrics/daily?days=30` | 每日调用趋势 |
| `GET` | `/api/calls?page=1&size=50` | 分页查询调用摘要（不含请求/响应正文与故障转移明细）；可用 `provider`、`provider_model` 组合筛选真实模型 |
| `GET` | `/api/calls/{id}` | 单次调用完整详情（含请求/响应正文与故障转移明细） |
| `GET` | `/api/config` | 查看配置（api_key 脱敏） |
| `GET` | `/api/config/providers` | 查看 Provider 及其实际模型目录（api_key 脱敏） |
| `GET` | `/api/config/models` | 查看虚拟模型的有序引用与结构化 pin |
| `PUT` | `/api/config` | 校验、原子写入并热重载配置 |

`POST /v1/messages` 只接受顶层为对象的有效 JSON，请求体上限为 50 MiB；独立 Dashboard 代理会在入口执行相同的有界读取，因此 chunked 请求也不能绕过限制。`model` 必须是非空字符串，`stream` 若提供则必须是布尔值。超过正文上限返回 `413 invalid_request_error`，畸形 JSON、非对象 JSON 或字段类型错误返回 `400 invalid_request_error`。

Router 会将客户端的 `anthropic-version` 与 `anthropic-beta` 请求头转发给最终 Anthropic-compatible Provider；认证头始终由 Provider 配置生成，不会透传客户端 token。SSE 按完整事件校验后转发，初始注释和未完成的事件不会提前锁定 Provider；首个有效事件前的可重试错误即使跨网络数据块，也仍能故障转移。已经交付事件后不会拼接另一家 Provider 的响应。流式客户端中途断开时会立即关闭上游响应并记录 `client_cancelled`，避免长期占用连接和 Provider 并发槽。

`PUT /api/config` 会先完成候选配置校验、TOML 序列化验证和运行时构建，再原子替换文件并切换 Router 与日志配置；任一步失败都会保留或恢复旧文件与旧运行时。热重载时已经开始上游 I/O 的真实在途调用可完成；尚未发起上游 I/O 的旧代际请求（包括本地队列中的请求）会透明地按最新配置重新选择 Provider，不会把配置更新误报为容量不足，也不能用旧 URL、密钥或并发限制继续调用。删除仍被引用的 Provider 或实际模型会返回 `409` 和 `provider_in_use` / `model_in_use`，并在 `referenced_by` 中列出虚拟模型。必须先单独保存引用移除，再执行删除。

### 调用记录数据库兼容性

调用记录的 `attempt` 表示实际开始的上游调用次数，跨配置热重载后的重新路由继续累计。本地容量不足、冷却、熔断或未解析密钥导致的跳过不计入次数；未发起任何上游调用时为 `0`。故障转移明细仍保留相关失败及跳过原因。

流式与非流式响应中的异常 token 用量在计费和记录时会被忽略，有效字段仍会保留；不会因此中断响应、丢弃调用记录或阻止上游资源关闭。非流式响应正文保持上游原样。

流式请求在首个事件预取期间被取消，或在发送响应头时断开，也会记录一次 `client_cancelled`，保留已开始的上游尝试次数并释放连接与并发槽。

SSE 支持流开头的 UTF-8 BOM，包括 BOM 字节跨数据块的情况；它不会影响首事件的错误检测、故障转移或用量解析。空流、仅含注释的流，以及尚未结束数据事件就到达 EOF 的流，均按上游协议错误记录，不计为成功。预取阶段及时发现时返回 HTTP 502；若已发送响应头（包括预取超时的情况），则追加完整的 SSE error 事件。

调用记录属于尽力而为的观测数据。请求完成后会先把请求与非流式响应序列化为最多 256 KiB 的有效 JSON（超限正文替换为包含原始字节数和文本预览的截断信封），再提交到进程内有界队列，由单个后台 writer 顺序写入 SQLite；这既限制慢磁盘期间的队列内存，也不会让 API 响应等待磁盘提交。队列已满、正文序列化失败、SQLite 写入失败或服务关闭时未能在超时内排空，会记录 `call_record.dropped`、`call_record.serialization_failed`、`call_record.failed`、`call_record.shutdown_timeout` 或 `call_record.cancelled` 日志，但不会把已经成功的模型响应改成失败。worker 的失败/取消日志会保留提交时的 `request_id`，便于关联请求链路。对应调用记录在这些情况下可能缺失，因此 `calls.db` 不应直接作为严格计费或审计账本。

本版本的 `calls` 表新增四类价格快照字段，不兼容缺少这些字段的旧 `calls.db`，也不会执行自动迁移。启动时若检测到旧 schema，服务会列出缺失字段并提示手动重建；程序不会删除、覆盖或修改原数据库。请先停止服务并备份或重命名旧文件，例如：

```powershell
Move-Item calls.db calls.db.pre-pricing.bak
```

之后重新运行 `uv run agent-router serve -c config.toml --db calls.db`，程序会创建完整的新数据库。需要保留的旧调用历史仍在备份文件中。

## Dashboard

```bash
cd dashboard
bun install
bun run dev       # 前端开发模式 (Vite 代理到 127.0.0.1:9456)
bun test          # Dashboard 状态逻辑与展示辅助函数测试
bun run build     # 生产构建 → dashboard/dist/
```

构建后通过 `uv run agent-router dashboard` 启动独立面板，访问 `http://127.0.0.1:5173`。代理会删除固定及 `Connection` 动态声明的 hop-by-hop 请求/响应头，避免把只属于单段连接的控制信息带入下一跳。

为防止误清空上游，Dashboard 保存配置时要求至少保留一个 Provider 和一个虚拟模型；每个虚拟模型至少选择一个实际模型。
保存已成功但随后重新加载失败时，面板会保留已保存状态并提示数据可能过期；已保存 Provider 的密钥仍可留空保留，保存期间的新编辑也会保留。自动刷新会等待当前批次完成，同一筛选条件的在途查询会复用，慢请求不会因轮询重叠而一直无法显示结果。
顶部故障转移开关会先重新读取服务器配置，再修改模式，避免用长期未打开的配置页草稿覆盖其他客户端的更新。读取期间出现未保存编辑时会中止切换，保留用户草稿。
配置页加载、保存后的重新加载和模式切换均使用一次完整配置读取，保证 Provider 与虚拟模型引用来自同一快照。
Providers 页面是 Dashboard 管理实际模型目录的入口。每个 Provider 使用独立卡片展示类型、密钥状态、Base URL 和模型摘要；模型超过四个时摘要显示可展开的“还有 N 个”。“设置”只编辑 Provider 连接与限流参数，“添加模型”或点击 `<provider>/<model>` 标签则在独立弹窗中新增模型或编辑四类价格。实际模型价格归 Provider 目录管理，不再出现在虚拟模型引用上。
虚拟模型页只能从该目录中按 Provider 分组选择，不允许自由输入名称或重复引用；长名称会在固定宽度的选择器中截断显示，完整值仍保留在标题和原生选择列表中。左侧拖拽把手调整的数组顺序就是 failover 优先级。
Overview 的真实模型图表和统计表、Calls 的筛选项、列表及调用详情都统一显示 `<provider>/<model>`；内部筛选与 API 始终使用独立的 Provider 和模型字段，不会从展示文本反向解析身份。调用详情同时显示四类价格快照，`—` 表示未配置，`$0.0000` 表示显式配置为零。
Calls 页的真实模型筛选项随进入页面及自动、手动刷新更新；切换筛选或离开页面后，旧请求不会再修改当前页码。
Dashboard 顶栏以“故障转移”开关呈现路由模式：关闭时为指定模型模式（内部仍使用 `sticky`），开启时按数组顺序自动故障转移。切换到指定模型模式前，每个虚拟模型都必须存在有效的结构化 pin，否则切换会失败并返回具体模型名称，原配置保持不变。删除操作都会先确认；删除被引用对象时 Dashboard 会列出引用方、显示错误 Toast 并保持数据不变。弹窗支持遮罩或 Escape 关闭、键盘焦点约束及关闭后的焦点恢复。

## 开发

```bash
uv run pytest                          # 运行测试
uv run pytest tests/test_routing.py -v # 单个测试文件
uv run ruff check src tests            # Lint
uv run ty check src tests              # 类型检查
```

## 项目结构

```
src/agent_router/
├── main.py              # console_scripts 薄入口
├── cli.py               # CLI 子命令：serve/dashboard/config/models/providers/calls/stats/doctor
├── dashboard.py         # 独立 dashboard 静态服务 + API 代理
├── app.py               # FastAPI 应用 + 路由处理
├── config.py            # TOML 加载 + ${ENV_VAR} 展开 + Pydantic 校验
├── routing.py           # 核心：优先级链 + 故障转移
├── recording.py         # 有界队列 + 后台调用记录 writer
├── db.py                # SQLite 调用记录 (aiosqlite)
├── monitoring.py        # 结构化日志 (structlog)
├── providers/
│   ├── base.py          # 抽象 Provider 接口
│   └── anthropic_compat.py  # Anthropic 兼容直通适配器
└── api/
    ├── metrics.py       # /api/metrics, /api/calls 查询接口
    └── config.py        # /api/config 配置读写接口

dashboard/               # Vue 3 + Vite + ECharts 监控面板
tests/                   # pytest (asyncio_mode=auto)
docs/design.md           # 详细设计文档
```

## 故障转移

路由引擎按 priority 升序遍历 provider，成功即返回。错误分类：

- **可重试**（自动切换下一个 provider）：HTTP 429、529、5xx、连接/超时错误
- **立即熔断**（故障转移 + 熔断该 provider）：HTTP 401、403；恢复超时后仍会进入半开探测
- **限流冷却**（按 `Retry-After` 或 Provider 默认值短暂跳过）：HTTP 429、529；不累计熔断失败次数
- **连续熔断**（连续达阈值后熔断）：HTTP 5xx、连接/超时等瞬态传输错误（默认 5 次连续失败）
- **不可重试**（立即返回错误）：HTTP 4xx（除 401/403/429）、协议错误、响应非 JSON
- **全部失败**：返回 502 + 聚合错误信息

熔断的 provider 在恢复超时（默认 600s）后进入半开状态，仅允许一次探测请求：成功则关闭熔断器，失败则重新熔断。

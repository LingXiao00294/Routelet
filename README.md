# Agent Router

本地 LLM API 路由代理，将虚拟模型名映射到多个 provider，按优先级进行故障转移。

``` text
Claude Code → router (本地 FastAPI) → 智谱 API      (优先级 1)
                                    → 火山引擎      (优先级 2, 故障转移)
                                    → DeepSeek      (优先级 3, 故障转移)
```

## 快速开始

首次从源码安装时，构建前端并安装为用户级工具：

```bash
cd dashboard
bun install --frozen-lockfile
bun run build
cd ..
uv tool install .
```

以后只需一个命令：

```bash
agent-router
```

程序在同一个进程、同一个端口提供 API 和 Dashboard，并在监听成功后自动打开 `http://127.0.0.1:9456`。首次运行会在当前目录创建空的 `config.toml`，可直接在页面添加 Provider、实际模型和虚拟模型，再连接客户端。已有配置不会被覆盖；格式错误时启动失败并显示原因。按 `Ctrl+C` 同时停止前后端服务。

源码开发也可使用 `uv sync --frozen` 后执行 `uv run agent-router`。安装包包含构建好的前端，使用时无需 Node.js / Bun。如果命令不在 PATH 中，运行 `uv tool update-shell` 后重开终端。

## 启动选项

```bash
agent-router --help
agent-router --no-browser                    # 无桌面环境或不自动打开浏览器
agent-router -c config.toml --db calls.db     # 指定配置和数据库
agent-router --host 127.0.0.1 --port 9457     # 同时更改 API 与页面的监听地址
agent-router --env-file custom.env           # 默认加载当前目录的 .env
agent-router --no-env-file                   # 不加载环境变量文件
agent-router --dist dashboard/dist          # 指定前端构建目录
```

配置、调用记录、统计、Provider 和模型统一通过 Dashboard 管理。旧的 `serve`、`dashboard`、`config`、`models`、`providers`、`calls`、`stats`、`doctor` 子命令和 `agent-router-dashboard` 入口已移除；已有启动脚本应改为 `agent-router`。重新执行 `uv tool install --force .` 可更新本地安装。

配置、数据库、日志和 `.env` 的默认路径均相对于启动目录，建议始终在固定目录启动。未解析的 `${ENV_VAR}` 不阻止页面启动，但实际请求会跳过对应 Provider。没有可用模型时，请先在页面完成配置。

未找到前端静态文件时会显示构建提示并退出，避免只启动一半服务。发布前先构建前端，再运行 `uv build`，生成包含页面资源的 wheel；可用 `uv tool install dist/agent_router-0.1.0-py3-none-any.whl` 安装。`config.toml.example` 仍可作为手工配置参考，首次启动自动生成的是空配置。

## 安全边界

Router 不校验客户端传入的 Anthropic token，Dashboard 还能读取调用详情、修改配置和重置熔断器；`calls.db` 会保存请求与响应正文（单项超过 256 KiB 时保存带 `_truncated` 标记的有界预览），其中仍可能包含提示词、模型输出和其他敏感数据。请限制配置文件、数据库、日志及备份的文件权限，并按自身保留策略清理。

默认拒绝绑定 `0.0.0.0`、`::` 或其他非回环地址。只有在受信网络或已配置鉴权与 TLS 的反向代理之后，才应显式添加 `--allow-remote`；该开关只确认风险，不会为服务增加鉴权。

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

`${ENV_VAR}` 会自动从环境变量或 `.env` 文件展开。未设置时不会阻止 `agent-router` 启动，方便先打开 dashboard 修改配置；包含未解析 key 的 provider 在实际请求路由时会被跳过，全部 provider 都不可用时返回明确错误。支持 `type = "anthropic"`（Anthropic Messages API 兼容 provider）。

当前版本仅实现 `anthropic` 类型。`openai` 协议转换仍在规划中；配置加载和 Dashboard 都不会再接受一个运行时无法调用的 `openai` 类型。

实际模型及价格只在对应 Provider 的 `models` 目录下定义一次。虚拟模型的 `models` 数组只能引用目录中已有的 `{ provider, model }`，数组顺序会在运行时生成从 1 开始的优先级；sticky 模式还必须提供位于该数组中的结构化 `pinned_model`。同一虚拟模型不能重复引用同一个实际模型。

四类价格均可选：未配置的价格在运行时保持 `None`，调用快照写入 SQLite `NULL`，费用计算时才按 `0`；显式配置 `0` 时快照保留为 `0`。正常写入的成功调用会保存最终实际使用的 Provider、模型、四类价格快照、Token 用量和 `cost_usd`，因此后续调价不会改变历史调用的解释结果。失败调用没有成功模型时，主 Provider 与四类价格快照保持 `NULL`，实际尝试过的 Provider、模型与错误仍保存在故障转移明细中。示例数值仅用于说明格式，请以 Provider 的实际价格为准。

> **Breaking change**：旧版 `[[models.<name>.providers]]`、显式 `priority`、引用上的价格字段以及 `pinned_provider` 不再读取，也不会自动迁移。升级时请先把实际模型移入 `providers.<provider>.models`，再把虚拟模型改为上面的有序 `models` 与结构化 `pinned_model`；程序遇到旧格式会返回可操作的配置错误，不会改写原文件。

## API 端点

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
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

`POST /v1/messages` 只接受顶层为对象的有效 JSON，请求体上限为 50 MiB，包括 chunked 请求；Dashboard 与客户端直接使用同一组 API。`model` 必须是非空字符串，`stream` 若提供则必须是布尔值。超过正文上限返回 `413 invalid_request_error`，畸形 JSON、非对象 JSON 或字段类型错误返回 `400 invalid_request_error`。非有限数值（如 `NaN`、`Infinity`、溢出的浮点数）、无法编码为 UTF-8 的字符串及嵌套过深导致入口解析或编码校验失败的正文也会在路由前返回 `400`，不调用上游或生成调用记录。

Router 会将客户端的 `anthropic-version` 与 `anthropic-beta` 请求头转发给最终 Anthropic-compatible Provider；认证头始终由 Provider 配置生成，不会透传客户端 token。SSE 按完整事件校验后转发，初始注释和未完成的事件不会提前锁定 Provider；首个有效事件前的可重试错误即使跨网络数据块，也仍能故障转移。已经交付事件后不会拼接另一家 Provider 的响应。流式客户端中途断开时会立即关闭上游响应并记录 `client_cancelled`，避免长期占用连接和 Provider 并发槽。

`PUT /api/config` 会先完成候选配置校验、TOML 序列化验证和运行时构建，再原子替换文件并切换 Router 与日志配置；任一步失败都会保留或恢复旧文件与旧运行时。热重载时已经开始上游 I/O 的真实在途调用可完成；尚未发起上游 I/O 的旧代际请求（包括本地队列中的请求）会透明地按最新配置重新选择 Provider，不会把配置更新误报为容量不足，也不能用旧 URL、密钥或并发限制继续调用。删除 Provider 或实际模型时，会在同一次保存中清理候选配置里的对应引用；保留其他候选及其顺序，没有剩余候选的虚拟模型会一起删除。被删除的 `pinned_model` 会清除，sticky 模式自动改用首个剩余候选。移除引用与删除或替换目录项可一次保存，不再要求分两次提交；无关的悬空引用仍会被拒绝。

### 调用记录数据库兼容性

调用记录的 `attempt` 表示实际开始的上游调用次数，跨配置热重载后的重新路由继续累计。本地容量不足、冷却、熔断或未解析密钥导致的跳过不计入次数；未发起任何上游调用时为 `0`。故障转移明细仍保留相关失败及跳过原因。

统计接口在 token 总量超过 SQLite 的整数范围时仍返回精确整数。费用超出浮点表示范围时，调用列表、详情及统计接口返回 `null`，面板显示 `—`；不会因该费用返回 HTTP 500，也不会将溢出费用显示为零。

流式与非流式响应中的异常 token 用量在计费和记录时会被忽略，有效字段仍会保留；不会因此中断响应、丢弃调用记录或阻止上游资源关闭。非流式响应正文保持上游原样。

流式请求在首个事件预取期间被取消，或在发送响应头时断开，也会记录一次 `client_cancelled`，保留已开始的上游尝试次数并释放连接与并发槽。

SSE 支持流开头的 UTF-8 BOM，包括 BOM 字节跨数据块的情况；它不会影响首事件的错误检测、故障转移或用量解析。空流、仅含注释的流，以及尚未结束数据事件就到达 EOF 的流，均按上游协议错误记录，不计为成功。预取阶段及时发现时返回 HTTP 502；若已发送响应头（包括预取超时的情况），则追加完整的 SSE error 事件。

调用记录属于尽力而为的观测数据。请求完成后会先把请求与非流式响应序列化为最多 256 KiB 的有效 JSON（超限正文替换为包含原始字节数和文本预览的截断信封），再提交到进程内有界队列，由单个后台 writer 顺序写入 SQLite；这既限制慢磁盘期间的队列内存，也不会让 API 响应等待磁盘提交。队列已满、正文序列化失败、SQLite 写入失败或服务关闭时未能在超时内排空，会记录 `call_record.dropped`、`call_record.serialization_failed`、`call_record.failed`、`call_record.shutdown_timeout` 或 `call_record.cancelled` 日志，但不会把已经成功的模型响应改成失败。worker 的失败/取消日志会保留提交时的 `request_id`，便于关联请求链路。对应调用记录在这些情况下可能缺失，因此 `calls.db` 不应直接作为严格计费或审计账本。

本版本的 `calls` 表新增四类价格快照字段，不兼容缺少这些字段的旧 `calls.db`，也不会执行自动迁移。启动时若检测到旧 schema，服务会列出缺失字段并提示手动重建；程序不会删除、覆盖或修改原数据库。请先停止服务并备份或重命名旧文件，例如：

```powershell
Move-Item calls.db calls.db.pre-pricing.bak
```

之后重新运行 `uv run agent-router -c config.toml --db calls.db`，程序会创建完整的新数据库。需要保留的旧调用历史仍在备份文件中。

## Dashboard

Dashboard 以六个工作区组织日常操作，使用赛博朋克浅色 / 深色双主题，并适配桌面和手机。浅色采用明亮底色与青、品红、电黄点缀；深色保持同样的技术字体、切角面板和线框控件，切换为深色底与霓虹强调色。页面使用真实 API 数据；空配置会显示接入引导。

| 工作区 | 用途 |
| ------ | ------ |
| 运行总览 | 累计请求、成功率、完整响应耗时、费用、7 / 14 / 30 天请求趋势、模型用量和最近调用 |
| 调用记录 | 按虚拟模型、上游、实际模型和状态筛选；分页、当前页 CSV 导出、执行明细、请求 / 响应正文与四类价格快照 |
| 路由编排 | 可视化候选链、带目标位置预览和动画的拖动排序、固定模型、复制路由，以及全局自动路由开关 |
| 上游服务 | API 连接、模型目录、四类价格、并发 / 排队 / 超时设置，以及实时熔断重置 |
| 请求实验室 | 使用已发布路由发送真实请求；支持系统提示词、流式 / 非流式响应、停止请求、Token 与原始响应 |
| 系统设置 | 全局熔断、监听地址、日志轮转，以及已发布配置的脱敏 JSON 导出 |

所有配置修改先保存在当前页面会话的内存草稿中，跨工作区保留。通过底部「检查并发布」统一校验和热重载；放弃草稿会重新读取服务器配置。发布前会比较最新服务器快照，发现其他客户端的修改时中止，避免直接覆盖。该检查不是后端原子版本锁；多人同时维护时仍需协调写入。刷新或关闭浏览器会提示未发布的草稿，确认离开后草稿不保留。

删除上游或实际模型前会列出受影响路由；确认后在同一草稿中清理引用和无效固定模型，没有剩余候选的路由一起移除。允许显式删除至空配置。已保存上游的 API Key 留空即保留，明文密钥不写入浏览器存储；价格留空与显式 0 保持区分。发布成功但重新读取失败时会提示已保存，并清除内存中的明文密钥。

总览指标和模型排行为累计数据，趋势时间范围只作用于请求流量图，按 UTC 自然日补齐没有调用的日期。调用时间按浏览器本地时区显示。自动刷新间隔为 15 秒，隐藏页面暂停轮询，同批请求不重叠；调用筛选保存在 URL 中，过期响应不会覆盖新筛选。

`Ctrl / ⌘ + K` 打开页面、路由和上游搜索；`Ctrl / ⌘ + S` 打开发布检查；`Esc` 关闭弹窗。侧栏底部切换主题、复制当前同源 API 端点。实验室请求会产生真实上游调用与费用，仅在点击发送后执行，最长等待 180 秒。

```bash
cd dashboard
bun install --frozen-lockfile
bun run dev                  # 127.0.0.1:5173，代理到后端 9456
bun test                     # 配置、网络、并发与数据展示逻辑
bun x playwright install chromium
bun run test:e2e              # 桌面 / 移动浏览器流程，使用模拟 API
bun run build                # 类型检查并构建 dashboard/dist/
```

构建后在仓库根目录运行 `uv run agent-router`，从同一端口使用 API 和面板。也可运行 `bun run format` 统一格式化前端源码。

[详细使用与实现说明](docs/dashboard.md) · [设计说明](docs/design.md)

下面的界面截图使用浏览器测试中的模拟数据，不代表真实调用或报价。

![Dashboard 运行总览](docs/screenshots/dashboard-overview.png)

## 开发

```bash
uv run pytest                          # 运行测试
uv run pytest tests/test_routing.py -v # 单个测试文件
uv run ruff check src tests            # Lint
uv run ty check src tests              # 类型检查
```

## 项目结构

``` text
src/agent_router/
├── main.py              # console_scripts 薄入口
├── cli/
│   ├── __init__.py      # 单一启动入口 main/run
│   ├── __main__.py      # python -m agent_router.cli
│   ├── app.py           # argparse 启动选项与退出码
│   ├── server.py        # API + Dashboard 启动，监听成功后打开浏览器
│   └── config_io.py     # 首次运行空配置、环境变量加载
├── dashboard.py         # 同进程静态文件与 SPA 路由回退
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

dashboard/               # Vue 3 + Vite + Pinia 模型路由工作台
tests/                   # pytest (asyncio_mode=auto)
docs/design.md           # 详细设计文档
```

## 故障转移

在 Dashboard 的「路由编排」顶部切换「自动路由」，然后「检查并发布」生效。关闭时（`router.mode = "sticky"`，默认）每条虚拟模型只调用其固定模型，不重试其他候选；尚未固定时，面板自动选择第一个候选，可在「编排」中修改。开启时（`router.mode = "failover"`），可重试错误会按候选顺序尝试备用上游。开关同时适用于普通与流式请求，仍保留虚拟模型映射和熔断、冷却等保护。

关闭自动路由时，上游 HTTP 错误原样返回状态码和完整正文（包括非 JSON 正文），不再截断或改写为统一错误格式。保留 `Content-Type`、`Content-Language`、`Retry-After`、`WWW-Authenticate`、请求 ID 和限流响应头；连接级响应头、Cookie 不透传，压缩正文解压后重新计算长度。流式请求等待上游响应后才发送响应头，避免提前返回 HTTP 200 掩盖上游错误；上游 HTTP 200 流内的 SSE error 帧按原始字节返回一次，并记录为失败。网络超时、断连及本地熔断、冷却等没有上游错误响应的情况，仍由 Router 生成错误；已发送响应头后发生的网络错误通过 SSE error 返回。

路由引擎按 priority 升序遍历 provider，成功即返回。错误分类：

- **可重试**（自动切换下一个 provider）：HTTP 429、529、5xx、连接/超时错误
- **立即熔断**（故障转移 + 熔断该 provider）：HTTP 401、403；恢复超时后仍会进入半开探测
- **限流冷却**（按 `Retry-After` 或 Provider 默认值短暂跳过）：HTTP 429、529；不累计熔断失败次数
- **连续熔断**（连续达阈值后熔断）：HTTP 5xx、连接/超时等瞬态传输错误（默认 5 次连续失败）
- **不可重试**（立即返回错误）：HTTP 4xx（除 401/403/429）、协议错误、响应非 JSON
- **全部失败**：返回 502 + 聚合错误信息

熔断的 provider 在恢复超时（默认 600s）后进入半开状态，仅允许一次探测请求：成功则关闭熔断器，失败则重新熔断。

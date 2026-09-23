# 配置参考

[返回 README](../README.md) · [面板操作](dashboard.md) · [运行与维护](operations.md)

配置默认位于 `~/.routelet/config.toml`，首次启动会创建空配置，推荐通过 Dashboard 管理。本文说明字段、默认值和模型引用规则；文件路径与启动参数见 [运行与维护](operations.md)。

## 完整配置示例

示例地址、模型名和价格均为占位值，使用前请替换为实际值。示例默认固定调用 `provider-a/model-a-pro`；将 `router.mode` 改为 `"failover"` 后才会在可重试错误时尝试备用候选。

```toml
[server]
host = "127.0.0.1"
port = 9456
log_level = "info"
log_file = "logs/routelet.log"
log_max_bytes = 10000000
log_backup_count = 5

[router]
mode = "sticky"               # 改为 "failover" 开启按候选顺序故障转移
failure_threshold = 5
recovery_timeout = 600.0

# Provider 连接设置与实际模型目录
[providers.provider-a]
type = "anthropic"
api_key = "${PROVIDER_A_API_KEY}"
base_url = "https://api.provider-a.example"
timeout_seconds = 120.0
max_concurrent = 0            # 0 表示不限制并发
max_queue = 0                 # 容量满时不排队
queue_wait_timeout = 30.0
rate_limit_cooldown = 30.0
# failure_threshold = 5       # 可选；不填时继承 router 设置
# recovery_timeout = 600.0    # 可选；不填时继承 router 设置

[providers.provider-a.models."model-a-pro"]
# 可选模型费用，单位 USD / 1M Token；不填的快照为 NULL，计费时按 0
input_price_per_million = 1.4
output_price_per_million = 4.4
cache_read_price_per_million = 0.26
cache_write_price_per_million = 0  # 显式 0 与未配置的 NULL 不同

[providers.provider-b]
type = "anthropic"
api_key = "${PROVIDER_B_API_KEY}"
base_url = "https://api.provider-b.example"

[providers.provider-b.models."model-b-pro"]

# 虚拟模型只保存有序的结构化引用；数组顺序就是路由优先级
[models.reasoning-route]
pinned_model = { provider = "provider-a", model = "model-a-pro" }
models = [
  { provider = "provider-a", model = "model-a-pro" },
  { provider = "provider-b", model = "model-b-pro" },
]
```

## 环境变量与密钥

`${ENV_VAR}` 会自动从环境变量或 `.env` 文件展开。未设置时不会阻止 `routelet` 启动，方便先打开 dashboard 修改配置；包含未解析 key 的 provider 在实际请求路由时会被跳过，全部 provider 都不可用时返回明确错误。

环境变量文件默认是 `~/.routelet/.env`，可参考仓库中的 [`.env.example`](../.env.example) 自行创建。已有进程环境变量优先于 `.env`；`--env-file` 指定其他文件，`--no-env-file` 禁用文件加载。修改环境变量文件后需重启服务以重新加载。

Dashboard 对已有 Provider 留空 API Key 会保留旧值；新增 Provider 必须提供密钥或 `${ENV_VAR}`。接口读取时密钥脱敏，具体更新语义见 [API 参考](api.md)。

## Server 字段

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `host` | `"127.0.0.1"` | 非空监听地址；非回环地址需显式 `--allow-remote` |
| `port` | `9456` | 监听端口，范围 1–65535 |
| `log_level` | `"info"` | `debug`、`info`、`warning` 或 `error` |
| `log_file` | `"logs/routelet.log"` | 相对 `~/.routelet/` 的日志路径；空字符串禁用文件日志 |
| `log_max_bytes` | `10000000` | 单个日志文件轮转阈值，必须大于 0 |
| `log_backup_count` | `5` | 保留的轮转文件数，必须大于等于 0 |

`--host`、`--port` 覆盖对应的配置值。Dashboard 发布监听地址与端口后需要重启服务，其他路由、连接和日志设置可热重载。

## Router 字段与故障转移

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `mode` | `"sticky"` | `sticky` 仅调用固定模型；`failover` 按候选顺序故障转移 |
| `failure_threshold` | `5` | 连续失败熔断阈值，必须大于等于 1 |
| `recovery_timeout` | `600.0` | 熔断后等待半开探测的秒数，必须是有限正数 |

面板中切换模式、选择固定模型和发布的操作见 [第一次接入](dashboard.md#第一次接入)。两种模式下的错误与流式行为见 [路由模式与错误](api.md#路由模式与错误)。

熔断状态机与许可机制见 [架构设计](design.md)。

## Provider 字段

每个 `[providers.<name>]` 定义一组连接设置和实际模型目录。

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `type` | 必填 | 当前只接受 `"anthropic"` |
| `api_key` | 必填 | 非空密钥，支持 `${ENV_VAR}` |
| `base_url` | 必填 | 绝对 HTTP(S) URL，不含 `/v1/messages`；不得含用户信息、查询参数或片段 |
| `timeout_seconds` | `120.0` | Provider 请求超时秒数 |
| `failure_threshold` | 未设置 | 覆盖全局连续失败阈值；必须大于等于 1 |
| `recovery_timeout` | 未设置 | 覆盖全局熔断恢复秒数 |
| `max_concurrent` | `0` | 最大并发数；0 表示不限制 |
| `max_queue` | `0` | 并发满时的排队容量；0 表示不排队 |
| `queue_wait_timeout` | `30.0` | 排队等待超时秒数 |
| `rate_limit_cooldown` | `30.0` | 未取得有效 `Retry-After` 时的限流冷却秒数 |
| `models` | 空目录 | 实际模型名称到价格配置的映射 |

并发与队列容量必须为非负整数；超时、冷却和恢复时间必须为有限正数。Provider 熔断参数未设置时继承 `router` 配置。

限流响应中的 `Retry-After` 若为有效的未来 HTTP 日期，会按该日期等待；无时区日期按 UTC 解释。已过期日期无法提供有效等待时间，回退到 `rate_limit_cooldown`，避免立即重试持续限流的 Provider。数字 `0` 则明确表示立即可重试。

当前版本仅实现 Messages API 兼容协议，`type` 必须为 `"anthropic"`。Chat Completions 协议转换仍在规划中；配置加载和 Dashboard 会拒绝未实现的协议类型。

## 实际模型与虚拟模型

实际模型及价格只在对应 Provider 的 `models` 目录下定义一次。虚拟模型的 `models` 数组只能引用目录中已有的 `{ provider, model }`，数组顺序会在运行时生成从 1 开始的优先级；sticky 模式还必须提供位于该数组中的结构化 `pinned_model`。同一虚拟模型不能重复引用同一个实际模型。

实际模型名必须非空；虚拟模型的 `models` 至少包含一个引用。实际模型身份始终是结构化的 `(provider, model)`，名称中的 `/` 不参与反向解析，`<provider>/<model>` 仅用于展示。

每个 `[providers.<provider>.models."<model>"]` 可设置以下四类价格，单位均为 USD / 1M Token；值必须为有限非负数：

| 字段 | 对应 Token |
| --- | --- |
| `input_price_per_million` | 输入 |
| `output_price_per_million` | 输出 |
| `cache_read_price_per_million` | 缓存读取 |
| `cache_write_price_per_million` | 缓存写入 |

四类价格均可选：未配置的价格在运行时保持 `None`，调用快照写入 SQLite `NULL`，费用计算时才按 `0`；显式配置 `0` 时快照保留为 `0`。正常写入的成功调用会保存最终实际使用的 Provider、模型、四类价格快照、Token 用量和 `cost_usd`，因此后续调价不会改变历史调用的解释结果。失败调用没有成功模型时，主 Provider 与四类价格快照保持 `NULL`，实际尝试过的 Provider、模型与错误仍保存在故障转移明细中。示例数值仅用于说明格式，请以 Provider 的实际价格为准。

配置包含未知字段、悬空引用或重复候选时会被拒绝。API 保存时会清理本次删除的 Provider 或实际模型所关联的引用，详见 [配置 API](api.md#配置读取与发布)。

## 旧格式升级

旧版 `[[models.<name>.providers]]`、引用上的 `priority` / 价格、字符串 `pinned_model` 与独立 `pinned_provider` 均不再接受，也不会自动迁移。

升级前备份配置，再把实际模型移入 `providers.<provider>.models`，将虚拟模型改为有序的 `models` 引用和结构化 `pinned_model`，参考上方示例。遇到旧格式时程序返回可操作的配置错误，不会改写原文件。旧数据目录和数据库升级步骤见 [运行与维护](operations.md#升级与迁移)。

# API 参考

[返回 README](../README.md) · [配置参考](configuration.md) · [架构设计](design.md)

默认基础地址为 `http://127.0.0.1:9456`。Dashboard 与客户端使用同一组接口，Routelet 不校验客户端 token；远程部署前应阅读 [安全边界](operations.md#安全边界)。目前仅支持 Messages API 兼容协议。

## 端点

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/health` | 健康检查 |
| `GET` | `/v1/models` | 列出虚拟模型（Messages API 兼容格式） |
| `POST` | `/v1/messages` | 聊天接口，支持 `stream: true/false` |
| `GET` | `/api/metrics/summary` | 调用概览统计 |
| `GET` | `/api/metrics/by-model` | 按虚拟模型分组统计 |
| `GET` | `/api/metrics/by-provider` | 按 Provider 名称分组统计，未关联 Provider 的调用返回 `provider: null` |
| `GET` | `/api/metrics/by-real-model` | 按 Provider + 真实模型复合分组统计，返回独立 `provider`、`model` 字段 |
| `GET` | `/api/metrics/daily?days=30` | 每日调用趋势 |
| `GET` | `/api/calls?page=1&size=50` | 分页查询调用摘要（不含请求/响应正文与故障转移明细）；可用 `provider`、`provider_model` 组合筛选真实模型 |
| `GET` | `/api/calls/{id}` | 单次调用完整详情（含请求/响应正文与故障转移明细） |
| `GET` | `/api/config` | 查看配置（api_key 脱敏） |
| `GET` | `/api/config/providers` | 查看 Provider 及其实际模型目录（api_key 脱敏） |
| `GET` | `/api/config/models` | 查看虚拟模型的有序引用与结构化 pin |
| `PUT` | `/api/config` | 校验、原子写入并热重载配置 |
| `GET` | `/api/circuit-breaker` | 查看各 Provider 的熔断状态 |
| `POST` | `/api/circuit-breaker/{provider}/reset` | 重置指定 Provider 的熔断状态 |

`GET /health` 返回 `{"status":"ok"}`。熔断状态包含尚无调用的已配置 Provider（`closed`），恢复超时后显示 `half_open`；该接口不显示短冷却。重置会立即清除指定 Provider 的熔断状态与短冷却；Provider 名称作为路径参数时需 URL 编码，例如名称 `a/b` 对应 `/api/circuit-breaker/a%2Fb/reset`。

## 模型列表

`GET /v1/models` 返回配置中的虚拟模型，客户端请求中的 `model` 应使用对应 `id`。示例响应：

```json
{
  "data": [
    {"id": "reasoning-route", "type": "model", "display_name": "reasoning-route", "created_at": "2025-01-01T00:00:00Z"}
  ]
}
```

## Messages 请求

`POST /v1/messages` 只接受顶层为对象的有效 JSON，请求体上限为 50 MiB，包括 chunked 请求；Dashboard 与客户端直接使用同一组 API。`model` 必须是非空字符串，`stream` 若提供则必须是布尔值。超过正文上限返回 `413 invalid_request_error`，畸形 JSON、非对象 JSON 或字段类型错误返回 `400 invalid_request_error`。非有限数值（如 `NaN`、`Infinity`、溢出的浮点数）、无法编码为 UTF-8 的字符串及嵌套过深导致入口解析或编码校验失败的正文也会在路由前返回 `400`，不调用 Provider 或生成调用记录。

客户端可能使用 `/v1/messages?beta=true`，该查询参数不会改变路由行为。协议版本和 beta 功能请求头会转发给最终 Messages API 兼容 Provider；认证头始终由服务端 Provider 配置生成，不会透传客户端 token。

以下为 Bash 示例。先启动服务，添加并发布 `reasoning-route`（或替换为自己的 Router 名称）；请求会产生真实 Provider 调用与费用。在 PowerShell 中可使用 `Invoke-RestMethod` 或根据 shell 规则调整 `curl.exe` 参数。

```bash
# 1. 健康检查
curl http://127.0.0.1:9456/health

# 2. 模型列表
curl http://127.0.0.1:9456/v1/models

# 3. 非流式请求
curl -s -X POST http://127.0.0.1:9456/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"reasoning-route","max_tokens":100,"messages":[{"role":"user","content":"你好"}]}'

# 4. 流式请求
curl -s -X POST http://127.0.0.1:9456/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"reasoning-route","max_tokens":100,"stream":true,"messages":[{"role":"user","content":"你好"}]}'
```

### 流式行为与取消

SSE 按完整事件校验后转发，初始注释和未完成的事件不会提前锁定 Provider；failover 模式下，首个有效事件前的可重试错误即使跨网络数据块，也仍能故障转移。已经交付事件后不会拼接另一家 Provider 的响应。

SSE 支持流开头的 UTF-8 BOM，包括 BOM 字节跨数据块的情况；它不会影响首事件的错误检测、故障转移或用量解析。空流、仅含注释的流，以及尚未结束数据事件就到达 EOF 的流，均按 Provider 协议错误记录，不计为成功。预取阶段及时发现时返回 HTTP 502；若已发送响应头（包括预取超时的情况），则追加完整的 SSE error 事件。

流式客户端中途断开时会立即关闭 Provider 响应并记录 `client_cancelled`，释放连接与 Provider 并发槽。首个事件预取期间被取消，或发送响应头时断开，也会记录一次取消，并保留已开始的 Provider 尝试次数。

### 路由模式与错误

全局 `router.mode` 默认为 `sticky`，只调用每个 Router 的固定模型；`failover` 才会按有序候选进行故障转移。配置和面板开关见 [Router 字段](configuration.md#router-字段与故障转移)。

关闭自动故障转移时，Provider HTTP 错误原样返回状态码和完整正文（包括非 JSON 正文），不再截断或改写为统一错误格式。保留 `Content-Type`、`Content-Language`、`Location`、`Retry-After`、`WWW-Authenticate`、请求 ID 和限流响应头；Provider 3xx 返回原始重定向目标，Router 不自动跟随。连接级响应头、Cookie 不透传，压缩正文解压后重新计算长度。流式请求依据实际开始调用的路由模式决定预取策略，固定模型模式等待 Provider 响应后才发送响应头，避免热重载期间提前返回 HTTP 200 掩盖 Provider 错误；Provider HTTP 200 流内的 SSE error 帧按原始字节返回一次，并记录为失败。网络超时、断连及本地熔断、冷却等没有 Provider 错误响应的情况，仍由 Router 生成错误；已发送响应头后发生的网络错误通过 SSE error 返回。

以下重试分类适用于 `failover` 模式，并且流式请求尚未交付首个有效事件：

| 错误 | 是否重试 | 熔断 | 说明 |
| --- | --- | --- | --- |
| HTTP 401 | ✅ 故障转移 | 🔴 立即熔断 | 认证失败；恢复超时后半开探测 |
| HTTP 403 | ✅ 故障转移 | 🔴 立即熔断 | 权限不足；恢复超时后半开探测 |
| HTTP 429 | ✅ 故障转移 | ❌（短冷却） | 按 `Retry-After` 或默认值暂时跳过 |
| HTTP 529 | ✅ 故障转移 | ❌（短冷却） | API 过载，按限流策略暂时跳过 |
| 其他 HTTP 5xx | ✅ 故障转移 | 🟡 连续触发 | 服务端错误 |
| `httpx.ConnectError` | ✅ 故障转移 | 🟡 连续触发 | DNS/连接拒绝 |
| `httpx.ConnectTimeout` | ✅ 故障转移 | 🟡 连续触发 | 网络不通 |
| `httpx.ReadTimeout` | ✅ 故障转移 | 🟡 连续触发 | 响应超时 |
| `httpx.RemoteProtocolError` | ✅ 故障转移 | 🟡 连续触发 | 连接异常关闭 |
| 其他 HTTP 4xx（如 400、404） | ❌ 不重试 | — | 客户端错误，立即返回 |
| 响应非 JSON | ❌ 不重试 | — | 协议错误 |

虚拟模型未配置时返回 `400` 并列出已知模型；failover 模式中全部候选失败或不可用时返回 `502` 与聚合错误详情。已发送响应头后发生的流式错误通过 SSE error 报告。熔断恢复后的半开探测见 [架构设计](design.md)。

## 配置读取与发布

`GET /api/config` 返回配置并对 API Key 脱敏；Provider 子端点返回实际模型目录，模型子端点返回有序引用与结构化固定模型。配置字段及 TOML 示例见 [配置参考](configuration.md)。

`PUT /api/config` 接受 JSON 配置对象。未提供的顶层 `server`、`router`、`providers`、`models` 节保留现有值；提供的节替换对应节，不是逐字段合并。更新已有 Provider 时，省略、留空或使用脱敏 API Key 会保留原密钥，新增 Provider 需要有效密钥。Dashboard 以完整候选配置发布。

候选配置会先完成校验、TOML 序列化验证和运行时构建，再原子替换文件并切换 Router 与日志配置；任一步失败都会保留或恢复旧文件与旧运行时。监听地址与端口保存后需重启服务。

热重载时已开始的 Provider 调用按原配置完成，尚未开始 Provider I/O 的请求按新配置重新路由；队列与配置代次的处理见 [架构设计](design.md#2-routingpy--路由引擎)。

删除 Provider 或实际模型时，同一次保存会清理对应的 Router 引用；无关的悬空引用仍会被拒绝。候选顺序、空 Router 和固定模型的处理见 [架构设计](design.md#11-apiconfigpy--配置一致性与引用完整性)。

后端会串行处理配置写入，文件读写与日志文件切换在工作线程执行，期间其他在途请求仍可继续运行；同一配置 API 的读取会等待当前写入完成。接口没有版本或 ETag 比较机制。Dashboard 发布前的 GET / PUT 快照检查不是原子版本锁，多客户端仍需协调写入。

## 调用记录与统计

`GET /api/calls` 返回 `{data, total, page, size, pages}`，列表不包含正文与故障转移明细；通过详情端点读取这些字段，记录不存在时返回 `404`。

| 查询参数 | 默认值 | 说明 |
| --- | --- | --- |
| `page` | `1` | 页码，必须大于等于 1 |
| `size` | `50` | 每页条数，范围 1–200 |
| `model` | 未设置 | 按虚拟模型筛选 |
| `status` | 未设置 | 按状态筛选，如 `success` 或 `error` |
| `provider` | 未设置 | 按 Provider 名称筛选 |
| `provider_model` | 未设置 | 按实际模型筛选，建议与 `provider` 组合使用 |

`GET /api/metrics/daily` 的 `days` 默认为 30，范围 1–365。总览与模型排行统计数据库中保留的全部历史数据，`days` 仅影响每日趋势；日期按 UTC 自然日聚合。`by-real-model` 按 Provider 与实际模型复合分组，分别返回 `provider`、`model` 字段，不将含 `/` 的名称拼接后再解析。离线清理过期调用的方法见 [运行与维护](operations.md#日志与调用记录)。

调用记录的 `attempt` 表示实际开始的 Provider 调用次数，跨配置热重载后的重新路由继续累计。本地容量不足、冷却、熔断或未解析密钥导致的跳过不计入次数；未发起任何 Provider 调用时为 `0`。故障转移明细仍保留相关失败及跳过原因。

统计接口在 token 总量超过 SQLite 的整数范围时仍返回精确整数。费用超出浮点表示范围时，调用列表、详情及统计接口返回 `null`，面板显示 `—`；不会因该费用返回 HTTP 500，也不会将溢出费用显示为零。

流式与非流式响应中的异常 token 用量在计费和记录时会被忽略，有效字段仍会保留；不会因此中断响应、丢弃调用记录或阻止 Provider 资源关闭。非流式 Provider 响应的顶层必须是 JSON 对象，返回时会重新序列化；顶层非对象或无法编码为标准 JSON（如包含 `NaN`）时返回 502 并只记录一次错误。

四类 Token 的有效值为 SQLite 有符号整数范围内的非负整数，布尔值不算有效 Token 数。调用费用使用最终成功模型在调用时的价格快照，后续调价不改变历史解释；未配置价格与显式零值的区别见 [价格说明](configuration.md#实际模型与虚拟模型)。

调用正文可能被截断，记录也可能因存储故障或队列已满而缺失，不应作为严格计费或审计账本。截断标记、相关日志及数据库兼容性见 [运行与维护](operations.md)。

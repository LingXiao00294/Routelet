# 开发指南

[返回 README](../README.md) · [架构设计](design.md) · [API 参考](api.md)

## 环境与安装

使用 Python 3.12+ 和 `uv` 管理后端，使用 Bun 管理前端；前端 `packageManager` 与 CI 当前使用 Bun 1.3.14。

在仓库根目录安装锁定的依赖并构建前端：

```bash
uv sync --frozen
cd dashboard
bun install --frozen-lockfile
bun run build
cd ..
uv run routelet
```

`uv run routelet` 在 `127.0.0.1:9456` 同时提供 API 与面板，并自动打开浏览器。首次创建空配置；默认数据目录与启动位置无关，见 [运行与维护](operations.md)。

## 本地开发

后端使用 `uv run routelet --no-browser` 启动。开发前端时，在另一个终端执行：

```bash
cd dashboard
bun run dev
```

Vite 监听 `127.0.0.1:5173`，将 API 请求代理到后端 `9456`。统一启动入口会检查静态资源，因此首次启动后端前仍需完成一次 `bun run build`。

## 项目结构

```text
src/routelet/
├── main.py              # console_scripts 薄入口
├── cli/                 # 参数解析、首次配置、API + Dashboard 启动
├── app.py               # FastAPI 请求处理与生命周期
├── config.py            # TOML、环境变量和结构化配置校验
├── paths.py             # 用户数据目录、路径解析与状态文件权限
├── routing.py           # 路由、故障转移与配置代次处理
├── circuit_breaker.py   # Provider 熔断状态机
├── provider_gate.py     # Provider 并发、排队与冷却
├── responses.py         # 受管流式响应与取消清理
├── sse.py               # SSE 事件解析与校验
├── recording.py         # 有界队列与后台调用记录 writer
├── db.py                # SQLite 调用记录与聚合查询
├── monitoring.py        # 结构化日志和轮转
├── providers/           # 抽象接口与 Messages API 兼容适配器
├── dashboard.py         # 静态资源与 SPA 回退
└── api/                 # 配置、统计与调用详情 API

dashboard/
├── src/
│   ├── pages/           # 六个工作区与 404 页面
│   ├── ui/              # 通用组件、编辑器、图表和弹窗
│   ├── domain/          # 配置规则、类型和格式化
│   ├── state/           # Pinia 配置、监控和通知状态
│   ├── services/        # HTTP 与 SSE 客户端
│   ├── composables/     # 可复用组合式逻辑
│   └── styles/          # 明暗主题与响应式样式
├── tests/               # bun:test 单元测试
├── e2e/                 # Playwright 浏览器流程
└── dist/                # 构建产物，随 Python 分发打包

tests/                   # pytest 后端测试
scripts/                 # 辅助脚本
docs/                    # 配置、API、运维、架构、面板和开发文档
```

## 主要依赖

| 依赖 | 用途 |
| --- | --- |
| `fastapi` | 异步 HTTP 框架，原生 SSE StreamingResponse |
| `uvicorn[standard]` | ASGI 服务器 (uvloop + httptools) |
| `httpx` | 异步 HTTP 客户端，连接池复用，流式支持 |
| `pydantic` | 请求/响应/配置 数据校验 |
| `structlog` | 结构化日志 (请求ID、provider、耗时、结果) |
| `aiosqlite` | 异步 SQLite，调用记录持久化 |

JSON 序列化使用 Python 标准库 `json`，TOML 解析使用标准库 `tomllib`。完整依赖和版本约束以 [pyproject.toml](../pyproject.toml) 与 [前端 package.json](../dashboard/package.json) 为准。

## 测试与检查

在仓库根目录执行后端检查：

```bash
uv run pytest
uv run pytest tests/test_routing.py -v
uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check src tests
```

后端使用 pytest、pytest-asyncio 和 pytest-httpx，异步模式为 `auto`。测试命名为 `tests/test_*.py`，复用 `tests/conftest.py` 的 fixtures，通过 HTTP mock 避免真实上游调用。

在 `dashboard/` 执行前端检查：

```bash
bun test
bun run build
bun x playwright install chromium
bun run test:e2e
```

前端单元测试位于 `dashboard/tests/`，浏览器流程测试位于 `dashboard/e2e/`。`bunfig.toml` 将 bun:test 限定在单元测试目录，避免误执行浏览器测试。Playwright 启动 `127.0.0.1:5174` 并拦截 API，配置、费用和请求都是模拟数据，不接触真实 Provider 或凭据。

修复缺陷时补充回归测试，覆盖受影响的失败、流式或并发路径。当前未设置覆盖率门槛；CI 的具体检查以 [ci.yml](../.github/workflows/ci.yml) 为准，后端在 Python 3.12 / 3.14 上执行，前端执行单元测试、构建和 Chromium 浏览器测试。

| 范围 | 验证重点 |
| --- | --- |
| 配置加载与发布 | 合法 / 非法配置、环境变量、引用、原子替换与失败回滚 |
| Provider 适配器 | 请求与响应直通、模型替换、协议头和认证头处理 |
| 路由与熔断 | 成功、429 / 5xx、超时、全部失败、冷却、半开和并发 |
| 流式响应 | SSE 跨块事件、首事件前故障转移、异常流与客户端取消 |
| 调用记录与日志 | 价格快照、用量、数据库查询、写入失败与 request_id 关联 |
| Dashboard | 空配置接入、跨页草稿、发布冲突与失败、筛选并发、实验室和响应式布局 |

手工调用示例见 [Messages 请求](api.md#messages-请求)，需要先配置可用 Router。手工故障转移验证应在隔离配置中开启 `failover`、使首个候选连接失败并配置可用备用候选；这种验证会产生真实 Provider 调用与费用。

## 构建与打包

发布前先构建前端，再打包 Python 分发文件：

```bash
cd dashboard
bun install --frozen-lockfile
bun run build
cd ..
uv build
```

`dashboard/dist/` 会打包进 wheel 的 `routelet/dashboard_dist/`。安装包运行时无需 Node.js / Bun；源码启动缺少静态资源时会提示构建并退出。

从源码安装用户级命令：

```bash
uv tool install .
# 更新现有本地安装
uv tool install --force .
```

也可安装生成的 wheel，例如 `uv tool install dist/routelet-0.1.0-py3-none-any.whl`，版本号以实际产物为准。命令不在 PATH 中时执行 `uv tool update-shell` 后重开终端。

## 代码与文档约定

Python 使用四空格缩进、类型注解和 Ruff 格式；可用 `uv run ruff format src tests` 格式化。TypeScript / Vue 延续两空格缩进、双引号和分号风格；在 `dashboard/` 使用 `bun run format` 格式化前端。组件采用 `PascalCase.vue`，组合式函数采用 `useXxx.ts`，保持 TypeScript strict 检查通过。

不在 `main` / `master` 上直接开发，按任务创建工作分支。默认不创建提交；提交时只包含当前任务变更，优先使用 Conventional Commits。不要提交 `.env`、`config.toml`、`calls.db` 或运行日志。

用户可见行为变化时同步更新对应专题与示例：配置字段放 `configuration.md`，接口契约放 `api.md`，运行与迁移放 `operations.md`，内部机制与取舍放 `design.md`，页面操作放 `dashboard.md`，开发流程放本文。README 保留入门步骤与文档导航，完整规则维护一处，其他位置通过摘要和链接引用。

Chat Completions 协议转换仍是未实现规划，设计草案保留在 [架构设计](design.md)，不要将规划能力写成已支持功能。

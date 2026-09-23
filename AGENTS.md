# Repository Guidelines

## 项目结构

Routelet 是本地 LLM API 路由代理，使用 Python 3.12+、FastAPI 和 SQLite。

- `src/routelet/`：后端；`routing.py` 负责路由，`providers/` 封装上游协议，`api/` 提供配置与统计接口，`cli/` 提供单一启动入口。
- `dashboard/src/`：Vue 3、TypeScript、Pinia 前端；页面、组件、状态和样式分别位于 `views/`、`components/`、`stores/`、`styles/`。
- `tests/`、`dashboard/tests/`：后端与前端测试；`dashboard/dist/` 是生成的静态资源。
- `docs/design.md`：架构设计与配置格式；`.env.example`：环境变量示例。

## 安装、开发与构建

在仓库根目录执行：

```bash
uv sync --frozen                       # 安装锁定依赖及开发工具
uv run routelet                        # API + Dashboard：127.0.0.1:9456；自动打开浏览器
uv run pytest                         # 后端测试
uv run ruff check src tests            # Python lint
uv run ruff format --check src tests   # 格式检查
uv run ty check src tests              # 类型检查
```

在 `dashboard/` 执行：

```bash
bun install --frozen-lockfile          # 安装前端依赖
bun run dev                           # Vite：5173，代理到后端
bun test                              # 前端测试
bun run build                         # vue-tsc 检查并构建静态资源
```

构建后，在根目录运行 `uv run routelet` 同时启动 API 和面板，首次运行自动创建 `~/.routelet/config.toml` 空配置；数据库、`.env` 和日志默认也位于 `~/.routelet/`，与启动目录无关。运行 `uv build` 打包 Python 分发文件及已构建的面板。

## 代码风格与命名

Python 使用四空格缩进、类型注解；模块和函数采用 `snake_case`，类采用 `PascalCase`。遵循 Ruff 默认格式，可用 `uv run ruff format src tests` 格式化。TypeScript/Vue 延续现有两空格缩进、双引号和分号风格；组件采用 `PascalCase.vue`，组合式函数采用 `useXxx.ts`。保持 TypeScript strict 检查通过。

## 测试要求

后端使用 pytest、pytest-asyncio 和 pytest-httpx，异步模式为 `auto`。测试命名为 `tests/test_*.py`，复用 `tests/conftest.py` fixtures，通过 HTTP mock 避免真实上游调用。前端使用 `bun:test`，文件命名为 `dashboard/tests/*.test.ts`。

修复缺陷时添加对应回归测试，覆盖相关失败、流式或并发路径。可用 `uv run pytest tests/test_routing.py -v` 定向验证。当前未设置覆盖率门槛；提交前运行受影响范围检查，PR 应通过 `.github/workflows/ci.yml` 的全部检查。

## 配置与协作

使用中文沟通，代码和日志可保留原文。不要提交 `.env`、`config.toml`、`calls.db` 或运行日志；调用记录可能包含敏感正文。服务默认绑定回环地址，不要未经评估扩大访问范围。交付时简述变更、检查结果、文档情况、分支与提交状态及遗留问题。

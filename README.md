# Routelet

本地 LLM API 路由代理，将客户端使用的虚拟模型名映射到实际 Provider 和模型，通过 Dashboard 管理连接、路由、调用记录与统计。

默认只调用固定模型；开启「自动故障转移」后，按候选顺序尝试备用 Provider：

```text
Agent → Routelet → Provider A    (优先级 1)
                → Provider B    (优先级 2, 故障转移)
                → Provider C    (优先级 3, 故障转移)
```

当前支持 Messages API 兼容协议。Chat Completions 协议转换仍在规划中。

## 快速开始

从源码安装需要 Python 3.12+、uv 和 Bun。在仓库根目录执行：

```bash
cd dashboard
bun install --frozen-lockfile
bun run build
cd ..
uv tool install .
routelet
```

程序在同一个进程、同一个端口提供 API 和 Dashboard，监听成功后自动打开 `http://127.0.0.1:9456`。首次启动会创建 `~/.routelet/` 和空的 `config.toml`，已有配置不会被覆盖。按 `Ctrl+C` 同时停止服务。

在页面完成最小配置：

1. 打开 **Providers**，添加服务的 Base URL、API Key 和实际模型；Base URL 不包含 `/v1/messages`。
2. 打开 **Routers**，创建客户端使用的虚拟模型名称（例如 `reasoning-route`），添加候选模型并选择固定模型。需要自动切换时开启「自动故障转移」。
3. 点击「检查并发布」，再按下方说明连接客户端。也可在「请求实验室」发送真实请求验证，调用会产生 Provider 费用。

详细操作见 [Dashboard 使用说明](docs/dashboard.md)；手工配置见 [配置参考与完整示例](docs/configuration.md)。

安装包已包含前端资源，运行时无需 Node.js / Bun。命令不在 PATH 中时，执行 `uv tool update-shell` 后重开终端。源码开发可使用 `uv sync --frozen` 后执行 `uv run routelet`，详见 [开发指南](docs/development.md)。

## 连接客户端

在支持 Messages API 的客户端中设置：

| 设置项 | 值 |
| --- | --- |
| API 基础地址 | `http://127.0.0.1:9456` |
| 模型 | 已发布的 Router 名称，例如 `reasoning-route` |
| 认证 token（若必填） | 任意非空值，例如 `dummy` |

Routelet 不校验客户端 token，实际 Provider 密钥由服务端配置管理。客户端的默认模型和子任务模型都应指向已配置的 Router，具体设置项或环境变量名由客户端决定。请求示例见 [API 参考](docs/api.md)。

## 常用启动选项

```bash
routelet --help
routelet --no-browser                 # 不自动打开浏览器
routelet --port 9457                  # 更改 API 与页面端口
routelet -c config.toml --db calls.db  # 指定配置和数据库
```

配置、数据库、可选 `.env` 和日志默认都位于 `~/.routelet/`（Windows 为 `%USERPROFILE%\.routelet\`），从不同目录启动会共用这些数据。完整参数、路径规则与升级步骤见 [运行与维护](docs/operations.md)。

## 安全与数据限制

Routelet 默认仅监听回环地址，没有内置客户端鉴权；Dashboard 可以读取调用详情并修改配置。远程访问需先评估部署环境，再显式使用 `--allow-remote`，该选项本身不会增加鉴权。

配置、调用正文、日志及备份可能包含敏感内容。Linux/macOS 会自动限制默认数据目录和状态文件权限，Windows 使用用户目录 ACL。调用记录可能截断或因存储故障缺失，不应作为严格计费或审计账本。详细边界见 [运行与维护](docs/operations.md#安全边界)。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [配置参考](docs/configuration.md) | 配置字段、默认值、环境变量、模型引用、价格与完整示例 |
| [API 参考](docs/api.md) | 接口、请求示例、错误处理、流式行为、配置发布与统计语义 |
| [运行与维护](docs/operations.md) | 启动参数、数据目录、权限、日志、备份、升级与故障排查 |
| [架构设计](docs/design.md) | 模块职责、路由与熔断、热重载、持久化机制及设计取舍 |
| [Dashboard 使用说明](docs/dashboard.md) | 面板接入、草稿发布、指标含义、请求实验室和快捷键 |
| [开发指南](docs/development.md) | 开发环境、项目结构、测试、构建与打包 |

## 界面预览

Dashboard 提供运行总览、调用记录、Routers、Providers、请求实验室和系统设置，支持浅色 / 深色主题与手机布局。以下截图使用模拟数据，不代表真实调用或报价；更多截图见 [Dashboard 使用说明](docs/dashboard.md#界面预览)。

![Dashboard 运行总览](docs/screenshots/dashboard-overview.png)

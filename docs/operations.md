# 运行与维护

[返回 README](../README.md) · [配置参考](configuration.md) · [开发指南](development.md)

本文说明启动参数、数据位置、文件权限、日志、备份和升级。首次安装与客户端接入见 [快速开始](../README.md#快速开始)。

## 启动与停止

```bash
routelet --help
routelet --no-browser                  # 无桌面环境或不自动打开浏览器
routelet -c config.toml --db calls.db   # 指定配置和数据库
routelet --host 127.0.0.1 --port 9457  # 同时更改 API 与页面的监听地址
routelet --host 0.0.0.0 --allow-remote  # 已按下方安全边界评估远程访问时使用
routelet --env-file custom.env         # 覆盖默认的 ~/.routelet/.env
routelet --no-env-file                 # 不加载环境变量文件
routelet --dist dashboard/dist         # 指定前端构建目录
```

`routelet --version` 显示版本。`-c` 是 `--config` 的简写，`-p` 是 `--port` 的简写。`--host`、`--port` 优先于配置文件中的值。

配置、调用记录、统计、Provider 和模型统一通过 Dashboard 管理，统一启动命令为 `routelet`。只有服务监听成功后才打开浏览器；浏览器打开失败时可手工访问输出的 URL。按 `Ctrl+C` 同时停止 API 和页面服务。

首次运行会生成空配置，已有配置不会被覆盖，格式错误时启动失败并显示原因。未解析的 `${ENV_VAR}` 不阻止页面启动，但实际请求会跳过对应 Provider；没有可用模型时，请先在页面完成配置。

## 数据目录与路径

默认配置和运行数据统一放在用户主目录下的 `~/.routelet/`（Windows 为 `%USERPROFILE%\.routelet\`）。无论从哪个目录运行 `routelet` 或 `uv run routelet`，都使用同一份数据：

```text
~/.routelet/
├── config.toml        # 路由配置，Dashboard 保存到这里
├── .env               # 可选的密钥环境变量文件，需自行创建
├── calls.db           # 调用记录与统计
└── logs/
    └── routelet.log   # 运行日志及同目录下的轮转文件
```

`-c`、`--db`、`--env-file` 可显式覆盖各自路径，支持 `~`；这些参数的相对路径仍以当前工作目录为基准，不会改变其他默认路径。`server.log_file` 的相对路径统一以 `~/.routelet/` 为基准，默认 `logs/routelet.log` 即 `~/.routelet/logs/routelet.log`；绝对路径和以 `~` 开头的路径按指定位置写入，空字符串禁用文件日志。启动输出会显示实际使用的配置和数据库绝对路径。

## 安全边界

Routelet 不校验客户端传入的 token，Dashboard 还能读取调用详情、修改配置和重置熔断器；`calls.db` 会保存请求与响应正文（单项超过 256 KiB 时保存带 `_truncated` 标记的有界预览），其中仍可能包含提示词、模型输出和其他敏感数据。请限制配置文件、数据库、日志及备份的文件权限，并按自身保留策略清理。

默认拒绝绑定 `0.0.0.0`、`::` 或其他非回环地址。只有在受信网络或已配置鉴权与 TLS 的反向代理之后，才应显式添加 `--allow-remote`；该开关只确认风险，不会为服务增加鉴权。

### 文件权限

在 Linux/macOS 上，Routelet 创建和使用 `~/.routelet/` 内的数据时，会将数据目录及其子目录权限收紧为 `0700`，已有配置、已加载的 `.env`、数据库和日志收紧为 `0600`。新建状态文件、配置原子写回及日志轮转也使用 `0600`，不依赖进程的 umask；无法设置权限时操作失败。显式指定路径的父目录权限保持不变。Windows 继续使用用户目录的 ACL，不套用 POSIX 权限位。

## 日志与调用记录

运行日志默认写入 `~/.routelet/logs/routelet.log`，按大小轮转。日志级别、文件大小与保留数量见 [Server 字段](configuration.md#server-字段)。排查一次请求时可用 `request_id` 关联请求、Provider 尝试和后台记录写入日志。

在仓库根目录执行 `uv run python scripts/query_logs.py` 可快速查看最近 20 条调用，默认只读打开 `~/.routelet/calls.db`。需要查询自定义数据库时，传入路径参数，例如 `uv run python scripts/query_logs.py ./my-calls.db`；显式相对路径以当前工作目录为基准。数据库不存在时脚本报错，不创建空文件。

调用记录属于尽力而为的观测数据，请求响应不等待 SQLite 提交。请求与非流式响应正文各自最多保存 256 KiB 的有效 JSON，超限内容保存为带 `_truncated`、原始字节数和文本预览的截断信封。

队列已满、正文序列化失败、SQLite 写入失败或关闭时未能排空队列，可能导致调用记录缺失，但不会把已经成功的模型响应改成失败。相关日志包括 `call_record.dropped`、`call_record.serialization_failed`、`call_record.failed`、`call_record.shutdown_timeout` 和 `call_record.cancelled`；后台失败 / 取消日志保留提交时的 `request_id`。因此，`calls.db` 不应直接作为严格计费或审计账本。

记录字段与数值边界见 [API 参考](api.md#调用记录与统计)，后台队列和持久化机制见 [架构设计](design.md)。

## 备份与恢复

1. 停止 Routelet 服务，避免复制正在写入的数据库。
2. 将 `config.toml`、`.env`（如有）、`calls.db` 和需要保留的 `logs/` 复制到受限访问的备份位置。使用自定义路径时应分别备份对应文件。
3. 恢复前先备份目标位置的现有数据，再恢复文件，核对配置格式、数据库兼容性和文件权限后启动服务。

调用正文、密钥和日志可能包含敏感内容，备份也需限制访问并按自身保留策略清理。恢复旧数据库前先阅读下方兼容性说明。

## 升级与迁移

### 更新安装

重新构建前端后，在仓库根目录执行 `uv tool install --force .` 更新本地安装。构建与 wheel 打包步骤见 [开发指南](development.md#构建与打包)。

更名前已安装的工具需要重新安装，并将启动脚本改为 `routelet`，Python 导入改为 `routelet`。浏览器使用新的主题偏好键，首次打开会使用浅色主题。

### 从旧启动目录迁移

升级旧版本时，先停止服务，把原启动目录中的 `config.toml`、`.env`（如有）、`calls.db` 和 `logs/` 复制到 `~/.routelet/`，再运行 `routelet`。旧目录不会被自动导入；若新目录已有数据，请先核对再合并。原配置中的相对日志路径会改为相对于新目录。未解析的 `${ENV_VAR}` 不阻止页面启动，但实际请求会跳过对应 Provider。没有可用模型时，请先在页面完成配置。

### 配置格式升级

旧版模型配置不会自动迁移；需先备份，再按 [旧格式升级](configuration.md#旧格式升级) 改为 Provider 模型目录、有序引用和结构化固定模型。

### 调用记录数据库兼容性

当前 `calls` 表包含四类价格快照字段，不兼容缺少这些字段的旧 `calls.db`，也不会执行自动迁移。启动时若检测到旧 schema，服务会列出缺失字段并提示手动重建；程序不会删除、覆盖或修改原数据库。请先停止服务并备份或重命名旧文件，例如：

```powershell
Move-Item -LiteralPath "$env:USERPROFILE\.routelet\calls.db" -Destination "$env:USERPROFILE\.routelet\calls.db.pre-pricing.bak"
```

之后重新运行 `routelet`，程序会在 `~/.routelet/` 创建完整的新数据库。若通过 `--db` 指定了其他路径，请备份该路径的文件并沿用相同参数启动。需要保留的旧调用历史仍在备份文件中。

## 常见问题

| 现象 | 排查方式 |
| --- | --- |
| `routelet` 不在 PATH 中 | 执行 `uv tool update-shell` 后重开终端 |
| 前端静态文件缺失，启动退出 | 按 [开发指南](development.md#构建与打包) 构建前端，或用 `--dist` 指向已构建目录 |
| 页面可打开，但没有可用模型 | 添加 Provider 与 Router 并发布，检查密钥环境变量是否已加载 |
| 非回环监听被拒绝 | 阅读上方安全边界，评估部署环境后再显式使用 `--allow-remote` |
| POSIX 文件权限设置失败 | 检查运行用户对数据目录和文件的所有权、权限及文件系统能力；程序会使相关操作失败 |
| 提示数据库缺少字段 | 停止服务，按数据库兼容性步骤备份或重命名旧库，再启动生成新库 |
| 成功响应没有对应调用记录 | 按 `request_id` 检查 `call_record.*` 日志，并排查磁盘、SQLite 与关闭超时 |

# Codex 额度重置通知插件

该插件每 5 分钟读取一次 `https://codex-resets.com/api/v1/resets?limit=20&order=desc`，并选取列表中最新的重置公告（包括 `regular` 和 `banked`）。当公告 `id` 发生变化时，插件会同时显示 API 中的 UTC 时间和转换后的北京时间：

```text
Latest Codex limit reset：2026-08-31 02:29:25 UTC

最新的codex额度重置时间：2026-08-31 10:29:25 北京时间
```

文本通知发送后，插件会用 Playwright 直接打开同一站点的主页，用 API 返回的 `source.url` 精确匹配同一条公告，然后发送“Codex reset announcements”标题和该公告卡片的 PNG 截图。如果公告没有来源链接，则使用 `announced_at` 精确匹配。文本通知本身不附带公告帖子正文。

API 请求会使用 `ETag` 条件请求减少重复传输。从 1.3.x 升级时，插件会将旧时间状态静默迁移到新公告 ID，避免对已发送的公告重复通知。

文本和图片分别记录投递状态：如果文本已发送但截图暂时失败，后续扫描只重试图片，不会重复发送文本。投递记录保存在 MaiBot 分配的插件数据目录中。

## 指令

- `/reset`：实时查询 codex-resets.com，并在当前聊天返回最近一次重置文本与对应公告截图。该指令不受通知白名单限制。

## 截图依赖

插件需要 `playwright` 以及可用的 Chrome/Chromium。插件会优先自动寻找系统中的 Google Chrome 或 Chromium；如果没有系统浏览器，请为当前 Python 环境安装 Playwright Chromium：

```bash
uv run playwright install chromium
```

## 配置

编辑插件目录下的 `config.toml`：

```toml
[plugin]
enabled = true
config_version = "1.0.0"
notify_on_first_run = false

[whitelist]
group_whitelist = ["123456789"]
private_whitelist = ["987654321"]
```

- `group_whitelist`：允许接收通知的 QQ 群号。
- `private_whitelist`：允许接收通知的 QQ 用户号。
- `notify_on_first_run`：默认为 `false`，首次启动只建立基线；设为 `true` 时会立即发送当前最新的重置。

QQ 号需使用引号包围。插件只使用 MaiBot 已存在的真实聊天流；配置前，请确保机器人已在对应群聊或私聊中收到过消息。

## 测试

在 MaiBot 根目录运行：

```bash
uv run pytest plugins/codex-reset_notify_plugin/tests
uv run ruff check plugins/codex-reset_notify_plugin
```

# Codex 额度重置通知插件

该插件每 5 分钟读取一次 `https://codex-reset.com/api/forecast`。当网站的 `last_reset_at` 发生变化时，插件会同时显示网站原始 UTC 时间和转换后的北京时间：

```text
Latest Codex limit reset：2026-08-31 02:34:27 UTC

最新的codex额度重置时间：2026-08-31 10:34:27 北京时间
```

文本通知发送后，插件会用 Playwright 直接打开 `https://codex-resets.com/` 主页，并紧接着发送“Codex reset announcements”标题和第一条最新公告的 PNG 截图。文本通知本身不再附带公告帖子正文。

重置变化仍以 `https://codex-reset.com/api/forecast` 为数据源。两个网站对同一公告的时间记录可能存在数分钟差异，插件只在两者相差不超过 15 分钟时发送截图。

文本和图片分别记录投递状态：如果文本已发送但截图暂时失败，后续扫描只重试图片，不会重复发送文本。投递记录保存在 MaiBot 分配的插件数据目录中。

## 指令

- `/reset`：实时查询 codex-reset.com，并在当前聊天返回最近一次重置通知。该指令不受通知白名单限制。

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

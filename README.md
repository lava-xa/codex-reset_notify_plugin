# Codex 额度重置通知插件

发送内容示例:

```text
Latest Codex limit reset：2026-08-31 02:29:25 UTC

最新的codex额度重置时间：2026-08-31 10:29:25 北京时间
```

![example](./image/example1.png)

- 可用 /reset 进行手动触发
- 截图由Chrome/Chromium 截图实现
- 目前重置通知url目前来自：https://codex-resets.com/


## 截图依赖

插件需要 `playwright` 以及可用的 Chrome/Chromium。插件会优先自动寻找系统中的 Google Chrome 或 Chromium；如果没有系统浏览器，请为当前 Python 环境安装 Playwright Chromium：

```bash
uv run playwright install chromium
```


## 配置

- `group_whitelist`：允许接收通知的 QQ 群列表。每条配置包含 `group_id`（群号）和 `only_reset`（仅接受 `/reset`，默认 `false`）。
- 每个群的 `only_reset` 独立生效。开启的群只接受 `/reset` 命令（支持前导 @ 当前机器人），忽略普通消息、图片、语音及其他命令；自动重置通知仍正常发送。其他群和私聊不受影响，插件禁用时不拦截消息。
- `private_whitelist`：允许接收通知的 QQ 用户号。
- `notify_on_first_run`：默认为 `false`，第一次运行插件是否进行一次通知

在插件的 `config.toml` 中配置，例如：

```toml
[whitelist]
group_whitelist = [
    { group_id = "123456789", only_reset = true },
    { group_id = "987654321", only_reset = false },
]
private_whitelist = []
```

保存并重载插件配置后生效；WebUI 的“QQ 通知白名单”中，每条群聊白名单都有独立的“群号”和“仅接受 /reset”开关。上例只有 `123456789` 群仅接受 `/reset`，`987654321` 群仍正常聊天。若启用开关后尚未建立目标群的聊天流，可先在群里发送一次 `/reset`。

旧版字符串列表会自动转换为逐群配置，默认关闭各群开关；若旧配置已设置共用的 `group_only_reset`，转换时会把该值保留到原有群号的条目中。转换后可逐群调整，共用开关不再显示。

验收时，在两个白名单群分别开启和关闭开关，依次发送 `/reset`、普通文本和其他命令，确认仅开启开关的群忽略其他消息；再确认自动通知和私聊照常处理。



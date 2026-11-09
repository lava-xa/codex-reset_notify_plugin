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

- `group_whitelist`：允许接收通知的 QQ 群号。
- `group_only_reset`：默认为 `false`。开启后，`group_whitelist` 中的 QQ 群只接受 `/reset` 命令（支持前导 @ 当前机器人），忽略普通消息、图片、语音及其他命令；自动重置通知仍正常发送，其他群和私聊不受影响。插件禁用时不拦截消息。
- `private_whitelist`：允许接收通知的 QQ 用户号。
- `notify_on_first_run`：默认为 `false`，第一次运行插件是否进行一次通知

在插件的 `config.toml` 中配置，例如：

```toml
[whitelist]
group_whitelist = ["123456789"]
group_only_reset = true
private_whitelist = []
```

保存并重载插件配置后生效；也可在 WebUI 的“QQ 通知白名单”中切换“白名单群聊仅接受 /reset”。若启用此开关后尚未建立目标群的聊天流，可先在群里发送一次 `/reset`。

验收时，在白名单群依次发送 `/reset`、普通文本和其他命令，确认只有 `/reset` 被处理；关闭开关后普通聊天恢复，再确认其他群和私聊照常处理。



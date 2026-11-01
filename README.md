# Codex 额度重置通知插件

由 [lava](https://github.com/lava-xa) 维护的 MaiBot 第三方插件，数据源为 [codex-resets.com](https://codex-resets.com/) 的公开公告。

该插件每 5 分钟读取一次 `https://codex-resets.com/api/v1/resets?limit=20&order=desc`，并选取列表中最新的重置公告（包括 `regular` 和 `banked`）。当公告 `id` 发生变化时，插件会同时显示 API 中的 UTC 时间和转换后的北京时间：

```text
Latest Codex limit reset：2026-08-31 02:29:25 UTC

最新的codex额度重置时间：2026-08-31 10:29:25 北京时间
```

文本通知发送后，插件会用 Playwright 直接打开同一站点的主页，用 API 返回的 `source.url` 精确匹配同一条公告，然后发送公告区标题和该公告卡片的 PNG 截图。如果公告没有来源链接，则使用 `announced_at` 精确匹配。文本通知本身不附带公告帖子正文。

API 请求会使用 `ETag` 条件请求减少重复传输。从 1.3.x 升级时，插件会将旧时间状态静默迁移到新公告 ID，避免对已发送的公告重复通知。

文本和图片分别记录投递状态：如果文本已发送但截图暂时失败，后续扫描只重试图片，不会重复发送文本。投递记录保存在 MaiBot 分配的插件数据目录中。

## 安装与兼容

| 项目 | 声明范围 / 要求 |
| --- | --- |
| MaiBot | `1.0.0` ～ `1.3.99` |
| maibot-plugin-sdk | `2.8.0` ～ `2.20.99` |
| Python | 建议使用 MaiBot 的 Python 3.12 环境 |
| Python 包 | `aiohttp>=3.12.14`、`playwright>=1.54.0` |
| 浏览器 | 可用的系统 Chrome / Chromium，或 Playwright Chromium |

SDK 下界已修正：旧清单中的 `2.0.0` 缺少插件所需配置重载与数据目录接口，无法正确加载。自动测试覆盖 SDK 2.8.0 和 2.20.0。宿主范围保留原有下界并规范上界，仍需在实际 MaiBot 实例验收；升到新的主次版本后，应先测试再调整上限。

在 MaiBot 根目录克隆：

```bash
git clone https://github.com/lava-xa/codex-reset_notify_plugin.git plugins/codex-reset_notify_plugin
```

Runner 会根据清单处理 Python 依赖，并在首次加载时根据配置模型生成 `config.toml`。也可在首次加载前复制示例：

```bash
cp plugins/codex-reset_notify_plugin/config.example.toml plugins/codex-reset_notify_plugin/config.toml
```

完成下方浏览器依赖安装后，重载插件或重启 MaiBot，再在 WebUI 配置通知白名单。官方插件中心审核收录后，也可在插件市场搜索「Codex 额度重置通知」并安装兼容的 Release。

## 指令

- `/reset`：实时查询 codex-resets.com，并在当前聊天返回最近一次重置文本与对应公告截图。该指令不受通知白名单限制。

## 截图依赖

插件需要 `playwright` 以及可用的 Chrome/Chromium。插件会优先自动寻找系统中的 Google Chrome 或 Chromium；如果没有系统浏览器，请为当前 Python 环境安装 Playwright Chromium：

```bash
uv run playwright install chromium
```

应在运行 MaiBot 的同一用户、同一 Python 环境下安装。仅安装 `playwright` Python 包不会自动安装浏览器。独立虚拟环境中也可执行 `python3 -m playwright install chromium`。

截图依赖站点的公告结构和网络访问；抓取失败时会记录错误，已成功发送的自动通知文本不会因重试截图而重复发送。

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
- `enabled`：控制自动监控任务；设为 `false` 后停止后台扫描。宿主对整个插件的停用行为以实际加载状态为准。
- `config_version`：配置结构版本，保持 `1.0.0`，与插件补丁发布版本独立。

QQ 号需使用引号包围。插件只使用 MaiBot 已存在的真实聊天流；配置前，请确保机器人已在对应群聊或私聊中收到过消息。

两个白名单默认为空，此时不会自动投递；`/reset` 仍按命令逻辑处理。配置保存后支持热更新。对白名单的新增目标，自动通知从下一条新公告开始生效；移除的目标不再接收待投递消息。

运行配置、配置备份与状态文件已加入 `.gitignore`，源码只提供 `config.example.toml`。实际状态文件路径为 `self.ctx.paths.data_dir / "state.json"`，由 MaiBot Runner 分配；保留该文件可延续投递记录。

## 测试

在独立开发环境中，从插件根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
python3 -m pytest tests -q
python3 -m ruff check .
```

在 MaiBot 根目录运行：

```bash
uv run pytest plugins/codex-reset_notify_plugin/tests
uv run ruff check plugins/codex-reset_notify_plugin
```

自动测试覆盖最新公告选择、UTC / 北京时间转换、旧状态迁移、公告 ID 去重、截图失败重试、真实 SDK 接口、配置注入及生命周期资源清理。测试中的消息发送使用替身，真实 QQ 投递需在自己的 MaiBot 实例中验收。

发布前完成以下运行验收：

1. 确认插件加载成功，首次启动按 `notify_on_first_run` 设置建立基线或通知。
2. 在当前聊天发送 `/reset`，确认文本时间和截图对应同一条公告。
3. 在测试白名单验证自动投递，确认白名单外目标不会收到自动通知。
4. 保存白名单及启用状态，确认后台监控随配置更新；重启后确认状态记录保留。
5. 核对截图失败重试期间不会重复发送已成功的文本。

## 发布与反馈

版本记录见 [CHANGELOG.md](CHANGELOG.md)，Tag、Release 和官方插件中心提交流程见 [RELEASING.md](RELEASING.md)。

问题请提交到 [Issues](https://github.com/lava-xa/codex-reset_notify_plugin/issues)，附 MaiBot / SDK / Playwright 版本、复现步骤和去除个人信息后的相关日志。

## 许可证

沿用 GNU GPL v3 或更新版本（`GPL-3.0-or-later`），全文见 [LICENSE](LICENSE)。

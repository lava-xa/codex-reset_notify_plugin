# 发布流程

参考 [官方发布文档](https://docs.maibot.chat/plugin/submission) 和 [插件中心贡献指南](https://github.com/Mai-with-u/plugin-repo/blob/main/CONTRIBUTING.md)。发布前重新核对最新规则。

## 本次发布信息

- 插件 ID：`codex-reset.notify-plugin`，沿用原 ID。
- 仓库：`https://github.com/lava-xa/codex-reset_notify_plugin`
- 插件版本：`1.4.2`
- Tag / Release：`v1.4.2`
- 许可证：`GPL-3.0-or-later`

## 发布前检查

1. 在真实 MaiBot 实例完成 README 中的加载、命令、自动通知、白名单及配置更新验收，记录 Host / SDK / Playwright 版本。
2. 确认浏览器已在运行 MaiBot 的同一用户和环境中安装，`/reset` 能生成同条公告的截图。
3. 在插件根目录运行：

   ```bash
   python3 -m pytest tests -q
   python3 -m ruff check .
   git diff --check
   ```

4. 根目录必须包含 `_manifest.json`、`plugin.py`、`LICENSE`；同时提供 README 和配置示例。
5. 确认运行配置与状态未被跟踪：

   ```bash
   git ls-files -- config.toml config_back state.json state.json.tmp ':(glob)**/__pycache__/**'
   ```

   预期没有输出。本地配置仍可保留，由 `.gitignore` 排除；发布源码根目录不能包含 `config.toml`。

6. 清单版本必须为纯三段式，兼容上限使用已验证主次版本加 `.99`，不能使用 `1.99.99` 等虚高上限。`capabilities` 仅保留实际调用的四项发送 / 聊天流能力。
7. 清单与 LICENSE 一致，仓库 URL 是公开 GitHub HTTPS 地址且没有 `.git` 后缀。

## 提交与 Release

检查改动后提交并推送：

```bash
git add .
git diff --cached --stat
git commit -m "Prepare 1.4.2 release"
git push origin main
git tag v1.4.2
git push origin v1.4.2
```

核对 Tag 中的源码：

```bash
git show v1.4.2:_manifest.json
git ls-tree --name-only v1.4.2
```

确认 `version` 为 `1.4.2`、ID 保持 `codex-reset.notify-plugin`，根目录没有 `config.toml`。

前往 GitHub [创建 Release](https://github.com/lava-xa/codex-reset_notify_plugin/releases/new)，选择 `v1.4.2`，填写 CHANGELOG 对应内容并发布。可标记为预发布；草稿或只有 Tag 均不满足首次收录要求。GitHub 自动生成的源码包即可。

已收录版本的 Tag 和 commit 应保持不变。后续修复请更新版本号并发布新 Release。

## 提交官方插件中心

至少一个有效 Release 发布后，打开 [Add Plugin Issue 模板](https://github.com/Mai-with-u/plugin-repo/issues/new?template=add-plugin.yml)，填写：

- 插件 ID：`codex-reset.notify-plugin`
- 仓库地址：`https://github.com/lava-xa/codex-reset_notify_plugin`
- 补充说明：每五分钟读取 codex-resets.com 公开 API，按公告 ID 去重并向 QQ 白名单发送重置时间与对应公告截图；提供 `/reset` 手动查询。附实际测试通过的 Host / SDK / Playwright 版本，并说明需可用的 Chrome / Chromium。

按模板确认已满足事项，提交后等待 CI 校验和维护者审核。失败时修复、推送后，在原 Issue 新增评论 `/recheck`。

自动测试、外部 API / 截图实测与真实 MaiBot / QQ 投递是不同的验证范围；首次发布前均应按实际运行情况确认。

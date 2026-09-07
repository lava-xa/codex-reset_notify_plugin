"""Codex 额度重置通知插件。"""

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from zoneinfo import ZoneInfo

import asyncio
import base64
import json
import shutil

from aiohttp import ClientError, ClientSession, ClientTimeout
from maibot_sdk import CONFIG_RELOAD_SCOPE_SELF, Command, Field, MaiBotPlugin, PluginConfigBase
from playwright.async_api import Error as PlaywrightError, async_playwright


SITE_URL = "https://codex-resets.com/"
RESET_LIST_LIMIT = 20
RESET_LIST_URL = f"{SITE_URL}api/v1/resets?limit={RESET_LIST_LIMIT}&order=desc"
ANNOUNCEMENT_SECTION_SELECTOR = ".log-section"
RESET_ANNOUNCEMENT_SELECTOR = f"{ANNOUNCEMENT_SECTION_SELECTOR} .log-item"
ANNOUNCEMENT_MORE_SELECTOR = f"{ANNOUNCEMENT_SECTION_SELECTOR} > .log-more"
ANNOUNCEMENT_TIME_SELECTOR = ".log-item-time"
ANNOUNCEMENT_AVATAR_SELECTOR = ".log-avatar"
ANNOUNCEMENT_LINK_SELECTOR = ".log-item-link"
ANNOUNCEMENT_TARGET_ATTRIBUTE = "data-codex-reset-screenshot-target"
ANNOUNCEMENT_CROP_STYLE = f"""
.graph-section {{
    visibility: hidden !important;
}}
{ANNOUNCEMENT_SECTION_SELECTOR} {{
    padding-top: 0 !important;
}}
{RESET_ANNOUNCEMENT_SELECTOR}:not([{ANNOUNCEMENT_TARGET_ATTRIBUTE}]),
{ANNOUNCEMENT_MORE_SELECTOR} {{
    display: none !important;
}}
{RESET_ANNOUNCEMENT_SELECTOR}[{ANNOUNCEMENT_TARGET_ATTRIBUTE}] {{
    animation: none !important;
    opacity: 1 !important;
    transform: none !important;
    transition: none !important;
}}
"""
SCAN_INTERVAL_SECONDS = 300
REQUEST_TIMEOUT_SECONDS = 30
SCREENSHOT_TIMEOUT_MS = 30_000
SCREENSHOT_STABILIZE_MS = 1_500
SCREENSHOT_VIEWPORT_WIDTH = 1_800
SCREENSHOT_VIEWPORT_HEIGHT = 1_000
LEGACY_STATE_TIME_TOLERANCE_SECONDS = 15 * 60
STATE_FILE_NAME = "state.json"
STATE_VERSION = 4
USER_AGENT = "MaiBot-Codex-Reset-Notify-Plugin/1.4.1"
BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")
BROWSER_EXECUTABLE_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)


@dataclass(frozen=True)
class ResetSnapshot:
    """codex-resets.com 上最新的重置信息。"""

    reset_id: str
    reset_at: str
    source_url: Optional[str]


@dataclass(frozen=True)
class NotificationTarget:
    """QQ 通知目标。"""

    chat_type: str
    target_id: str

    @property
    def key(self) -> str:
        """返回用于去重的稳定目标标识。"""

        return f"{self.chat_type}:{self.target_id}"


@dataclass
class MonitorState:
    """持久化的监控与逐目标投递状态。"""

    reset_id: Optional[str]
    reset_at: str
    source_url: Optional[str]
    target_keys: List[str]
    text_delivered_target_keys: Set[str]
    image_delivered_target_keys: Set[str]

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "MonitorState":
        """从持久化字典中构造状态并校验关键字段。"""

        state_version = payload.get("version")
        if state_version not in {1, 2, 3, STATE_VERSION}:
            raise ValueError(f"不支持的状态文件版本: {payload.get('version')!r}")

        reset_at = payload.get("reset_at")
        target_keys = payload.get("target_keys")
        if not isinstance(reset_at, str) or not reset_at.strip():
            raise ValueError("状态文件缺少有效的 reset_at")
        if not isinstance(target_keys, list) or not all(isinstance(item, str) for item in target_keys):
            raise ValueError("状态文件中的 target_keys 必须是字符串列表")

        if state_version in {1, 2}:
            legacy_delivered_target_keys = cls._required_string_list(payload, "delivered_target_keys")
            text_delivered_target_keys = set(legacy_delivered_target_keys)
            image_delivered_target_keys = set(legacy_delivered_target_keys)
        else:
            text_delivered_target_keys = set(cls._required_string_list(payload, "text_delivered_target_keys"))
            image_delivered_target_keys = set(cls._required_string_list(payload, "image_delivered_target_keys"))

        if state_version == STATE_VERSION:
            reset_id = cls._optional_string(payload, "reset_id")
            source_url = cls._optional_string(payload, "source_url")
        else:
            reset_id = None
            source_url = None

        return cls(
            reset_id=reset_id,
            reset_at=reset_at.strip(),
            source_url=source_url,
            target_keys=list(dict.fromkeys(target_keys)),
            text_delivered_target_keys=text_delivered_target_keys,
            image_delivered_target_keys=image_delivered_target_keys,
        )

    @staticmethod
    def _required_string_list(payload: Dict[str, Any], field_name: str) -> List[str]:
        """从状态字典读取必需的字符串列表。"""

        value = payload.get(field_name)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"状态文件中的 {field_name} 必须是字符串列表")
        return value

    @staticmethod
    def _optional_string(payload: Dict[str, Any], field_name: str) -> Optional[str]:
        """从状态字典读取可选的非空字符串。"""

        value = payload.get(field_name)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"状态文件中的 {field_name} 必须是非空字符串或 null")
        return value.strip()

    def to_dict(self) -> Dict[str, Any]:
        """转换为可写入 JSON 的字典。"""

        return {
            "version": STATE_VERSION,
            "reset_id": self.reset_id,
            "reset_at": self.reset_at,
            "source_url": self.source_url,
            "target_keys": self.target_keys,
            "text_delivered_target_keys": sorted(self.text_delivered_target_keys),
            "image_delivered_target_keys": sorted(self.image_delivered_target_keys),
        }

    def to_snapshot(self) -> ResetSnapshot:
        """转换为可用于投递与截图的重置快照。"""

        if self.reset_id is None:
            raise RuntimeError("旧版状态尚未迁移，缺少 reset_id")
        return ResetSnapshot(
            reset_id=self.reset_id,
            reset_at=self.reset_at,
            source_url=self.source_url,
        )


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "refresh-cw"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用 Codex 额度重置通知")
    config_version: str = Field(default="1.0.0", description="配置版本")
    notify_on_first_run: bool = Field(
        default=False,
        description="首次运行时是否立即通知网站当前的最新重置",
    )


class WhitelistConfig(PluginConfigBase):
    """QQ 群聊与私聊通知白名单。"""

    __ui_label__ = "QQ 通知白名单"
    __ui_icon__ = "users"
    __ui_order__ = 1

    group_whitelist: List[str] = Field(
        default_factory=list,
        description="接收通知的 QQ 群号列表",
        json_schema_extra={"label": "群聊白名单", "placeholder": "例如：123456789"},
    )
    private_whitelist: List[str] = Field(
        default_factory=list,
        description="接收通知的 QQ 用户号列表",
        json_schema_extra={"label": "私聊白名单", "placeholder": "例如：987654321"},
    )


class CodexResetNotifyConfig(PluginConfigBase):
    """插件完整配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    whitelist: WhitelistConfig = Field(default_factory=WhitelistConfig)


class CodexResetNotifyPlugin(MaiBotPlugin):
    """每五分钟监控 Codex 额度重置并通知 QQ 白名单。"""

    config_model = CodexResetNotifyConfig

    def __init__(self) -> None:
        """初始化运行时状态。"""

        super().__init__()
        self._http_session: Optional[ClientSession] = None
        self._monitor_task: Optional[asyncio.Task[None]] = None
        self._state: Optional[MonitorState] = None
        self._resets_etag: Optional[str] = None
        self._cached_snapshot: Optional[ResetSnapshot] = None
        self._screenshot_lock = asyncio.Lock()

    async def on_load(self) -> None:
        """加载状态并启动监控任务。"""

        self.ctx.paths.data_dir.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()
        self._http_session = ClientSession(
            timeout=ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        if self.config.plugin.enabled:
            self._start_monitor_task()
        self.ctx.logger.info(
            "Codex 额度重置通知插件已加载，扫描间隔=%s秒，群聊白名单=%s，私聊白名单=%s",
            SCAN_INTERVAL_SECONDS,
            len(self.config.whitelist.group_whitelist),
            len(self.config.whitelist.private_whitelist),
        )

    async def on_unload(self) -> None:
        """停止监控任务并释放 HTTP 资源。"""

        await self._stop_monitor_task()
        if self._http_session is not None:
            await self._http_session.close()
            self._http_session = None
        self.ctx.logger.info("Codex 额度重置通知插件已卸载")

    async def on_config_update(self, scope: str, config_data: Dict[str, object], version: str) -> None:
        """应用插件配置热更新。"""

        del config_data
        if scope != CONFIG_RELOAD_SCOPE_SELF:
            return

        await self._stop_monitor_task()
        if self.config.plugin.enabled:
            self._start_monitor_task()
        self.ctx.logger.info("Codex 额度重置通知配置已更新: version=%s", version)

    @Command(
        "codex_reset_latest",
        description="查询 codex-resets.com 最近一次 Codex 额度重置通知",
        pattern=r"^/reset$",
    )
    async def handle_reset_command(self, stream_id: str = "", **kwargs: Any):
        """实时获取并返回最近一次 Codex 额度重置通知。"""

        del kwargs
        if not stream_id:
            return False, "缺少当前聊天流 ID，无法返回重置通知", True

        try:
            snapshot = await self._fetch_latest_snapshot()
        except (ClientError, asyncio.TimeoutError, ValueError) as exception:
            self.ctx.logger.error("/reset 指令获取最近重置失败: %s", exception, exc_info=True)
            failure_message = "暂时无法获取最近的 Codex 重置通知，请稍后重试。"
            await self.ctx.send.text(failure_message, stream_id)
            return False, failure_message, True

        message = self._build_notification_message(snapshot.reset_at)
        text_sent = await self.ctx.send.text(message, stream_id)
        if not text_sent:
            return False, "最近的 Codex 重置通知发送失败", True

        try:
            reset_image = await self._capture_reset_image(snapshot)
        except (OSError, PlaywrightError, ValueError) as exception:
            self.ctx.logger.error("/reset 指令生成主页重置截图失败: %s", exception, exc_info=True)
            failure_message = "重置时间已返回，但主页重置信息截图生成失败。"
            await self.ctx.send.text(failure_message, stream_id)
            return False, failure_message, True

        image_sent = await self.ctx.send.image(reset_image, stream_id)
        if not image_sent:
            return False, "最近的 Codex 重置信息图片发送失败", True
        return True, "已返回最近的 Codex 重置通知", True

    def _start_monitor_task(self) -> None:
        """启动唯一的后台监控任务。"""

        if self._monitor_task is not None and not self._monitor_task.done():
            return
        self._monitor_task = asyncio.create_task(self._monitor_loop(), name="codex-reset-notify-monitor")
        self._monitor_task.add_done_callback(self._handle_monitor_task_done)

    async def _stop_monitor_task(self) -> None:
        """停止当前后台监控任务。"""

        monitor_task = self._monitor_task
        self._monitor_task = None
        if monitor_task is None or monitor_task.done():
            return
        monitor_task.cancel()
        with suppress(asyncio.CancelledError):
            await monitor_task

    def _handle_monitor_task_done(self, task: asyncio.Task[None]) -> None:
        """显式记录后台任务的非预期退出。"""

        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            exception_info = (type(exception), exception, exception.__traceback__)
            self.ctx.logger.error("监控任务因未处理异常退出", exc_info=exception_info)

    async def _monitor_loop(self) -> None:
        """立即扫描一次，之后每五分钟扫描一次。"""

        while True:
            try:
                await self._scan_once()
            except (ClientError, asyncio.TimeoutError, ValueError) as exception:
                self.ctx.logger.error("Codex 额度重置扫描失败: %s", exception, exc_info=True)
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

    async def _scan_once(self) -> None:
        """获取最新重置，更新状态并投递待发通知。"""

        snapshot = await self._fetch_latest_snapshot()
        if self._state is None:
            target_keys = (
                [target.key for target in self._configured_targets()] if self.config.plugin.notify_on_first_run else []
            )
            self._state = self._state_from_snapshot(snapshot, target_keys)
            self._save_state()
            if not self.config.plugin.notify_on_first_run:
                self.ctx.logger.info(
                    "Codex 重置监控基线已建立: id=%s, time=%s",
                    snapshot.reset_id,
                    snapshot.reset_at,
                )
                return
        elif self._state.reset_id is None:
            is_same_legacy_event = self._timestamps_within_tolerance(
                self._state.reset_at,
                snapshot.reset_at,
                LEGACY_STATE_TIME_TOLERANCE_SECONDS,
            )
            if is_same_legacy_event:
                legacy_state = self._state
                self._state = MonitorState(
                    reset_id=snapshot.reset_id,
                    reset_at=snapshot.reset_at,
                    source_url=snapshot.source_url,
                    target_keys=legacy_state.target_keys,
                    text_delivered_target_keys=legacy_state.text_delivered_target_keys,
                    image_delivered_target_keys=legacy_state.image_delivered_target_keys,
                )
                self._save_state()
                self.ctx.logger.info(
                    "旧版重置状态已静默迁移: id=%s, time=%s",
                    snapshot.reset_id,
                    snapshot.reset_at,
                )
            else:
                target_keys = [target.key for target in self._configured_targets()]
                self._state = self._state_from_snapshot(snapshot, target_keys)
                self._save_state()
                self.ctx.logger.info(
                    "状态迁移时发现新的 Codex 重置: id=%s, time=%s",
                    snapshot.reset_id,
                    snapshot.reset_at,
                )
        elif self._state.reset_id != snapshot.reset_id:
            target_keys = [target.key for target in self._configured_targets()]
            self._state = self._state_from_snapshot(snapshot, target_keys)
            self._save_state()
            self.ctx.logger.info(
                "Codex limit reset 公告已变化: id=%s, time=%s",
                snapshot.reset_id,
                snapshot.reset_at,
            )
        elif self._state.reset_at != snapshot.reset_at or self._state.source_url != snapshot.source_url:
            self._state.reset_at = snapshot.reset_at
            self._state.source_url = snapshot.source_url
            self._save_state()
            self.ctx.logger.info("Codex 重置公告元数据已更新: id=%s", snapshot.reset_id)

        await self._deliver_pending_notifications()

    async def _fetch_latest_snapshot(self) -> ResetSnapshot:
        """从公开 API 读取最新重置公告。"""

        if self._http_session is None:
            raise RuntimeError("HTTP 会话尚未初始化")

        request_headers: Dict[str, str] = {}
        if self._resets_etag is not None:
            request_headers["If-None-Match"] = self._resets_etag

        async with self._http_session.get(RESET_LIST_URL, headers=request_headers) as response:
            if response.status == 304:
                if self._cached_snapshot is None:
                    raise ValueError("API 返回 304，但插件尚无可用的重置快照缓存")
                return self._cached_snapshot
            response.raise_for_status()
            payload = await response.json(content_type=None)
        if not isinstance(payload, dict):
            raise ValueError(f"{RESET_LIST_URL} 返回的 JSON 顶层必须是对象")

        snapshot = self._snapshot_from_reset_list(payload)
        self._resets_etag = response.headers.get("ETag")
        self._cached_snapshot = snapshot
        return snapshot

    @classmethod
    def _snapshot_from_reset_list(cls, payload: Dict[str, Any]) -> ResetSnapshot:
        """从按时间降序的公告列表中提取最新重置。"""

        reset_items = payload.get("data")
        if not isinstance(reset_items, list):
            raise ValueError("resets.data 必须是列表")
        if not reset_items:
            raise ValueError("resets.data 中没有重置公告")

        reset_item = reset_items[0]
        source_name = "resets.data[0]"
        if not isinstance(reset_item, dict):
            raise ValueError(f"{source_name} 必须是对象")

        # banked 也是网站正式的额度重置类型，不应跳过。
        cls._required_string(reset_item, "reset_type", source_name)
        reset_id = cls._required_string(reset_item, "id", source_name)
        reset_at = cls._required_string(reset_item, "announced_at", source_name)
        cls._required_string(reset_item, "text", source_name)
        cls._parse_timestamp(reset_at)

        source = reset_item.get("source")
        if not isinstance(source, dict):
            raise ValueError(f"{source_name}.source 必须是对象")
        cls._required_string(source, "type", f"{source_name}.source")
        source_url_value = source.get("url")
        if source_url_value is None:
            source_url = None
        elif isinstance(source_url_value, str) and source_url_value.strip():
            source_url = source_url_value.strip()
        else:
            raise ValueError(f"{source_name}.source.url 必须是非空字符串或 null")

        return ResetSnapshot(
            reset_id=reset_id,
            reset_at=reset_at,
            source_url=source_url,
        )

    async def _deliver_pending_notifications(self) -> None:
        """向本次变化中尚未成功的目标依次投递文本和截图。"""

        if self._state is None:
            raise RuntimeError("监控状态尚未初始化")

        snapshot = self._state.to_snapshot()
        configured_target_map = {target.key: target for target in self._configured_targets()}
        message = self._build_notification_message(snapshot.reset_at)
        resolved_stream_ids: Dict[str, str] = {}
        for target_key in self._state.target_keys:
            text_delivered = target_key in self._state.text_delivered_target_keys
            image_delivered = target_key in self._state.image_delivered_target_keys
            if text_delivered and image_delivered:
                continue
            target = configured_target_map.get(target_key)
            if target is None:
                continue
            try:
                stream_id = await self._resolve_stream_id(target)
                if stream_id is None:
                    continue
                resolved_stream_ids[target_key] = stream_id
                if text_delivered:
                    continue
                text_sent = await self.ctx.send.text(message, stream_id)
            except Exception:
                self.ctx.logger.error("Codex 重置文本通知投递异常: target=%s", target_key, exc_info=True)
                continue

            if not text_sent:
                self.ctx.logger.error("Codex 重置文本通知投递失败: target=%s", target_key)
                continue
            self._state.text_delivered_target_keys.add(target_key)
            self._save_state()
            self.ctx.logger.info("Codex 重置文本通知已投递: target=%s", target_key)

        pending_image_target_keys = [
            target_key
            for target_key in self._state.target_keys
            if target_key in self._state.text_delivered_target_keys
            and target_key not in self._state.image_delivered_target_keys
            and target_key in resolved_stream_ids
        ]
        if not pending_image_target_keys:
            return

        try:
            reset_image = await self._capture_reset_image(snapshot)
        except (OSError, PlaywrightError, ValueError) as exception:
            self.ctx.logger.error("Codex 主页重置信息截图生成失败: %s", exception, exc_info=True)
            return

        for target_key in pending_image_target_keys:
            try:
                image_sent = await self.ctx.send.image(reset_image, resolved_stream_ids[target_key])
            except Exception:
                self.ctx.logger.error("Codex 重置图片投递异常: target=%s", target_key, exc_info=True)
                continue
            if not image_sent:
                self.ctx.logger.error("Codex 重置图片投递失败: target=%s", target_key)
                continue
            self._state.image_delivered_target_keys.add(target_key)
            self._save_state()
            self.ctx.logger.info("Codex 重置图片已投递: target=%s", target_key)

    async def _capture_reset_image(self, snapshot: ResetSnapshot) -> str:
        """使用 Chrome 打开主页并截取 API 对应的重置公告。"""

        async with self._screenshot_lock:
            browser_executable = self._resolve_browser_executable()
            launch_options: Dict[str, Any] = {"headless": True}
            if browser_executable is not None:
                launch_options["executable_path"] = browser_executable

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(**launch_options)
                try:
                    page = await browser.new_page(
                        viewport={
                            "width": SCREENSHOT_VIEWPORT_WIDTH,
                            "height": SCREENSHOT_VIEWPORT_HEIGHT,
                        },
                        device_scale_factor=2,
                        locale="zh-CN",
                        timezone_id="Asia/Shanghai",
                        color_scheme="light",
                    )
                    await page.goto(
                        SITE_URL,
                        wait_until="domcontentloaded",
                        timeout=SCREENSHOT_TIMEOUT_MS,
                    )
                    await page.wait_for_timeout(SCREENSHOT_STABILIZE_MS)
                    await page.evaluate("() => document.fonts.ready")

                    announcement_items = page.locator(RESET_ANNOUNCEMENT_SELECTOR)
                    target_index = await announcement_items.evaluate_all(
                        """(announcements, target) => announcements.findIndex(announcement => {
                            const marker = announcement.querySelector(target.selector);
                            return marker?.getAttribute(target.attribute) === target.value;
                        })""",
                        {
                            "selector": (
                                ANNOUNCEMENT_LINK_SELECTOR
                                if snapshot.source_url is not None
                                else ANNOUNCEMENT_TIME_SELECTOR
                            ),
                            "attribute": "href" if snapshot.source_url is not None else "data-datetime",
                            "value": snapshot.source_url if snapshot.source_url is not None else snapshot.reset_at,
                        },
                    )
                    if target_index < 0:
                        raise ValueError(
                            f"主页中未找到 API 公告: id={snapshot.reset_id}, "
                            f"source_url={snapshot.source_url!r}, time={snapshot.reset_at}"
                        )
                    target_announcement = announcement_items.nth(target_index)
                    await target_announcement.wait_for(state="attached", timeout=SCREENSHOT_TIMEOUT_MS)

                    # 公告可能位于折叠历史中，截图前将它移入主列表并标记为唯一目标。
                    await target_announcement.evaluate(
                        f"""announcement => {{
                            const section = announcement.closest('{ANNOUNCEMENT_SECTION_SELECTOR}');
                            const primaryList = section?.querySelector(':scope > .log-list');
                            if (!primaryList) {{
                                throw new Error('公告区缺少主列表');
                            }}
                            announcement.setAttribute('{ANNOUNCEMENT_TARGET_ATTRIBUTE}', '');
                            if (announcement.parentElement !== primaryList) {{
                                primaryList.prepend(announcement);
                            }}
                        }}"""
                    )
                    await target_announcement.wait_for(state="visible", timeout=SCREENSHOT_TIMEOUT_MS)

                    avatar = target_announcement.locator(ANNOUNCEMENT_AVATAR_SELECTOR)
                    await avatar.wait_for(state="visible", timeout=SCREENSHOT_TIMEOUT_MS)
                    await avatar.evaluate("image => image.decode()")

                    # 用持久样式隐藏其他帖子，避免网站定时更新 DOM 后将它们重新显示。
                    await page.add_style_tag(content=ANNOUNCEMENT_CROP_STYLE)
                    await target_announcement.wait_for(state="visible", timeout=SCREENSHOT_TIMEOUT_MS)
                    await page.wait_for_timeout(100)

                    announcement_section = page.locator(ANNOUNCEMENT_SECTION_SELECTOR)
                    await announcement_section.wait_for(state="visible", timeout=SCREENSHOT_TIMEOUT_MS)
                    image_bytes = await announcement_section.screenshot(
                        type="png",
                        animations="disabled",
                        timeout=SCREENSHOT_TIMEOUT_MS,
                    )
                finally:
                    await browser.close()

        if not image_bytes:
            raise ValueError("主页重置信息截图为空")
        return base64.b64encode(image_bytes).decode("ascii")

    @staticmethod
    def _resolve_browser_executable() -> Optional[str]:
        """优先使用系统 Chrome/Chromium，未找到时使用 Playwright 托管的 Chromium。"""

        for executable_name in BROWSER_EXECUTABLE_NAMES:
            executable_path = shutil.which(executable_name)
            if executable_path is not None:
                return executable_path
        return None

    async def _resolve_stream_id(self, target: NotificationTarget) -> Optional[str]:
        """通过 SDK 解析已存在的真实 QQ 聊天流。"""

        if target.chat_type == "group":
            stream = await self.ctx.chat.get_stream_by_group_id(target.target_id, platform="qq")
        elif target.chat_type == "private":
            stream = await self.ctx.chat.get_stream_by_user_id(target.target_id, platform="qq")
        else:
            raise ValueError(f"不支持的聊天类型: {target.chat_type}")

        if stream is None:
            self.ctx.logger.warning(
                "未找到白名单 QQ 目标对应的已有聊天流，将在下次扫描重试: target=%s",
                target.key,
            )
            return None
        if not isinstance(stream, dict):
            raise ValueError(f"QQ 目标 {target.key} 的聊天流响应不是字典")

        stream_id = stream.get("session_id")
        if not isinstance(stream_id, str) or not stream_id.strip():
            raise ValueError(f"QQ 目标 {target.key} 的聊天流缺少有效 session_id")
        return stream_id.strip()

    def _configured_targets(self) -> List[NotificationTarget]:
        """按配置顺序构造并去重 QQ 通知目标。"""

        targets: List[NotificationTarget] = []
        seen_keys: Set[str] = set()
        configured_groups = self.config.whitelist.group_whitelist
        configured_users = self.config.whitelist.private_whitelist
        for chat_type, configured_ids in (("group", configured_groups), ("private", configured_users)):
            for configured_id in configured_ids:
                target_id = configured_id.strip()
                if not target_id:
                    continue
                target = NotificationTarget(chat_type=chat_type, target_id=target_id)
                if target.key in seen_keys:
                    continue
                seen_keys.add(target.key)
                targets.append(target)
        return targets

    @staticmethod
    def _state_from_snapshot(snapshot: ResetSnapshot, target_keys: List[str]) -> MonitorState:
        """为新发现的重置创建待投递状态。"""

        return MonitorState(
            reset_id=snapshot.reset_id,
            reset_at=snapshot.reset_at,
            source_url=snapshot.source_url,
            target_keys=list(dict.fromkeys(target_keys)),
            text_delivered_target_keys=set(),
            image_delivered_target_keys=set(),
        )

    @classmethod
    def _build_notification_message(cls, reset_at: str) -> str:
        """构造包含原始 UTC 时间和北京时间的通知文本。"""

        reset_timestamp = cls._parse_timestamp(reset_at)
        utc_time = reset_timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        beijing_time = reset_timestamp.astimezone(BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S 北京时间")
        return f"Latest Codex limit reset：{utc_time}\n\n最新的codex额度重置时间：{beijing_time}"

    @staticmethod
    def _required_string(payload: Dict[str, Any], field_name: str, source_name: str) -> str:
        """读取必需的非空字符串字段。"""

        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{source_name}.{field_name} 必须是非空字符串")
        return value.strip()

    @staticmethod
    def _parse_timestamp(timestamp: str) -> datetime:
        """解析网站使用的 ISO 8601 时间戳。"""

        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exception:
            raise ValueError(f"无法解析重置时间: {timestamp}") from exception
        if parsed.tzinfo is None:
            raise ValueError(f"重置时间缺少时区: {timestamp}")
        return parsed

    @classmethod
    def _timestamps_within_tolerance(cls, first: str, second: str, tolerance_seconds: int) -> bool:
        """判断两个带时区的时间戳是否在允许偏差内。"""

        difference_seconds = abs((cls._parse_timestamp(first) - cls._parse_timestamp(second)).total_seconds())
        return difference_seconds <= tolerance_seconds

    @property
    def _state_path(self) -> Path:
        """返回 Runner 授权的插件状态路径。"""

        return self.ctx.paths.data_dir / STATE_FILE_NAME

    def _load_state(self) -> Optional[MonitorState]:
        """从插件专属数据目录加载状态。"""

        state_path = self._state_path
        if not state_path.exists():
            return None
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exception:
            raise ValueError(f"无法读取状态文件 {state_path}: {exception}") from exception
        if not isinstance(payload, dict):
            raise ValueError(f"状态文件 {state_path} 的 JSON 顶层必须是对象")
        return MonitorState.from_dict(payload)

    def _save_state(self) -> None:
        """原子写入当前监控状态。"""

        if self._state is None:
            raise RuntimeError("不能保存空的监控状态")
        state_path = self._state_path
        temporary_path = state_path.with_suffix(".json.tmp")
        serialized_state = json.dumps(self._state.to_dict(), ensure_ascii=False, indent=2)
        temporary_path.write_text(f"{serialized_state}\n", encoding="utf-8")
        temporary_path.replace(state_path)


def create_plugin() -> CodexResetNotifyPlugin:
    """创建 Codex 额度重置通知插件实例。"""

    return CodexResetNotifyPlugin()

"""Codex 额度重置通知插件单元测试。"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, List

import asyncio
import logging
import sys


def _load_plugin_module() -> ModuleType:
    """从带横线的插件目录加载 plugin.py。"""

    plugin_path = Path(__file__).resolve().parents[1] / "plugin.py"
    specification = spec_from_file_location("codex_reset_notify_plugin", plugin_path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法创建插件加载规格: {plugin_path}")
    module = module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


plugin_module = _load_plugin_module()


def test_snapshot_from_reset_list_selects_latest_regular_reset() -> None:
    """快照跳过 banked 公告，选取最新的 regular 重置。"""

    reset_list: Dict[str, Any] = {
        "data": [
            {
                "id": "banked-1",
                "reset_type": "banked",
                "announced_at": "2026-09-01T00:00:00.000Z",
                "text": "Banked reset.",
                "source": {
                    "type": "observed",
                    "url": None,
                },
            },
            {
                "id": "2094251180121854309",
                "reset_type": "regular",
                "announced_at": "2026-08-31T02:29:25.000Z",
                "text": "Usage reset for all paid Codex users.",
                "source": {
                    "type": "x_post",
                    "url": "https://x.com/thsottiaux/status/2094251180121854309",
                },
            },
        ]
    }

    snapshot = plugin_module.CodexResetNotifyPlugin._snapshot_from_reset_list(reset_list)

    assert snapshot == plugin_module.ResetSnapshot(
        reset_id="2094251180121854309",
        reset_at="2026-08-31T02:29:25.000Z",
        source_url="https://x.com/thsottiaux/status/2094251180121854309",
    )


def test_notification_message_has_utc_and_beijing_times() -> None:
    """通知保留 UTC 时间，并在空行后显示北京时间。"""

    message = plugin_module.CodexResetNotifyPlugin._build_notification_message("2026-08-31T02:34:27.000Z")

    assert message == (
        "Latest Codex limit reset：2026-08-31 02:34:27 UTC\n\n最新的codex额度重置时间：2026-08-31 10:34:27 北京时间"
    )


def test_monitor_state_round_trip() -> None:
    """状态文件保留逐目标投递结果。"""

    state = plugin_module.MonitorState(
        reset_id="2094251180121854309",
        reset_at="2026-08-31T02:34:27.000Z",
        source_url="https://x.com/thsottiaux/status/2094251180121854309",
        target_keys=["group:10001", "private:20002"],
        text_delivered_target_keys={"group:10001"},
        image_delivered_target_keys=set(),
    )

    restored = plugin_module.MonitorState.from_dict(state.to_dict())

    assert restored == state


def test_monitor_state_reads_legacy_announcement_fields() -> None:
    """旧版状态中的公告字段会被安全忽略并升级到新版本。"""

    legacy_state: Dict[str, Any] = {
        "version": 1,
        "reset_at": "2026-08-31T02:34:27.000Z",
        "announcement_id": "reset-1",
        "announcement": "Old reset announcement.",
        "target_keys": ["group:10001"],
        "delivered_target_keys": ["group:10001"],
    }

    restored = plugin_module.MonitorState.from_dict(legacy_state)

    assert restored.to_dict() == {
        "version": 4,
        "reset_id": None,
        "reset_at": "2026-08-31T02:34:27.000Z",
        "source_url": None,
        "target_keys": ["group:10001"],
        "text_delivered_target_keys": ["group:10001"],
        "image_delivered_target_keys": ["group:10001"],
    }


def test_equivalent_timestamp_formats_are_the_same_instant() -> None:
    """网站只改变 ISO 格式时不应被识别为新的重置时刻。"""

    timestamp_with_milliseconds = plugin_module.CodexResetNotifyPlugin._parse_timestamp("2026-08-31T02:34:27.000Z")
    timestamp_without_milliseconds = plugin_module.CodexResetNotifyPlugin._parse_timestamp("2026-08-31T02:34:27Z")

    assert timestamp_with_milliseconds == timestamp_without_milliseconds


def test_legacy_state_migrates_without_resetting_delivery_progress() -> None:
    """旧站点的同一公告迁移后保留已有的逐目标投递进度。"""

    async def run_scan():
        plugin = plugin_module.CodexResetNotifyPlugin()
        plugin._ctx = SimpleNamespace(logger=logging.getLogger("test.codex_reset_notify.migration"))
        plugin._state = plugin_module.MonitorState(
            reset_id=None,
            reset_at="2026-08-31T02:34:27.000Z",
            source_url=None,
            target_keys=["group:10001"],
            text_delivered_target_keys={"group:10001"},
            image_delivered_target_keys={"group:10001"},
        )
        latest_snapshot = plugin_module.ResetSnapshot(
            reset_id="2094251180121854309",
            reset_at="2026-08-31T02:29:25.000Z",
            source_url="https://x.com/thsottiaux/status/2094251180121854309",
        )
        delivery_called = False

        async def fetch_snapshot():
            return latest_snapshot

        async def deliver_notifications():
            nonlocal delivery_called
            delivery_called = True

        plugin._fetch_latest_snapshot = fetch_snapshot
        plugin._deliver_pending_notifications = deliver_notifications
        plugin._save_state = lambda: None

        await plugin._scan_once()
        return plugin._state, delivery_called

    state, delivery_called = asyncio.run(run_scan())

    assert state == plugin_module.MonitorState(
        reset_id="2094251180121854309",
        reset_at="2026-08-31T02:29:25.000Z",
        source_url="https://x.com/thsottiaux/status/2094251180121854309",
        target_keys=["group:10001"],
        text_delivered_target_keys={"group:10001"},
        image_delivered_target_keys={"group:10001"},
    )
    assert delivery_called is True


def test_new_reset_is_detected_by_id_even_when_time_is_unchanged() -> None:
    """公告 ID 改变时必须通知，不依赖时间字符串是否改变。"""

    async def run_scan():
        plugin = plugin_module.CodexResetNotifyPlugin()
        plugin._ctx = SimpleNamespace(logger=logging.getLogger("test.codex_reset_notify.new_id"))
        plugin._state = plugin_module.MonitorState(
            reset_id="reset-old",
            reset_at="2026-08-31T02:29:25.000Z",
            source_url="https://x.com/example/status/reset-old",
            target_keys=[],
            text_delivered_target_keys=set(),
            image_delivered_target_keys=set(),
        )
        latest_snapshot = plugin_module.ResetSnapshot(
            reset_id="reset-new",
            reset_at="2026-08-31T02:29:25.000Z",
            source_url="https://x.com/example/status/reset-new",
        )
        delivery_called = False

        async def fetch_snapshot():
            return latest_snapshot

        async def deliver_notifications():
            nonlocal delivery_called
            delivery_called = True

        plugin._fetch_latest_snapshot = fetch_snapshot
        plugin._configured_targets = lambda: [plugin_module.NotificationTarget(chat_type="group", target_id="10001")]
        plugin._deliver_pending_notifications = deliver_notifications
        plugin._save_state = lambda: None

        await plugin._scan_once()
        return plugin._state, delivery_called

    state, delivery_called = asyncio.run(run_scan())

    assert state.reset_id == "reset-new"
    assert state.target_keys == ["group:10001"]
    assert delivery_called is True


def test_reset_command_fetches_and_sends_latest_notification() -> None:
    """/reset 会依次发送实时重置文本和主页截图。"""

    class FakeSendCapability:
        def __init__(self, events: List[Any]) -> None:
            self.events = events

        async def text(self, text: str, stream_id: str) -> bool:
            self.events.append(("text", text, stream_id))
            return True

        async def image(self, image_data: str, stream_id: str) -> bool:
            self.events.append(("image", image_data, stream_id))
            return True

    class FakeContext:
        def __init__(self, events: List[Any]) -> None:
            self.send = FakeSendCapability(events)
            self.logger = logging.getLogger("test.codex_reset_notify")

    async def run_command():
        events: List[Any] = []
        plugin = plugin_module.CodexResetNotifyPlugin()
        fake_context = FakeContext(events)
        plugin._ctx = fake_context

        async def fetch_snapshot():
            return plugin_module.ResetSnapshot(
                reset_id="2094251180121854309",
                reset_at="2026-08-31T02:34:27.000Z",
                source_url="https://x.com/thsottiaux/status/2094251180121854309",
            )

        async def capture_reset_image(snapshot):
            events.append(("capture", snapshot))
            return "reset-image-base64"

        plugin._fetch_latest_snapshot = fetch_snapshot
        plugin._capture_reset_image = capture_reset_image
        result = await plugin.handle_reset_command(stream_id="stream-1")
        return result, events

    result, events = asyncio.run(run_command())

    assert result == (True, "已返回最近的 Codex 重置通知", True)
    assert events == [
        (
            "text",
            "Latest Codex limit reset：2026-08-31 02:34:27 UTC\n\n"
            "最新的codex额度重置时间：2026-08-31 10:34:27 北京时间",
            "stream-1",
        ),
        (
            "capture",
            plugin_module.ResetSnapshot(
                reset_id="2094251180121854309",
                reset_at="2026-08-31T02:34:27.000Z",
                source_url="https://x.com/thsottiaux/status/2094251180121854309",
            ),
        ),
        ("image", "reset-image-base64", "stream-1"),
    ]


def test_automatic_notification_retries_only_failed_image() -> None:
    """自动通知截图首次失败后，下一轮只重试图片。"""

    class FakeSendCapability:
        def __init__(self, events: List[Any]) -> None:
            self.events = events

        async def text(self, text: str, stream_id: str) -> bool:
            self.events.append(("text", text, stream_id))
            return True

        async def image(self, image_data: str, stream_id: str) -> bool:
            self.events.append(("image", image_data, stream_id))
            return True

    class FakeContext:
        def __init__(self, events: List[Any]) -> None:
            self.send = FakeSendCapability(events)
            self.logger = logging.getLogger("test.codex_reset_notify.retry")

    async def run_delivery():
        events: List[Any] = []
        plugin = plugin_module.CodexResetNotifyPlugin()
        plugin._ctx = FakeContext(events)
        plugin._state = plugin_module.MonitorState(
            reset_id="2094251180121854309",
            reset_at="2026-08-31T02:34:27.000Z",
            source_url="https://x.com/thsottiaux/status/2094251180121854309",
            target_keys=["group:10001"],
            text_delivered_target_keys=set(),
            image_delivered_target_keys=set(),
        )
        plugin._configured_targets = lambda: [plugin_module.NotificationTarget(chat_type="group", target_id="10001")]
        plugin._save_state = lambda: None

        async def resolve_stream_id(target):
            assert target.key == "group:10001"
            return "stream-1"

        screenshot_attempt = 0

        async def capture_reset_image(snapshot):
            nonlocal screenshot_attempt
            screenshot_attempt += 1
            events.append(("capture", screenshot_attempt, snapshot))
            if screenshot_attempt == 1:
                raise ValueError("模拟截图失败")
            return "reset-image-base64"

        plugin._resolve_stream_id = resolve_stream_id
        plugin._capture_reset_image = capture_reset_image

        await plugin._deliver_pending_notifications()
        first_state = (
            set(plugin._state.text_delivered_target_keys),
            set(plugin._state.image_delivered_target_keys),
        )
        await plugin._deliver_pending_notifications()
        second_state = (
            set(plugin._state.text_delivered_target_keys),
            set(plugin._state.image_delivered_target_keys),
        )
        return events, first_state, second_state

    events, first_state, second_state = asyncio.run(run_delivery())

    assert events == [
        (
            "text",
            "Latest Codex limit reset：2026-08-31 02:34:27 UTC\n\n"
            "最新的codex额度重置时间：2026-08-31 10:34:27 北京时间",
            "stream-1",
        ),
        (
            "capture",
            1,
            plugin_module.ResetSnapshot(
                reset_id="2094251180121854309",
                reset_at="2026-08-31T02:34:27.000Z",
                source_url="https://x.com/thsottiaux/status/2094251180121854309",
            ),
        ),
        (
            "capture",
            2,
            plugin_module.ResetSnapshot(
                reset_id="2094251180121854309",
                reset_at="2026-08-31T02:34:27.000Z",
                source_url="https://x.com/thsottiaux/status/2094251180121854309",
            ),
        ),
        ("image", "reset-image-base64", "stream-1"),
    ]
    assert first_state == ({"group:10001"}, set())
    assert second_state == ({"group:10001"}, {"group:10001"})

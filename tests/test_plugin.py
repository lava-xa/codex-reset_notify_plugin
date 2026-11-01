"""Codex 额度重置通知插件单元测试。"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, List

import asyncio
import inspect
import logging
import sys
import tomllib

from maibot_sdk import MaiBotPlugin
from maibot_sdk.context import PluginContext, PluginPaths


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


def test_sdk_factory_lifecycle_and_command_contract() -> None:
    """真实 SDK 能发现入口、生命周期、命令和插件路径接口。"""

    plugin = plugin_module.create_plugin()
    assert isinstance(plugin, MaiBotPlugin)
    for method_name in ("on_load", "on_unload", "on_config_update"):
        assert inspect.iscoroutinefunction(getattr(plugin, method_name))
        assert getattr(type(plugin), method_name) is not getattr(MaiBotPlugin, method_name)
    assert plugin.get_config_reload_subscriptions() == []
    assert isinstance(PluginContext("codex-reset.notify-plugin").paths.data_dir, Path)
    components = plugin.get_components()
    assert len(components) == 1
    assert components[0]["type"] == "COMMAND"
    assert components[0]["metadata"]["command_pattern"] == r"^/reset$"


def test_config_example_matches_sdk_defaults_and_generates_schema() -> None:
    """示例可经 SDK 注入，默认不向任何目标发送通知。"""

    example_path = Path(__file__).resolve().parents[1] / "config.example.toml"
    example = tomllib.loads(example_path.read_text(encoding="utf-8"))
    plugin = plugin_module.create_plugin()
    plugin.set_plugin_config(example)
    assert plugin.get_plugin_config_data() == plugin.build_default_config()
    assert plugin._configured_targets() == []
    schema = plugin.build_config_schema(plugin_id="codex-reset.notify-plugin")
    assert schema["sections"]["whitelist"]["fields"]["group_whitelist"]["item_type"] == "string"


def test_stream_lookup_uses_real_sdk_response_normalization() -> None:
    """验证 Host 包装结果经过 SDK 解包后能解析群聊及私聊流。"""

    async def run_lookups():
        calls = []

        async def rpc_call(method, plugin_id, payload):
            assert method == "cap.call"
            assert plugin_id == "codex-reset.notify-plugin"
            calls.append(payload)
            target_id = payload["args"].get("group_id") or payload["args"].get("user_id")
            return {
                "success": True,
                "stream": None if target_id == "missing" else {"session_id": f"stream-{target_id}"},
            }

        plugin = plugin_module.create_plugin()
        plugin._ctx = PluginContext("codex-reset.notify-plugin", rpc_call=rpc_call)
        assert await plugin._resolve_stream_id(plugin_module.NotificationTarget("group", "10001")) == "stream-10001"
        assert await plugin._resolve_stream_id(plugin_module.NotificationTarget("private", "20002")) == "stream-20002"
        assert await plugin._resolve_stream_id(plugin_module.NotificationTarget("group", "missing")) is None
        return calls

    calls = asyncio.run(run_lookups())
    assert calls[0] == {
        "capability": "chat.get_stream_by_group_id",
        "args": {"group_id": "10001", "platform": "qq"},
    }
    assert calls[1] == {
        "capability": "chat.get_stream_by_user_id",
        "args": {"user_id": "20002", "platform": "qq"},
    }


def test_lifecycle_hot_update_stops_monitor_and_closes_session(tmp_path) -> None:
    """真实配置更新与卸载会取消后台任务、关闭会话并使用分配的数据目录。"""

    async def run_lifecycle():
        plugin = plugin_module.create_plugin()
        plugin._ctx = PluginContext("codex-reset.notify-plugin", paths=PluginPaths(
            data_dir=tmp_path / "data", runtime_dir=tmp_path / "runtime",
        ))
        config = plugin.build_default_config()
        config["plugin"]["enabled"] = False
        plugin.set_plugin_config(config)

        async def idle_monitor():
            await asyncio.Event().wait()

        # 仅隔离自动轮询，生命周期和 SDK 配置注入走真实实现。
        plugin._monitor_loop = idle_monitor
        await plugin.on_load()
        session = plugin._http_session
        try:
            assert plugin._state_path == tmp_path / "data" / "state.json"
            assert plugin._state_path.parent.is_dir()
            assert session is not None and not session.closed
            assert plugin._monitor_task is None

            config["plugin"]["enabled"] = True
            plugin.set_plugin_config(config)
            await plugin.on_config_update("self", config, "1.0.0")
            monitor_task = plugin._monitor_task
            assert monitor_task is not None
            await asyncio.sleep(0)
            await plugin.on_config_update("bot", {}, "1.0.0")
            assert plugin._monitor_task is monitor_task

            config["plugin"]["enabled"] = False
            plugin.set_plugin_config(config)
            await plugin.on_config_update("self", config, "1.0.0")
            assert plugin._monitor_task is None
            assert monitor_task.cancelled()
        finally:
            await plugin.on_unload()
        assert session.closed
        assert plugin._http_session is None

    asyncio.run(run_lifecycle())


def test_snapshot_from_reset_list_selects_latest_banked_reset() -> None:
    """最新公告是 banked 时不能跳过它去返回旧的 regular 重置。"""

    reset_list: Dict[str, Any] = {
        "data": [
            {
                "id": "banked-1",
                "reset_type": "banked",
                "announced_at": "2026-09-01T00:00:00.000Z",
                "text": "Banked reset.",
                "source": {
                    "type": "x_post",
                    "url": "https://x.com/thsottiaux/status/banked-1",
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
        reset_id="banked-1",
        reset_at="2026-09-01T00:00:00.000Z",
        source_url="https://x.com/thsottiaux/status/banked-1",
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

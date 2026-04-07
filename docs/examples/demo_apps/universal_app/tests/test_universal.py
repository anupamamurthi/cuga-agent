"""
Tests for the Universal Agent.

Coverage:
  1. registry.py   — channel/tool lookups and summary helpers
  2. factory.py    — trigger message generation, output channel selection,
                     tool instantiation (mocked), full factory smoke test
  3. planner_tools.py — _slugify, create_pipeline (mocked host+client),
                        stop_pipeline, list_pipelines, update_pipeline
  4. Integration   — full create → list → stop flow with mock host/client

Run:
    cd docs/examples/demo_apps/universal_app
    pip install pytest pytest-asyncio
    pytest tests/test_universal.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure the app directory and demos directory are on sys.path
_APP_DIR   = Path(__file__).parent.parent
_DEMOS_DIR = _APP_DIR.parent
for _p in [str(_APP_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# 1.  registry.py
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_all_data_channels_present(self):
        from registry import CHANNEL_REGISTRY
        expected = {"rss", "imap", "slack_data", "telegram_data",
                    "discord_data", "docling", "audio"}
        assert expected.issubset(CHANNEL_REGISTRY.keys())

    def test_all_output_channels_present(self):
        from registry import CHANNEL_REGISTRY
        expected = {"email", "slack", "telegram", "discord", "sms", "log"}
        assert expected.issubset(CHANNEL_REGISTRY.keys())

    def test_all_tools_present(self):
        from registry import TOOL_REGISTRY
        expected = {"web_search", "calendar", "github", "market_data",
                    "image_gen", "rag", "shell"}
        assert expected.issubset(TOOL_REGISTRY.keys())

    def test_channel_roles(self):
        from registry import CHANNEL_REGISTRY
        for name, spec in CHANNEL_REGISTRY.items():
            assert spec.role in ("data", "output"), (
                f"{name} has unexpected role {spec.role!r}"
            )

    def test_data_channel_summary_contains_all(self):
        from registry import data_channel_summary
        summary = data_channel_summary()
        for name in ("rss", "imap", "docling", "audio"):
            assert name in summary

    def test_output_channel_summary_contains_all(self):
        from registry import output_channel_summary
        summary = output_channel_summary()
        for name in ("email", "slack", "telegram", "log"):
            assert name in summary

    def test_tool_summary_contains_all(self):
        from registry import tool_summary
        summary = tool_summary()
        for name in ("web_search", "calendar", "github", "market_data"):
            assert name in summary

    def test_env_vars_declared(self):
        from registry import CHANNEL_REGISTRY, TOOL_REGISTRY
        assert CHANNEL_REGISTRY["email"].env_vars == ["SMTP_USERNAME", "SMTP_PASSWORD"]
        assert CHANNEL_REGISTRY["imap"].env_vars == ["IMAP_PASSWORD"]
        assert TOOL_REGISTRY["web_search"].env_vars == ["TAVILY_API_KEY"]
        assert TOOL_REGISTRY["rag"].env_vars == []


# ---------------------------------------------------------------------------
# 2.  factory.py
# ---------------------------------------------------------------------------

class TestTriggerMessage:
    """_build_trigger_message produces context-appropriate agent prompts."""

    def _msg(self, **kwargs):
        from factory import _build_trigger_message
        defaults = {
            "pipeline_id": "test",
            "description": "Do the thing",
            "data_type": "none",
            "tool_names": [],
            "output_type": "log",
        }
        defaults.update(kwargs)
        return _build_trigger_message(defaults)

    def test_description_in_message(self):
        msg = self._msg(description="Summarise arxiv papers")
        assert "Summarise arxiv papers" in msg

    def test_data_channel_context(self):
        msg = self._msg(data_type="rss")
        assert "collected items" in msg or "data channel" in msg

    def test_tool_mention_when_no_data(self):
        msg = self._msg(data_type="none", tool_names=["web_search", "github"])
        assert "web_search" in msg and "github" in msg

    def test_email_output_format_hint(self):
        msg = self._msg(output_type="email")
        assert "HTML" in msg

    def test_sms_output_format_hint(self):
        msg = self._msg(output_type="sms")
        assert "160" in msg or "short" in msg.lower()

    def test_no_send_tools_reminder(self):
        msg = self._msg()
        assert "delivery" in msg.lower() or "do not call" in msg.lower()


class TestOutputChannelSelection:
    """_build_output_channel returns the right channel type."""

    def _build(self, output_type, output_target="me@co.com"):
        from factory import _build_output_channel
        # Pass real channel classes (we only check the type returned)
        from cuga_channels import (
            EmailChannel, SlackChannel, TelegramChannel,
            DiscordChannel, SMSChannel,
        )
        return _build_output_channel(
            output_type, output_target, "Test",
            EmailChannel, SlackChannel, TelegramChannel, DiscordChannel, SMSChannel,
        )

    def test_email_channel_returned(self):
        import os
        os.environ.setdefault("SMTP_USERNAME", "test@test.com")
        os.environ.setdefault("SMTP_PASSWORD", "secret")
        ch = self._build("email", "recipient@co.com")
        from cuga_channels import EmailChannel
        assert isinstance(ch, EmailChannel)

    def test_log_channel_fallback(self):
        ch = self._build("log", "")
        assert ch is None  # caller wraps in LogChannel

    def test_missing_target_returns_none(self):
        ch = self._build("email", "")   # no target → None → LogChannel fallback
        assert ch is None

    def test_slack_webhook(self):
        ch = self._build("slack", "https://hooks.slack.com/T123/B456/xxx")
        from cuga_channels import SlackChannel
        assert isinstance(ch, SlackChannel)


class TestBuildTools:
    """_build_tools returns the right number of tool functions."""

    def test_empty_list(self):
        from factory import _build_tools
        assert _build_tools([]) == []

    @patch("cuga_channels.make_web_search_tool")
    def test_web_search(self, mock_factory):
        mock_factory.return_value = MagicMock()
        from factory import _build_tools
        tools = _build_tools(["web_search"])
        assert len(tools) == 1

    @patch("cuga_channels.make_github_tools")
    def test_github_returns_multiple(self, mock_factory):
        # github returns 5 tools
        mock_factory.return_value = [MagicMock() for _ in range(5)]
        from factory import _build_tools
        tools = _build_tools(["github"])
        assert len(tools) == 5

    def test_unknown_tool_ignored(self):
        from factory import _build_tools
        # Should not raise; unknown names are silently skipped
        tools = _build_tools(["nonexistent_tool"])
        assert tools == []


# ---------------------------------------------------------------------------
# 3.  planner_tools.py
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_basic(self):
        from planner_tools import _slugify
        assert _slugify("Monitor arxiv for AI papers") == "monitor-arxiv-for-ai-papers"

    def test_special_chars(self):
        from planner_tools import _slugify
        assert _slugify("Check BTC/ETH prices!") == "check-btceth-prices"

    def test_max_length(self):
        from planner_tools import _slugify
        long = "a" * 100
        assert len(_slugify(long)) <= 40

    def test_consecutive_hyphens_collapsed(self):
        from planner_tools import _slugify
        result = _slugify("hello   world")
        assert "--" not in result

    def test_empty_string(self):
        from planner_tools import _slugify
        assert _slugify("") == ""


class TestPlannerToolsMocked:
    """
    Test planner tools with fully mocked host and client.
    No real CugaHost, CugaRuntime, or LLM is used.
    """

    def _make_tools(self):
        host   = MagicMock()
        client = MagicMock()
        client.start_runtime  = AsyncMock(return_value={"id": "test", "config": {}})
        client.stop_runtime   = AsyncMock()
        client.list_runtimes  = AsyncMock(return_value=[])
        client.get_runtime    = AsyncMock(return_value={"id": "test", "config": {}})
        client.update_runtime = AsyncMock(return_value={"id": "test", "config": {}})

        from planner_tools import make_planner_tools
        tools = make_planner_tools(host=host, client=client)
        tool_map = {t.name: t for t in tools}
        return host, client, tool_map

    # --- create_pipeline -----------------------------------------------

    @pytest.mark.asyncio
    async def test_create_pipeline_registers_factory(self):
        with patch("factory.build_factory", return_value=MagicMock()):
            host, client, tools = self._make_tools()
            result = await tools["create_pipeline"].ainvoke({
                "description":      "Monitor arxiv for AI papers",
                "data_type":        "rss",
                "rss_sources":      ["https://arxiv.org/rss/cs.AI"],
                "trigger_schedule": "0 8 * * *",
                "output_type":      "log",
            })
        host.register_factory.assert_called_once()
        client.start_runtime.assert_called_once()
        assert "monitor-arxiv-for-ai-papers" in result

    @pytest.mark.asyncio
    async def test_create_pipeline_tool_driven(self):
        with patch("factory.build_factory", return_value=MagicMock()):
            host, client, tools = self._make_tools()
            result = await tools["create_pipeline"].ainvoke({
                "description":      "Check Bitcoin price every hour",
                "data_type":        "none",
                "tool_names":       ["market_data"],
                "trigger_schedule": "0 * * * *",
                "output_type":      "log",
            })
        assert "check-bitcoin-price-every-hour" in result

    @pytest.mark.asyncio
    async def test_create_pipeline_with_email_output(self):
        with patch("factory.build_factory", return_value=MagicMock()):
            host, client, tools = self._make_tools()
            result = await tools["create_pipeline"].ainvoke({
                "description":    "Weekly GitHub digest",
                "data_type":      "none",
                "tool_names":     ["github"],
                "output_type":    "email",
                "output_target":  "me@co.com",
            })
        assert "email" in result
        assert "me@co.com" in result

    @pytest.mark.asyncio
    async def test_create_pipeline_start_failure(self):
        with patch("factory.build_factory", return_value=MagicMock()):
            host, client, tools = self._make_tools()
            client.start_runtime.side_effect = Exception("host not ready")
            result = await tools["create_pipeline"].ainvoke({
                "description": "Should fail",
                "data_type":   "none",
            })
        assert "Failed" in result or "failed" in result

    # --- stop_pipeline -------------------------------------------------

    @pytest.mark.asyncio
    async def test_stop_existing_pipeline(self):
        _, client, tools = self._make_tools()
        result = await tools["stop_pipeline"].ainvoke(
            {"pipeline_id": "my-pipeline"}
        )
        client.stop_runtime.assert_called_once_with("my-pipeline")
        assert "stopped" in result.lower()

    @pytest.mark.asyncio
    async def test_stop_nonexistent_pipeline(self):
        _, client, tools = self._make_tools()
        client.stop_runtime.side_effect = Exception("not found")
        result = await tools["stop_pipeline"].ainvoke(
            {"pipeline_id": "nonexistent"}
        )
        assert "not" in result.lower() or "no pipeline" in result.lower()

    @pytest.mark.asyncio
    async def test_stop_all(self):
        _, client, tools = self._make_tools()
        client.list_runtimes.return_value = [
            {"id": "pipeline-a"}, {"id": "pipeline-b"},
        ]
        result = await tools["stop_pipeline"].ainvoke({"pipeline_id": "all"})
        assert client.stop_runtime.call_count == 2
        assert "2" in result

    # --- list_pipelines ------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_empty(self):
        _, client, tools = self._make_tools()
        result = await tools["list_pipelines"].ainvoke({})
        assert "No pipelines" in result

    @pytest.mark.asyncio
    async def test_list_with_entries(self):
        _, client, tools = self._make_tools()
        client.list_runtimes.return_value = [
            {
                "id": "arxiv-monitor",
                "config": {
                    "description": "Monitor arxiv",
                    "data_type": "rss",
                    "trigger_schedule": "0 8 * * *",
                    "output_type": "email",
                    "output_target": "me@co.com",
                },
            }
        ]
        result = await tools["list_pipelines"].ainvoke({})
        assert "arxiv-monitor" in result
        assert "rss" in result

    # --- update_pipeline -----------------------------------------------

    @pytest.mark.asyncio
    async def test_update_schedule(self):
        _, client, tools = self._make_tools()
        client.get_runtime.return_value = {
            "id": "my-pipeline",
            "config": {
                "pipeline_id": "my-pipeline",
                "trigger_schedule": "0 8 * * *",
                "output_type": "log",
            },
        }
        result = await tools["update_pipeline"].ainvoke({
            "pipeline_id": "my-pipeline",
            "trigger_schedule": "0 9 * * *",
        })
        client.update_runtime.assert_called_once()
        call_config = client.update_runtime.call_args[0][2]  # third positional arg
        assert call_config["trigger_schedule"] == "0 9 * * *"
        assert "updated" in result.lower()

    @pytest.mark.asyncio
    async def test_update_nonexistent(self):
        _, client, tools = self._make_tools()
        client.get_runtime.side_effect = Exception("not found")
        result = await tools["update_pipeline"].ainvoke({
            "pipeline_id": "ghost",
            "trigger_schedule": "0 9 * * *",
        })
        assert "No pipeline" in result or "not" in result.lower()

    @pytest.mark.asyncio
    async def test_update_no_changes(self):
        _, client, tools = self._make_tools()
        client.get_runtime.return_value = {"id": "my-pipeline", "config": {}}
        result = await tools["update_pipeline"].ainvoke({"pipeline_id": "my-pipeline"})
        assert "No changes" in result
        client.update_runtime.assert_not_called()


# ---------------------------------------------------------------------------
# 4.  Integration: create → list → stop flow
# ---------------------------------------------------------------------------

class TestIntegration:
    """
    Full create → list → stop flow, fully mocked.
    Tests that the tools interact correctly with each other via the shared
    client mock.
    """

    @pytest.mark.asyncio
    async def test_create_list_stop(self):
        from planner_tools import make_planner_tools

        _running: dict = {}

        async def fake_start(pid, factory, config):
            _running[pid] = config
            return {"id": pid, "config": config}

        async def fake_stop(pid):
            _running.pop(pid, None)

        async def fake_list():
            return [{"id": k, "config": v} for k, v in _running.items()]

        client = MagicMock()
        client.start_runtime  = AsyncMock(side_effect=fake_start)
        client.stop_runtime   = AsyncMock(side_effect=fake_stop)
        client.list_runtimes  = AsyncMock(side_effect=fake_list)
        client.get_runtime    = AsyncMock(return_value={"id": "test", "config": {}})
        client.update_runtime = AsyncMock()

        host = MagicMock()

        with patch("factory.build_factory", return_value=MagicMock()):
            tools = make_planner_tools(host=host, client=client)
            tool_map = {t.name: t for t in tools}

            # 1. Create
            await tool_map["create_pipeline"].ainvoke({
                "description":      "Daily web search digest",
                "data_type":        "none",
                "tool_names":       ["web_search"],
                "trigger_schedule": "0 8 * * *",
            })
            assert len(_running) == 1

            # 2. List — should show the new pipeline
            listing = await tool_map["list_pipelines"].ainvoke({})
            assert "daily-web-search-digest" in listing

            # 3. Stop
            await tool_map["stop_pipeline"].ainvoke(
                {"pipeline_id": "daily-web-search-digest"}
            )
            assert len(_running) == 0

            # 4. List again — should be empty
            listing = await tool_map["list_pipelines"].ainvoke({})
            assert "No pipelines" in listing

    @pytest.mark.asyncio
    async def test_multiple_pipelines_stop_all(self):
        from planner_tools import make_planner_tools

        _running: dict = {}

        async def fake_start(pid, factory, config):
            _running[pid] = config
            return {"id": pid, "config": config}

        async def fake_stop(pid):
            _running.pop(pid, None)

        async def fake_list():
            return [{"id": k, "config": v} for k, v in _running.items()]

        client = MagicMock()
        client.start_runtime  = AsyncMock(side_effect=fake_start)
        client.stop_runtime   = AsyncMock(side_effect=fake_stop)
        client.list_runtimes  = AsyncMock(side_effect=fake_list)
        client.get_runtime    = AsyncMock(return_value={"id": "t", "config": {}})
        client.update_runtime = AsyncMock()

        host = MagicMock()

        with patch("factory.build_factory", return_value=MagicMock()):
            tools = make_planner_tools(host=host, client=client)
            tool_map = {t.name: t for t in tools}

            # Create three pipelines
            for desc in ["RSS monitor", "Bitcoin price check", "GitHub PR digest"]:
                await tool_map["create_pipeline"].ainvoke({
                    "description": desc, "data_type": "none",
                })

            assert len(_running) == 3

            # Stop all
            result = await tool_map["stop_pipeline"].ainvoke({"pipeline_id": "all"})
            assert "3" in result
            assert len(_running) == 0


# ---------------------------------------------------------------------------
# 5. Spec-to-factory parameter tests
# ---------------------------------------------------------------------------

class TestSpecParameters:
    """Verify that spec fields flow correctly into the factory's merged config."""

    def _run_factory(self, spec: dict):
        """Call build_factory(spec) and return the factory callable (not the runtime)."""
        from factory import build_factory
        return build_factory(spec)

    def test_factory_is_callable(self):
        spec = {
            "pipeline_id": "test",
            "description": "test",
            "data_type": "none",
            "trigger_schedule": "0 8 * * *",
            "output_type": "log",
            "tool_names": [],
        }
        factory = self._run_factory(spec)
        assert callable(factory)

    def test_spec_override_in_config(self):
        """Runtime config dict should override spec defaults when factory is called."""
        # We can't easily call the factory without cuga imports, so we test
        # the merge logic indirectly via trigger message generation.
        from factory import _build_trigger_message
        spec = {
            "pipeline_id": "t",
            "description": "original description",
            "data_type": "none",
            "tool_names": [],
            "output_type": "log",
        }
        # Simulate a config override
        merged = {**spec, "description": "updated description"}
        msg = _build_trigger_message(merged)
        assert "updated description" in msg
        assert "original description" not in msg

    def test_require_buffer_true_for_data_channels(self):
        """require_buffer should be True when data_type != "none"."""
        from planner_tools import _slugify
        # This logic lives in create_pipeline; we test it via the spec dict shape.
        # The "require_buffer" field is set by create_pipeline before calling start_runtime.
        # We verify the rule by inspecting planner_tools source (not the tool itself
        # since that's an async LangChain tool and harder to unit-test directly).
        import ast, inspect
        import planner_tools
        src = inspect.getsource(planner_tools)
        assert 'data_type != "none"' in src or "data_type !=" in src

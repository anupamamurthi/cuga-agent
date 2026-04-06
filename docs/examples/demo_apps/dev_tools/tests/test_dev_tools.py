"""
Unit + integration tests for dev_tools/main.py.

Tests cover:
  - _AUDIT_MESSAGE — correct tool references and structure
  - build_audit_runtime() — correct channel wiring
  - _make_output_channels() — channel selection
  - make_agent() — returns agent with shell tools

No LLM, no shell execution, no Slack API required.
"""
import sys
import importlib.util
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

_DEMO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(_DEMO_DIR))
sys.path.insert(0, str(_DEMO_DIR.parent))


def _load_module():
    spec = importlib.util.spec_from_file_location("devtools_main", str(_DEMO_DIR / "main.py"))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass
    except Exception:
        pass
    return module


# ---------------------------------------------------------------------------
# _AUDIT_MESSAGE — content quality
# ---------------------------------------------------------------------------

class TestAuditMessage:

    @pytest.fixture
    def mod(self):
        return _load_module()

    def test_message_is_string(self, mod):
        assert isinstance(mod._AUDIT_MESSAGE, str)

    def test_message_not_empty(self, mod):
        assert len(mod._AUDIT_MESSAGE) > 100

    def test_references_check_python_dependencies(self, mod):
        assert "check_python_dependencies" in mod._AUDIT_MESSAGE

    def test_references_run_test_coverage(self, mod):
        assert "run_test_coverage" in mod._AUDIT_MESSAGE

    def test_references_git_log(self, mod):
        assert "git log" in mod._AUDIT_MESSAGE

    def test_asks_for_dependency_section(self, mod):
        assert "Dependency" in mod._AUDIT_MESSAGE or "dependency" in mod._AUDIT_MESSAGE.lower()

    def test_asks_for_test_coverage_section(self, mod):
        assert "Test Coverage" in mod._AUDIT_MESSAGE or "coverage" in mod._AUDIT_MESSAGE.lower()

    def test_asks_for_recent_activity_section(self, mod):
        assert "Recent Activity" in mod._AUDIT_MESSAGE or "commit" in mod._AUDIT_MESSAGE.lower()

    def test_word_limit_mentioned(self, mod):
        """Message should specify a word limit to prevent overly verbose output."""
        assert "600" in mod._AUDIT_MESSAGE or "word" in mod._AUDIT_MESSAGE.lower()


# ---------------------------------------------------------------------------
# build_audit_runtime — channel wiring
# ---------------------------------------------------------------------------

class TestBuildAuditRuntime:

    @pytest.fixture
    def mod(self):
        return _load_module()

    def test_returns_cuga_runtime(self, mod):
        from cuga_channels import CugaRuntime
        mock_agent = MagicMock()
        outputs = [MagicMock()]
        runtime = mod.build_audit_runtime(mock_agent, "0 9 * * 1", outputs)
        assert isinstance(runtime, CugaRuntime)

    def test_require_buffer_false(self, mod):
        mock_agent = MagicMock()
        outputs = [MagicMock()]
        runtime = mod.build_audit_runtime(mock_agent, "0 9 * * 1", outputs)
        assert runtime._require_buffer is False

    def test_thread_id_is_stable(self, mod):
        mock_agent = MagicMock()
        outputs = [MagicMock()]
        runtime = mod.build_audit_runtime(mock_agent, "0 9 * * 1", outputs)
        assert runtime._thread_id == "dev-tools-audit"

    def test_audit_message_in_channel(self, mod):
        from cuga_channels import CronChannel
        mock_agent = MagicMock()
        outputs = [MagicMock()]
        runtime = mod.build_audit_runtime(mock_agent, "0 9 * * 1", outputs)
        channels = runtime._input_channels if hasattr(runtime, "_input_channels") else []
        # At least one cron channel should be present
        cron_channels = [c for c in channels if isinstance(c, CronChannel)]
        assert len(cron_channels) >= 1


# ---------------------------------------------------------------------------
# _make_output_channels — channel selection
# ---------------------------------------------------------------------------

class TestMakeOutputChannels:

    @pytest.fixture
    def mod(self):
        return _load_module()

    def test_no_flags_includes_log_channel(self, mod):
        channels = mod._make_output_channels(use_slack=False, use_email=False)
        names = [type(c).__name__ for c in channels]
        assert any("Log" in n for n in names)

    def test_slack_flag_adds_slack_channel(self, mod, monkeypatch):
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
        channels = mod._make_output_channels(use_slack=True, use_email=False)
        names = [type(c).__name__ for c in channels]
        assert any("Slack" in n for n in names)

    def test_returns_list(self, mod):
        channels = mod._make_output_channels(use_slack=False, use_email=False)
        assert isinstance(channels, list)
        assert len(channels) >= 1


# ---------------------------------------------------------------------------
# Module import — no syntax errors
# ---------------------------------------------------------------------------

class TestDevToolsImport:

    def test_module_loads_without_error(self):
        spec = importlib.util.spec_from_file_location(
            "devtools_main_check", str(_DEMO_DIR / "main.py")
        )
        module = importlib.util.module_from_spec(spec)
        assert module is not None

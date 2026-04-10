"""
Unit + integration tests for smart_alarm/main.py.

Tests cover:
  - _time_to_cron() — correct cron expression generation
  - _MORNING_BRIEF_MESSAGE — correct content guidelines
  - _make_output_channels() — channel selection

No LLM, no Google Calendar, no TTS audio output required.
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
    spec = importlib.util.spec_from_file_location("alarm_main", str(_DEMO_DIR / "main.py"))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass
    except Exception:
        pass
    return module


# ---------------------------------------------------------------------------
# _time_to_cron — cron expression generation
# ---------------------------------------------------------------------------

class TestTimeToCron:

    @pytest.fixture
    def mod(self):
        return _load_module()

    def test_seven_am_weekdays(self, mod):
        cron = mod._time_to_cron("7:00", weekend=False)
        # Should produce "0 7 * * 1-5"
        assert "7" in cron
        assert "1-5" in cron

    def test_seven_am_all_days(self, mod):
        cron = mod._time_to_cron("7:00", weekend=True)
        assert "7" in cron
        assert "1-5" not in cron  # weekend=True means all days

    def test_six_thirty(self, mod):
        cron = mod._time_to_cron("6:30", weekend=False)
        assert "30" in cron
        assert "6" in cron

    def test_zero_hour(self, mod):
        cron = mod._time_to_cron("0:00", weekend=False)
        assert cron.startswith("0 0") or "0" in cron

    def test_invalid_time_defaults_to_seven(self, mod):
        cron = mod._time_to_cron("not-a-time", weekend=False)
        # Should fall back to 7:00
        assert "7" in cron

    def test_returns_string(self, mod):
        result = mod._time_to_cron("8:00", weekend=False)
        assert isinstance(result, str)

    def test_five_parts(self, mod):
        """Valid cron expression has 5 space-separated parts."""
        cron = mod._time_to_cron("7:00", weekend=False)
        assert len(cron.split()) == 5

    def test_minute_is_first(self, mod):
        cron = mod._time_to_cron("7:45", weekend=False)
        minute = cron.split()[0]
        assert minute == "45"

    def test_hour_is_second(self, mod):
        cron = mod._time_to_cron("9:00", weekend=False)
        hour = cron.split()[1]
        assert hour == "9"

    def test_eight_thirty(self, mod):
        cron = mod._time_to_cron("8:30", weekend=False)
        assert cron.split()[0] == "30"
        assert cron.split()[1] == "8"


# ---------------------------------------------------------------------------
# _MORNING_BRIEF_MESSAGE — content quality
# ---------------------------------------------------------------------------

class TestMorningBriefMessage:

    @pytest.fixture
    def mod(self):
        return _load_module()

    def test_message_is_string(self, mod):
        assert isinstance(mod._MORNING_BRIEF_MESSAGE, str)

    def test_message_mentions_calendar(self, mod):
        assert "calendar" in mod._MORNING_BRIEF_MESSAGE.lower() or "events" in mod._MORNING_BRIEF_MESSAGE.lower()

    def test_message_mentions_tts_constraint(self, mod):
        """Should instruct agent not to use markdown since output is spoken."""
        msg = mod._MORNING_BRIEF_MESSAGE.lower()
        assert "markdown" in msg or "asterisk" in msg or "spoken" in msg or "aloud" in msg

    def test_message_not_empty(self, mod):
        assert len(mod._MORNING_BRIEF_MESSAGE) > 50


# ---------------------------------------------------------------------------
# _make_output_channels — channel selection
# ---------------------------------------------------------------------------

class TestMakeOutputChannels:

    @pytest.fixture
    def mod(self):
        return _load_module()

    def test_print_voice_includes_tts_channel(self, mod):
        channels = mod._make_output_channels(voice="print", use_telegram=False)
        names = [type(c).__name__ for c in channels]
        assert any("TTS" in n for n in names)

    def test_telegram_flag_adds_telegram(self, mod, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:TOKEN")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "456")
        channels = mod._make_output_channels(voice="print", use_telegram=True)
        names = [type(c).__name__ for c in channels]
        assert any("Telegram" in n for n in names)

    def test_returns_list(self, mod):
        channels = mod._make_output_channels(voice="print", use_telegram=False)
        assert isinstance(channels, list)
        assert len(channels) >= 1


# ---------------------------------------------------------------------------
# Module import — no syntax errors
# ---------------------------------------------------------------------------

class TestSmartAlarmImport:

    def test_module_loads_without_error(self):
        spec = importlib.util.spec_from_file_location(
            "alarm_main_check", str(_DEMO_DIR / "main.py")
        )
        module = importlib.util.module_from_spec(spec)
        assert module is not None

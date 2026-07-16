"""
testing/test_f006_f007_f008.py — Unit tests for F-006 Telegram, F-007 TTS, F-008 Scheduler.

All offline — no real Telegram bot calls, no audio output, no network.
"""

import os
import threading
import time
from unittest.mock import MagicMock, patch


# ── F-007 TTS ────────────────────────────────────────────────────────────────

class TestTTSTools:
    def test_speak_empty_text_returns_failure(self):
        from tools.tools_tts import TTSTools
        tts = TTSTools()
        result = tts.speak("")
        assert result["success"] is False

    def test_speak_engine_init_failure_graceful(self):
        from tools.tools_tts import TTSTools
        tts = TTSTools()
        # Force engine to None — simulates missing pyttsx3 or audio driver
        tts._engine = None
        with patch("tools.tools_tts.TTSTools._get_engine", return_value=None):
            result = tts.speak("hello")
        assert result["success"] is False
        assert "error" in result

    def test_speak_async_returns_immediately(self):
        from tools.tools_tts import TTSTools
        tts = TTSTools()
        # speak_async should return without blocking even if engine call would be slow
        mock_engine = MagicMock()
        mock_engine.say = MagicMock()
        mock_engine.runAndWait = MagicMock()
        tts._engine = mock_engine
        result = tts.speak_async("test message")
        assert result["success"] is True
        assert result["chars"] == len("test message")

    def test_speak_success_with_mock_engine(self):
        from tools.tools_tts import TTSTools
        tts = TTSTools()
        mock_engine = MagicMock()
        tts._engine = mock_engine
        result = tts.speak("hello world")
        assert result["success"] is True
        mock_engine.say.assert_called_once_with("hello world")
        mock_engine.runAndWait.assert_called_once()

    def test_list_voices_no_engine_returns_error(self):
        from tools.tools_tts import TTSTools
        tts = TTSTools()
        with patch.object(tts, "_get_engine", return_value=None):
            result = tts.list_voices()
        assert result["success"] is False

    def test_list_voices_with_mock_engine(self):
        from tools.tools_tts import TTSTools
        tts = TTSTools()
        mock_voice = MagicMock()
        mock_voice.id = "voice-1"
        mock_voice.name = "Microsoft David"
        mock_engine = MagicMock()
        mock_engine.getProperty.return_value = [mock_voice]
        tts._engine = mock_engine
        result = tts.list_voices()
        assert result["success"] is True
        assert result["count"] == 1
        assert result["voices"][0]["name"] == "Microsoft David"

    def test_save_to_wav_uses_pyttsx3(self, tmp_path):
        from tools.tools_tts import TTSTools
        tts = TTSTools()
        out = str(tmp_path / "test.wav")
        mock_engine = MagicMock()
        tts._engine = mock_engine
        result = tts.save("save me", out)
        assert result["success"] is True
        mock_engine.save_to_file.assert_called_once_with("save me", out)


# ── F-006 Telegram ────────────────────────────────────────────────────────────

class TestTelegramSendMessage:
    def test_no_token_returns_error(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove token from environment so _TOKEN is empty
            from tools import tools_telegram
            orig_token = tools_telegram._TOKEN
            orig_chat = tools_telegram._ALLOWED_CHAT_ID
            tools_telegram._TOKEN = ""
            tools_telegram._ALLOWED_CHAT_ID = ""
            try:
                result = tools_telegram.send_message("hello")
            finally:
                tools_telegram._TOKEN = orig_token
                tools_telegram._ALLOWED_CHAT_ID = orig_chat
        assert result["success"] is False

    def test_no_chat_id_returns_error(self):
        from tools import tools_telegram
        orig_token = tools_telegram._TOKEN
        orig_chat = tools_telegram._ALLOWED_CHAT_ID
        tools_telegram._TOKEN = "fake-token"
        tools_telegram._ALLOWED_CHAT_ID = ""
        try:
            result = tools_telegram.send_message("hello")
        finally:
            tools_telegram._TOKEN = orig_token
            tools_telegram._ALLOWED_CHAT_ID = orig_chat
        assert result["success"] is False
        assert "chat_id" in result["error"]

    def test_send_success_with_mock_bot(self):
        from tools import tools_telegram
        orig_token = tools_telegram._TOKEN
        orig_chat = tools_telegram._ALLOWED_CHAT_ID
        tools_telegram._TOKEN = "fake-token"
        tools_telegram._ALLOWED_CHAT_ID = "123456"
        mock_bot = MagicMock()
        with patch("tools.tools_telegram._get_bot", return_value=mock_bot):
            result = tools_telegram.send_message("test message")
        tools_telegram._TOKEN = orig_token
        tools_telegram._ALLOWED_CHAT_ID = orig_chat
        assert result["success"] is True
        # T-219: send_message now passes parse_mode='HTML'; check chat_id and text
        args, kwargs = mock_bot.send_message.call_args
        assert args[0] == 123456
        assert "test message" in args[1]  # _format_for_telegram may escape but preserves text

    def test_send_exception_returns_failure(self):
        from tools import tools_telegram
        orig_token = tools_telegram._TOKEN
        orig_chat = tools_telegram._ALLOWED_CHAT_ID
        tools_telegram._TOKEN = "fake-token"
        tools_telegram._ALLOWED_CHAT_ID = "123"
        mock_bot = MagicMock()
        mock_bot.send_message.side_effect = Exception("network error")
        with patch("tools.tools_telegram._get_bot", return_value=mock_bot):
            result = tools_telegram.send_message("oops")
        tools_telegram._TOKEN = orig_token
        tools_telegram._ALLOWED_CHAT_ID = orig_chat
        assert result["success"] is False
        assert "network error" in result["error"]


class TestTelegramTools:
    def _make_tools(self):
        from tools.tools_telegram import TelegramTools
        agent = MagicMock()
        agent.process_input.return_value = "pong"
        tools = TelegramTools(agent=agent)
        return tools, agent

    def test_is_available_false_without_token(self):
        from tools import tools_telegram
        orig = tools_telegram._TOKEN
        tools_telegram._TOKEN = ""
        from tools.tools_telegram import TelegramTools
        t = TelegramTools(agent=MagicMock())
        assert t.is_available() is False
        tools_telegram._TOKEN = orig

    def test_chunk_text_splits_long_messages(self):
        from tools.tools_telegram import _chunk_text
        text = "x" * 10000
        chunks = list(_chunk_text(text, 4096))
        assert len(chunks) == 3
        assert all(len(c) <= 4096 for c in chunks)

    def test_chunk_text_short_message_single_chunk(self):
        from tools.tools_telegram import _chunk_text
        text = "short message"
        chunks = list(_chunk_text(text, 4096))
        assert len(chunks) == 1
        assert chunks[0] == text


# ── F-008 Scheduler ───────────────────────────────────────────────────────────

class TestPiScheduler:
    def _make_scheduler(self):
        from tools.tools_scheduler import PiScheduler
        agent = MagicMock()
        agent.memory = MagicMock()
        agent.awareness = MagicMock()
        return PiScheduler(agent=agent)

    def test_list_jobs_initially_empty(self):
        sched = self._make_scheduler()
        result = sched.list_jobs()
        assert result["count"] == 0
        assert result["jobs"] == []

    def test_start_registers_default_jobs(self):
        sched = self._make_scheduler()
        import schedule as sched_lib
        sched_lib.clear()
        sched.start()
        result = sched.list_jobs()
        assert result["running"] is True
        # T-085 R4: daily_briefing + memory_prune + weekly_audit.
        # T-083 R2.3: tool_usage_audit. T-087 R6: replication_log_rotate.
        # T-259: turns_log_rotate. T-285: vault_sync. Seven default jobs.
        assert result["count"] == 7
        job_ids = {j["id"] for j in result["jobs"]}
        assert job_ids == {
            "daily_briefing", "memory_prune", "weekly_audit",
            "tool_usage_audit", "replication_log_rotate", "turns_log_rotate",
            "vault_sync",
        }
        sched.stop()
        sched_lib.clear()

    def test_add_daily_registers_job(self):
        sched = self._make_scheduler()
        import schedule as sched_lib
        sched_lib.clear()
        result = sched.add_daily("12:00", lambda: None, "test_job", "A test job")
        assert result["success"] is True
        assert result["id"] == "test_job"
        assert sched.list_jobs()["count"] == 1
        sched_lib.clear()

    def test_add_interval_registers_job(self):
        sched = self._make_scheduler()
        import schedule as sched_lib
        sched_lib.clear()
        result = sched.add_interval(30, lambda: None, "interval_job")
        assert result["success"] is True
        assert sched.list_jobs()["count"] == 1
        sched_lib.clear()

    def test_summarise_for_speech_removes_headers(self):
        from tools.tools_scheduler import _summarise_for_speech
        text = "# Header\n\nThis is a sentence. Another one.\n\n## Section\n\nMore content."
        summary = _summarise_for_speech(text)
        assert "#" not in summary
        assert "sentence" in summary or "content" in summary

    def test_summarise_for_speech_max_chars(self):
        from tools.tools_scheduler import _summarise_for_speech
        long_text = "A " * 1000
        summary = _summarise_for_speech(long_text, max_chars=100)
        assert len(summary) <= 200  # some headroom for joining

    def test_memory_prune_job_calls_both_prunes(self):
        """T-085 R4: _l3_prune_job renamed _memory_prune_job; now also calls prune_l2_stale."""
        sched = self._make_scheduler()
        sched._agent.memory.prune_l3_expired.return_value = {"success": True, "sqlite_deleted": 3}
        sched._agent.memory.prune_l2_stale.return_value = {"success": True, "archived": 1}
        result = sched._memory_prune_job()
        assert result["l3"] == {"success": True, "sqlite_deleted": 3}
        assert result["l2"] == {"success": True, "archived": 1}
        assert result["errors"] == []
        sched._agent.memory.prune_l3_expired.assert_called_once()
        sched._agent.memory.prune_l2_stale.assert_called_once()

    def test_vault_sync_job_calls_sync_vault(self):
        """T-285: daily vault sync job calls tools_obsidian.sync_vault(agent.memory)."""
        sched = self._make_scheduler()
        with patch("tools.tools_obsidian.sync_vault") as mock_sync:
            mock_sync.return_value = {"success": True, "per_ticket": {"written": 5}}
            result = sched._vault_sync_job()
        mock_sync.assert_called_once_with(sched._agent.memory)
        assert result == {"success": True, "per_ticket": {"written": 5}}

    def test_vault_sync_job_survives_exception(self):
        """A sync failure must not crash the scheduler thread."""
        sched = self._make_scheduler()
        with patch("tools.tools_obsidian.sync_vault", side_effect=RuntimeError("boom")):
            result = sched._vault_sync_job()
        assert result["success"] is False
        assert "boom" in result["error"]

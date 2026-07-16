"""testing/test_normie_tools.py — T-201: normie read-tier toolset.

Unit tests only (no live network). Verify that normie mode exposes
read-tier tools while blocking write/side-effect tools.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.modes import MODE_CONFIGS, get_mode_config


# ── 1. Allowlist structure ─────────────────────────────────────────────────────

def test_normie_supports_tools():
    cfg = MODE_CONFIGS["normie"]
    assert cfg.supports_tools is True, "normie must have supports_tools=True after T-201"


def test_normie_has_explicit_allowlist():
    cfg = MODE_CONFIGS["normie"]
    assert cfg.tool_allowlist is not None, "normie needs an explicit allowlist, not None (all)"
    assert len(cfg.tool_allowlist) > 0, "allowlist must not be empty"


def test_normie_allowlist_contains_media_analysis():
    """Telegram image → analyze_media must be allowed (the T-201 bug root)."""
    cfg = MODE_CONFIGS["normie"]
    for tool in ("analyze_media", "analyze_image", "analyze_images", "analyze_video"):
        assert tool in cfg.tool_allowlist, (
            f"{tool!r} must be in normie allowlist — Pi was refusing Telegram image analysis"
        )


def test_normie_allowlist_contains_core_read_tools():
    cfg = MODE_CONFIGS["normie"]
    expected = {
        "memory_read", "memory_write", "memory_search_semantic",
        "web_search", "fetch",
        "get_weather", "get_news", "get_location",
        "calendar_today", "calendar_upcoming",
        "gmail_inbox", "gmail_read",
        "obsidian_read", "obsidian_search",
    }
    missing = expected - set(cfg.tool_allowlist)
    assert not missing, f"Read-tier tools missing from normie allowlist: {missing}"


# ── 2. Write tools are ABSENT from normie ─────────────────────────────────────

# telegram_send + image_gen are deliberately NOT in this set (T-249/S-189,
# 2026-07-04): normie needs telegram_send for reactions and to auto-deliver
# generated images; image_gen is free (Pollinations) and its output is
# auto-delivered at the dispatch layer, so the model never chains a
# separate write call. Every other write/side-effect tool stays blocked.
WRITE_TOOLS = {
    "modify_file", "create_file", "read_file",
    "execute_bash", "execute_python",
    "gmail_send",
    "calendar_create", "calendar_delete",
    "obsidian_write", "obsidian_append",
    "generate_video",
    "browser_click", "browser_fill", "browser_open",
    "computer_click", "computer_type", "computer_key",
    "register_face",
}


def test_write_tools_absent_from_normie():
    cfg = MODE_CONFIGS["normie"]
    allowed = set(cfg.tool_allowlist)
    leaked = WRITE_TOOLS & allowed
    assert not leaked, (
        f"Write-class tools must NOT be in normie allowlist but found: {leaked}"
    )


# ── 3. _filtered_tool_defs honours the allowlist ──────────────────────────────

def test_filtered_tool_defs_excludes_write_tools():
    """_filtered_tool_defs on normie cfg must not include any write-class tool."""
    from unittest.mock import patch, MagicMock
    from agent.modes import get_mode_config

    cfg = get_mode_config("normie")

    # Fake a full tool list that includes both read and write tools
    fake_defs = [
        {"name": "analyze_media"},
        {"name": "web_search"},
        {"name": "memory_read"},
        {"name": "modify_file"},        # write — must be filtered
        {"name": "execute_bash"},       # write — must be filtered
        {"name": "gmail_send"},         # write — must be filtered
        {"name": "telegram_send"},      # T-249/S-189: deliberately allowed (reactions + image auto-delivery)
    ]

    # We need a minimal agent-like object with _get_tool_definitions
    class FakeAgent:
        def _get_tool_definitions(self):
            return fake_defs

        def _filtered_tool_defs(self, cfg):
            from pi_agent import PiAgent
            return PiAgent._filtered_tool_defs(self, cfg)

    agent = FakeAgent()
    result = agent._filtered_tool_defs(cfg)
    result_names = {d["name"] for d in result}

    assert "analyze_media" in result_names, "analyze_media should pass the filter"
    assert "web_search" in result_names, "web_search should pass the filter"
    assert "memory_read" in result_names, "memory_read should pass the filter"
    assert "modify_file" not in result_names, "modify_file must be blocked in normie"
    assert "execute_bash" not in result_names, "execute_bash must be blocked in normie"
    assert "gmail_send" not in result_names, "gmail_send must be blocked in normie"
    assert "telegram_send" in result_names, "telegram_send is deliberately allowed in normie (T-249/S-189)"


# ── 4. Schema translation round-trip for normie tool defs ─────────────────────

def test_normie_tool_defs_translate_to_openai_format():
    """Normie tools must survive anthropic→openai schema translation (Groq path)."""
    from core.schema_translate import anthropic_to_openai_tools

    sample_normie_tools = [
        {
            "name": "analyze_media",
            "description": "Analyze an image or video",
            "input_schema": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
        {
            "name": "web_search",
            "description": "Search the web",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    ]

    converted = anthropic_to_openai_tools(sample_normie_tools)
    assert len(converted) == 2
    for item in converted:
        assert item["type"] == "function"
        assert "function" in item
        assert "name" in item["function"]
        assert "parameters" in item["function"]


# ── 5. Root mode still has all tools (no regression) ─────────────────────────

def test_root_mode_tool_allowlist_is_none():
    """Root mode must keep tool_allowlist=None (all tools available)."""
    cfg = MODE_CONFIGS["root"]
    assert cfg.tool_allowlist is None, "Root must have tool_allowlist=None (unrestricted)"
    assert cfg.supports_tools is True

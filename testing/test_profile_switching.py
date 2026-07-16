"""testing/test_profile_switching.py — T-223: profile_switch context manager.

Verifies turn-scoped isolation: memory, consciousness, and current_profile are
swapped for the duration of the block and restored afterward, even on exception.
"""
import sys
import os
import uuid
import tempfile
import pathlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock
from agent.profile import Profile, profile_switch, profile_to_memory


def _make_profile(name: str, tmp_path: pathlib.Path) -> Profile:
    class _TP(Profile):
        def __init__(self, _name, _path):
            super().__init__(
                id=str(uuid.uuid4()), name=_name, display_name=_name, nickname="",
                password_hash="h", salt="s",
                is_guest=(_name != "ash"), allowlist_json="[]", created_at="now"
            )
            self._path = _path

        @property
        def db_path(self):
            return str(self._path)

        @property
        def namespace(self):
            return "pi" if self.name == "ash" else f"profile_{self.name}"

        @property
        def consciousness_path(self):
            return "prompts/consciousness.txt" if self.name == "ash" else "prompts/consciousness_guest.txt"

    db = tmp_path / f"pi_{name}.db"
    return _TP(name, db)


def _make_agent(tmp_path: pathlib.Path, name: str = "ash") -> MagicMock:
    agent = MagicMock()
    agent.current_profile = None
    agent.memory = MagicMock()
    agent.memory._supabase_url = ""
    agent.memory._supabase_key = ""
    agent.consciousness = "ASH_CONSCIOUSNESS"
    agent.conversation_id = "ash-conv"
    agent.messages = []
    return agent


# ── Basic switch and restore ──────────────────────────────────────────────────

def test_profile_switch_sets_current_profile(tmp_path):
    agent = _make_agent(tmp_path)
    guest = _make_profile("alice", tmp_path)
    with profile_switch(agent, guest):
        assert agent.current_profile is guest


def test_profile_switch_restores_after_block(tmp_path):
    agent = _make_agent(tmp_path)
    original_profile = None  # ash has None before profile system
    agent.current_profile = original_profile
    guest = _make_profile("alice", tmp_path)
    with profile_switch(agent, guest):
        pass
    assert agent.current_profile is original_profile


def test_profile_switch_restores_memory_after_block(tmp_path):
    agent = _make_agent(tmp_path)
    original_memory = agent.memory
    guest = _make_profile("alice", tmp_path)
    with profile_switch(agent, guest):
        assert agent.memory is not original_memory  # switched
    assert agent.memory is original_memory  # restored


def test_profile_switch_restores_on_exception(tmp_path):
    agent = _make_agent(tmp_path)
    original_memory = agent.memory
    original_consciousness = agent.consciousness
    guest = _make_profile("alice", tmp_path)
    try:
        with profile_switch(agent, guest):
            raise RuntimeError("deliberate error in turn")
    except RuntimeError:
        pass
    assert agent.memory is original_memory
    assert agent.consciousness == original_consciousness
    assert agent.current_profile is None


# ── Isolation: guest's memory is different from Ash's ────────────────────────

def test_guest_turn_uses_different_memory_than_ash(tmp_path):
    agent = _make_agent(tmp_path)
    ash_memory = agent.memory
    guest = _make_profile("alice", tmp_path)
    with profile_switch(agent, guest):
        guest_memory = agent.memory
    assert guest_memory is not ash_memory


def test_guest_memory_has_guest_namespace(tmp_path):
    agent = _make_agent(tmp_path)
    guest = _make_profile("alice", tmp_path)
    with profile_switch(agent, guest):
        assert agent.memory.namespace == "profile_alice"


# ── No cross-bleed between consecutive turns ──────────────────────────────────

def test_consecutive_turns_no_bleed(tmp_path):
    """Ash turn followed by guest turn: Ash's memory unchanged after guest."""
    agent = _make_agent(tmp_path)
    ash_memory = agent.memory

    guest = _make_profile("alice", tmp_path)

    # Guest turn
    with profile_switch(agent, guest):
        assert agent.memory is not ash_memory

    # Back to Ash turn
    assert agent.memory is ash_memory
    assert agent.consciousness == "ASH_CONSCIOUSNESS"


# ── Nested switch leaves agent state identical to start ──────────────────────

def test_nested_switch_restores_fully(tmp_path):
    agent = _make_agent(tmp_path)
    original_memory = agent.memory
    original_consciousness = agent.consciousness
    original_profile = agent.current_profile

    guest_a = _make_profile("alice", tmp_path)
    guest_b = _make_profile("bob", tmp_path)

    with profile_switch(agent, guest_a):
        with profile_switch(agent, guest_b):
            pass  # inner switch

    # After all switches, agent is fully restored
    assert agent.memory is original_memory
    assert agent.consciousness == original_consciousness
    assert agent.current_profile is original_profile


if __name__ == "__main__":
    import inspect
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for name, fn in tests:
        with tempfile.TemporaryDirectory() as td:
            try:
                sig = inspect.signature(fn)
                params = list(sig.parameters.keys())
                fn(pathlib.Path(td)) if "tmp_path" in params else fn()
                print(f"  PASS  {name}")
                passed += 1
            except Exception as e:
                import traceback
                print(f"  FAIL  {name}: {e}")
                traceback.print_exc()
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)

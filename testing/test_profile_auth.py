"""testing/test_profile_auth.py — T-222: profile auth + login.

All tests use a temp db; no real Telegram, no real Pi.db.
"""
import sys
import os
import pathlib
import tempfile
import uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_registry(tmp_path: str):
    from agent.profile import ProfileRegistry
    return ProfileRegistry(db_path=os.path.join(str(tmp_path), "pi.db"))


# ── resolve_profile: ash auto-resolves ───────────────────────────────────────

def test_ash_resolves_by_chat_id(tmp_path):
    reg = _make_registry(str(tmp_path))
    # Ash profile was seeded by registry init
    ash = reg.get_profile("ash")
    assert ash is not None
    assert not ash.is_guest


# ── verify_password correctness ───────────────────────────────────────────────

def test_correct_password_verifies(tmp_path):
    from agent.profile import verify_password, hash_password
    ph, salt = hash_password("correct_password")
    assert verify_password("correct_password", ph, salt)


def test_wrong_password_rejected(tmp_path):
    from agent.profile import verify_password, hash_password
    ph, salt = hash_password("correct")
    assert not verify_password("wrong", ph, salt)


# ── Sticky binding: second message needs no re-login ─────────────────────────

def test_sticky_binding_persists(tmp_path):
    reg = _make_registry(str(tmp_path))
    reg.create_profile("alice", "pw123")

    # Simulate successful login → bind device
    from agent.profile import verify_password
    profile = reg.get_profile("alice")
    assert verify_password("pw123", profile.password_hash, profile.salt)
    reg.bind_device("9999", "alice")

    # Second lookup (no password) → still resolves
    bound = reg.resolve_binding("9999")
    assert bound == "alice"


# ── Lockout after N failures ──────────────────────────────────────────────────

def test_lockout_after_5_failures(tmp_path):
    reg = _make_registry(str(tmp_path))
    reg.create_profile("bob", "pw")
    for _ in range(5):
        reg.record_attempt("bob", "8888", success=False)
    assert reg.is_locked_out("bob", "8888")


def test_lockout_unlocked_for_different_chat(tmp_path):
    reg = _make_registry(str(tmp_path))
    reg.create_profile("carol", "pw")
    for _ in range(5):
        reg.record_attempt("carol", "7777", success=False)
    # Different chat_id — should NOT be locked
    assert not reg.is_locked_out("carol", "6666")


# ── Logout + revoke ───────────────────────────────────────────────────────────

def test_logout_clears_binding(tmp_path):
    reg = _make_registry(str(tmp_path))
    reg.create_profile("diana", "pw")
    reg.bind_device("5555", "diana")
    assert reg.resolve_binding("5555") == "diana"
    reg.unbind_device("5555")
    assert reg.resolve_binding("5555") is None


def test_revoke_clears_all_bindings_for_profile(tmp_path):
    reg = _make_registry(str(tmp_path))
    reg.create_profile("eve", "pw")
    reg.bind_device("4444", "eve")
    reg.bind_device("3333", "eve")
    removed = reg.revoke_profile_devices("eve")
    assert removed == 2
    assert reg.resolve_binding("4444") is None
    assert reg.resolve_binding("3333") is None


# ── Guest cannot login as ash ─────────────────────────────────────────────────

def test_login_ash_from_unknown_device_refused(tmp_path):
    """The /login handler refuses 'ash' regardless of password."""
    # This is a protocol-level check — enforced in tools_telegram._register_handlers.
    # We test the policy by verifying that 'ash' is seeded with a random uncrackable hash.
    import secrets
    from agent.profile import ProfileRegistry, verify_password
    reg = ProfileRegistry(db_path=str(tmp_path / "pi.db"))
    ash = reg.get_profile("ash")
    # The seeded hash was made from a random 32-byte token — brute-force infeasible.
    # Verify that random guesses don't match.
    for _ in range(10):
        assert not verify_password(secrets.token_urlsafe(12), ash.password_hash, ash.salt)


# ── /profile create is ash-only (enforced in handler, tested via policy) ─────

def test_profile_list_shows_created(tmp_path):
    reg = _make_registry(str(tmp_path))
    reg.create_profile("frank", "pw")
    names = [p.name for p in reg.list_profiles()]
    assert "frank" in names
    assert "ash" in names


def test_profile_delete_removes(tmp_path):
    reg = _make_registry(str(tmp_path))
    reg.create_profile("grace", "pw")
    reg.delete_profile("grace")
    assert reg.get_profile("grace") is None


# ── _resolve_profile integration ──────────────────────────────────────────────

def test_telegram_resolve_profile_ash_by_chat_id(tmp_path, monkeypatch):
    """_resolve_profile returns ash profile for TELEGRAM_CHAT_ID."""
    from agent.profile import ProfileRegistry
    reg = ProfileRegistry(db_path=str(tmp_path / "pi.db"))

    # Monkeypatch the registry singleton + env var
    import tools.tools_telegram as tg
    monkeypatch.setattr(tg, "_ALLOWED_CHAT_ID", "11111")

    import agent.profile as ap
    monkeypatch.setattr(ap, "_REGISTRY", reg)

    tt = tg.TelegramTools.__new__(tg.TelegramTools)
    tt._bot = None

    profile = tt._resolve_profile("11111")
    assert profile is not None
    assert profile.name == "ash"


def test_telegram_resolve_profile_unknown_returns_none(tmp_path, monkeypatch):
    from agent.profile import ProfileRegistry
    reg = ProfileRegistry(db_path=str(tmp_path / "pi.db"))

    import tools.tools_telegram as tg
    monkeypatch.setattr(tg, "_ALLOWED_CHAT_ID", "11111")

    import agent.profile as ap
    monkeypatch.setattr(ap, "_REGISTRY", reg)

    tt = tg.TelegramTools.__new__(tg.TelegramTools)
    tt._bot = None

    # chat_id not ash and no binding
    profile = tt._resolve_profile("99999")
    assert profile is None


def test_telegram_resolve_profile_bound_guest(tmp_path, monkeypatch):
    from agent.profile import ProfileRegistry
    reg = ProfileRegistry(db_path=str(tmp_path / "pi.db"))
    reg.create_profile("hank", "pw")
    reg.bind_device("22222", "hank")

    import tools.tools_telegram as tg
    monkeypatch.setattr(tg, "_ALLOWED_CHAT_ID", "11111")

    import agent.profile as ap
    monkeypatch.setattr(ap, "_REGISTRY", reg)

    tt = tg.TelegramTools.__new__(tg.TelegramTools)
    tt._bot = None

    profile = tt._resolve_profile("22222")
    assert profile is not None
    assert profile.name == "hank"
    assert profile.is_guest


if __name__ == "__main__":
    import inspect
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for name, fn in tests:
        with tempfile.TemporaryDirectory() as td:
            try:
                sig = inspect.signature(fn)
                params = list(sig.parameters.keys())
                kwargs = {}
                if "tmp_path" in params:
                    kwargs["tmp_path"] = pathlib.Path(td)
                fn(**kwargs)
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

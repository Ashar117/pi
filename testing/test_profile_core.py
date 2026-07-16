"""testing/test_profile_core.py — T-221: profile registry + namespace isolation.

All tests use temp directories — no real pi.db or Supabase are touched.
"""
import sys
import os
import sqlite3
import uuid
import tempfile
import pathlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.profile import (
    validate_profile_name,
    hash_password,
    verify_password,
    ProfileRegistry,
    Profile,
    profile_to_memory,
    GUEST_DENIED_TOOLS,
    GUEST_APPROVAL_TOOLS,
)


# ── Name validation ───────────────────────────────────────────────────────────

def test_valid_name_accepted():
    validate_profile_name("alice")  # should not raise


def test_valid_name_with_digits():
    validate_profile_name("user42")


def test_valid_name_with_underscore():
    validate_profile_name("my_user")


def test_name_too_short_rejected():
    try:
        validate_profile_name("a")
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_name_too_long_rejected():
    try:
        validate_profile_name("a" * 25)
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_name_uppercase_rejected():
    try:
        validate_profile_name("Alice")
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_name_path_traversal_rejected():
    try:
        validate_profile_name("../god")
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_reserved_name_pi_rejected():
    try:
        validate_profile_name("pi")
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_reserved_name_god_rejected():
    try:
        validate_profile_name("god")
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_reserved_name_ash_rejected():
    try:
        validate_profile_name("ash")
        assert False, "Expected ValueError"
    except ValueError:
        pass


# ── Password hashing ──────────────────────────────────────────────────────────

def test_password_hash_verify_roundtrip():
    ph, salt = hash_password("super_secret")
    assert verify_password("super_secret", ph, salt)


def test_wrong_password_fails():
    ph, salt = hash_password("correct")
    assert not verify_password("wrong", ph, salt)


def test_different_salts_produce_different_hashes():
    ph1, salt1 = hash_password("same_password")
    ph2, salt2 = hash_password("same_password")
    assert ph1 != ph2  # Different salts → different hashes


# ── ProfileRegistry ───────────────────────────────────────────────────────────

def _make_registry(tmp_dir: str) -> ProfileRegistry:
    return ProfileRegistry(db_path=os.path.join(tmp_dir, "pi.db"))


def test_ash_profile_seeded(tmp_path):
    reg = _make_registry(str(tmp_path))
    ash = reg.get_profile("ash")
    assert ash is not None
    assert ash.name == "ash"
    assert not ash.is_guest


def test_create_guest_profile(tmp_path):
    reg = _make_registry(str(tmp_path))
    reg.create_profile("alice", "secret123", is_guest=True)
    alice = reg.get_profile("alice")
    assert alice is not None
    assert alice.is_guest


def test_duplicate_profile_raises(tmp_path):
    reg = _make_registry(str(tmp_path))
    reg.create_profile("bob", "pass1")
    try:
        reg.create_profile("bob", "pass2")
        assert False, "Expected ValueError for duplicate"
    except ValueError:
        pass


def test_delete_profile(tmp_path):
    reg = _make_registry(str(tmp_path))
    reg.create_profile("charlie", "pw")
    assert reg.get_profile("charlie") is not None
    reg.delete_profile("charlie")
    assert reg.get_profile("charlie") is None


def test_cannot_delete_ash(tmp_path):
    reg = _make_registry(str(tmp_path))
    try:
        reg.delete_profile("ash")
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_list_profiles_includes_ash(tmp_path):
    reg = _make_registry(str(tmp_path))
    profiles = reg.list_profiles()
    names = [p.name for p in profiles]
    assert "ash" in names


# ── Device bindings ───────────────────────────────────────────────────────────

def test_bind_and_resolve_device(tmp_path):
    reg = _make_registry(str(tmp_path))
    reg.create_profile("diana", "pw")
    reg.bind_device("99999", "diana")
    assert reg.resolve_binding("99999") == "diana"


def test_unbind_device(tmp_path):
    reg = _make_registry(str(tmp_path))
    reg.create_profile("eve", "pw")
    reg.bind_device("88888", "eve")
    reg.unbind_device("88888")
    assert reg.resolve_binding("88888") is None


def test_revoke_profile_devices(tmp_path):
    reg = _make_registry(str(tmp_path))
    reg.create_profile("frank", "pw")
    reg.bind_device("77777", "frank")
    reg.bind_device("66666", "frank")
    removed = reg.revoke_profile_devices("frank")
    assert removed == 2
    assert reg.resolve_binding("77777") is None


# ── Lockout ───────────────────────────────────────────────────────────────────

def test_lockout_after_5_failures(tmp_path):
    reg = _make_registry(str(tmp_path))
    reg.create_profile("grace", "pw")
    for _ in range(5):
        reg.record_attempt("grace", "55555", success=False)
    assert reg.is_locked_out("grace", "55555")


def test_no_lockout_below_threshold(tmp_path):
    reg = _make_registry(str(tmp_path))
    reg.create_profile("hank", "pw")
    for _ in range(4):
        reg.record_attempt("hank", "44444", success=False)
    assert not reg.is_locked_out("hank", "44444")


# ── Profile properties ────────────────────────────────────────────────────────

def test_ash_profile_db_path(tmp_path):
    p = Profile(
        id="x", name="ash", password_hash="h", salt="s",
        is_guest=False, allowlist_json="[]", created_at="now"
    )
    assert p.db_path.endswith("data/pi.db") or "pi.db" in p.db_path


def test_guest_profile_db_path(tmp_path):
    p = Profile(
        id="x", name="alice", password_hash="h", salt="s",
        is_guest=True, allowlist_json="[]", created_at="now"
    )
    assert "pi_alice.db" in p.db_path


def test_guest_profile_namespace():
    p = Profile(
        id="x", name="alice", password_hash="h", salt="s",
        is_guest=True, allowlist_json="[]", created_at="now"
    )
    assert p.namespace == "profile_alice"


def test_ash_profile_namespace():
    p = Profile(
        id="x", name="ash", password_hash="h", salt="s",
        is_guest=False, allowlist_json="[]", created_at="now"
    )
    assert p.namespace == "pi"


# ── Two profiles get distinct db paths ───────────────────────────────────────

def test_two_profiles_have_distinct_db_paths():
    a = Profile(id="1", name="alice", password_hash="h", salt="s",
                is_guest=True, allowlist_json="[]", created_at="now")
    b = Profile(id="2", name="bob", password_hash="h", salt="s",
                is_guest=True, allowlist_json="[]", created_at="now")
    assert a.db_path != b.db_path


# ── profile_to_memory creates local-only MemoryTools for guests ──────────────

def test_guest_profile_to_memory_is_noop_supabase(tmp_path):
    p = Profile(
        id="x", name="alice", password_hash="h", salt="s",
        is_guest=True, allowlist_json="[]", created_at="now"
    )
    # Override db_path property via a custom object so we use tmp_path
    class _TmpProfile(Profile):
        @property
        def db_path(self):
            return str(tmp_path / "pi_alice.db")

    tp = _TmpProfile(
        id="x", name="alice", password_hash="h", salt="s",
        is_guest=True, allowlist_json="[]", created_at="now"
    )
    mem = profile_to_memory(tp)
    # namespace != 'pi' → _NoopSupabase; verify it can't accidentally write to public Supabase
    assert mem.namespace == "profile_alice"
    assert mem.is_private  # Supabase routed to _NoopSupabase


# ── Write in profile A is invisible to profile B ─────────────────────────────

def test_profile_a_write_invisible_to_profile_b(tmp_path):
    """Data written to profile A's SQLite must not appear in profile B's."""

    class _ProfileForTest(Profile):
        def __init__(self, name, path):
            super().__init__(
                id=str(uuid.uuid4()), name=name, password_hash="h", salt="s",
                is_guest=True, allowlist_json="[]", created_at="now"
            )
            self._path = path

        @property
        def db_path(self):
            return self._path

    pa = _ProfileForTest("alice", str(tmp_path / "pi_alice.db"))
    pb = _ProfileForTest("bob", str(tmp_path / "pi_bob.db"))

    mem_a = profile_to_memory(pa)
    mem_b = profile_to_memory(pb)

    # Write a fact into A's SQLite
    import sqlite3
    conn_a = sqlite3.connect(pa.db_path)
    try:
        conn_a.execute(
            "INSERT INTO l3_cache (id, content, importance, category, created_at) "
            "VALUES ('test-id', 'secret fact from alice', 9, 'note', '2026-01-01')"
        )
        conn_a.commit()
    finally:
        conn_a.close()

    # B's DB should NOT have it
    try:
        conn_b = sqlite3.connect(pb.db_path)
        rows = conn_b.execute("SELECT * FROM l3_cache WHERE content LIKE '%alice%'").fetchall()
        conn_b.close()
        assert len(rows) == 0, f"Profile B saw profile A's data: {rows}"
    except sqlite3.OperationalError:
        pass  # table doesn't exist in B's fresh DB — correct


# ── Capability sets ───────────────────────────────────────────────────────────

def test_guest_denied_tools_not_empty():
    assert len(GUEST_DENIED_TOOLS) > 0


def test_guest_approval_tools_not_empty():
    assert len(GUEST_APPROVAL_TOOLS) > 0


if __name__ == "__main__":
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for name, fn in tests:
        with tempfile.TemporaryDirectory() as td:
            try:
                import inspect
                sig = inspect.signature(fn)
                if "tmp_path" in sig.parameters:
                    fn(pathlib.Path(td))
                else:
                    fn()
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

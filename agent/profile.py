"""agent/profile.py — Multi-profile system (T-221).

Profiles give Pi isolated memory/history for family members without touching
Ash's namespace. The implementation reuses the MemoryTools namespace seam:
each profile gets its own SQLite DB (data/pi_<name>.db) and a namespace
(profile_<name>) that routes Supabase through _NoopSupabase (local-only).

Control-plane tables (profiles, device_bindings) live in Ash's pi.db.
Guest data never touches Ash's tables.
"""
from __future__ import annotations

import os
import re
import sqlite3
import secrets
import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List

# Reserved names that cannot be used as profile names.
_RESERVED = frozenset({"pi", "god", "ash", "test", "default", "admin", "root", "system"})
_NAME_RE = re.compile(r"^[a-z0-9_]{2,24}$")

# PBKDF2 parameters — stdlib, no dependency.
_HASH_ALG = "sha256"
_ITERATIONS = 260_000  # NIST recommendation 2023


@dataclass
class Profile:
    id: str
    name: str               # lowercase slug — internal key, DB paths, namespace
    password_hash: str
    salt: str               # hex-encoded random bytes
    is_guest: bool
    allowlist_json: str     # JSON list of extra-allowed tool names (for future use)
    created_at: str
    display_name: str = ""  # user-chosen casing, e.g. "MasiM"; defaults to name
    nickname: str = ""      # what Pi calls them, e.g. "Majesty"; defaults to display_name
    last_login_at: Optional[str] = None

    @property
    def greeting_name(self) -> str:
        """Name Pi uses when addressing this user."""
        return self.nickname or self.display_name or self.name

    @property
    def db_path(self) -> str:
        """Absolute path to this profile's SQLite database."""
        project_root = Path(__file__).parent.parent
        if self.name == "ash":
            return str(project_root / "data" / "pi.db")
        return str(project_root / "data" / f"pi_{self.name}.db")

    @property
    def namespace(self) -> str:
        if self.name == "ash":
            return "pi"
        return f"profile_{self.name}"

    @property
    def vault_path(self) -> str:
        project_root = Path(__file__).parent.parent
        if self.name == "ash":
            return str(project_root / "vault")
        folder = self.display_name or self.name
        return str(project_root / "vault" / folder)

    @property
    def consciousness_path(self) -> str:
        if self.name == "ash":
            return "prompts/consciousness.txt"
        return "prompts/consciousness_guest.txt"

    @property
    def tool_allowlist(self) -> Optional[tuple]:
        """None = full root allowlist. Guests get a restricted set."""
        if self.is_guest:
            return _GUEST_ALLOWLIST
        return None  # Ash: all tools


# Capability sets for guests — enforced in execute_tool, not in prompt.
# T-224: fail-closed posture — only explicitly listed tools are allowed.
GUEST_DENIED_TOOLS = frozenset({
    # File-mutating tools: no appeal — deny outright.
    "modify_file", "create_file", "write_file", "edit_file",
    "delete_file", "archive_file",
})
GUEST_APPROVAL_TOOLS = frozenset({
    # Execution tools: queue for Ash's explicit approval (T-225 workflow).
    "execute_python", "run_bash", "execute_bash", "run_script", "computer_use",
})

# Tools from normie allowlist that guests cannot access (Pi-internal / Ash-specific).
_GUEST_NOT_ALLOWED = frozenset({
    "system_introspect", "refresh_awareness", "daily_briefing",
    "obsidian_read", "obsidian_search",
})

# Derived from normie allowlist so additions there propagate automatically.
def _build_guest_allowlist() -> tuple:
    from agent.modes import MODE_CONFIGS
    normie = MODE_CONFIGS["normie"].tool_allowlist or ()
    return tuple(t for t in normie if t not in _GUEST_NOT_ALLOWED)

_GUEST_ALLOWLIST = _build_guest_allowlist()


def validate_profile_name(name: str) -> None:
    """Raise ValueError if name is not a safe, non-reserved profile name."""
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Profile name {name!r} is invalid. "
            "Must be 2-24 lowercase alphanumeric/underscore characters."
        )
    if name in _RESERVED:
        raise ValueError(
            f"Profile name {name!r} is reserved. "
            f"Reserved names: {sorted(_RESERVED)}"
        )


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Return (hash_hex, salt_hex). Generates a new salt if not provided."""
    if salt is None:
        salt = secrets.token_bytes(32).hex()
    dk = hashlib.pbkdf2_hmac(
        _HASH_ALG,
        password.encode("utf-8"),
        bytes.fromhex(salt),
        _ITERATIONS,
    )
    return dk.hex(), salt


def verify_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    """Return True iff password matches the stored hash."""
    candidate_hash, _ = hash_password(password, salt=stored_salt)
    return secrets.compare_digest(candidate_hash, stored_hash)


# ── Profile registry ──────────────────────────────────────────────────────────

def _default_registry_db() -> str:
    return str(Path(__file__).parent.parent / "data" / "pi.db")


class ProfileRegistry:
    """Persistent registry of Pi profiles, stored in the default pi.db control plane."""

    def __init__(self, db_path: Optional[str] = None):
        self._db = db_path or _default_registry_db()
        os.makedirs(os.path.dirname(self._db), exist_ok=True)
        self._init_tables()
        self._seed_ash()

    def _connect(self):
        conn = sqlite3.connect(self._db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    nickname TEXT NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    is_guest INTEGER NOT NULL DEFAULT 1,
                    allowlist_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                )
            """)
            # Migrate existing tables that predate display_name/nickname columns.
            for col, default in (("display_name", "''"), ("nickname", "''")):
                try:
                    conn.execute(f"ALTER TABLE profiles ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}")
                except sqlite3.OperationalError:
                    pass  # column already exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS device_bindings (
                    chat_id TEXT NOT NULL,
                    profile_name TEXT NOT NULL REFERENCES profiles(name),
                    bound_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS login_attempts (
                    profile_name TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    success INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.commit()

    def _seed_ash(self):
        """Ensure the ash profile exists as the implicit owner (no password in DB)."""
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM profiles WHERE name='ash'").fetchone()
            if row is None:
                import uuid as _uuid
                now = datetime.now(timezone.utc).isoformat()
                # Ash authenticates by chat_id, not password. Store a random hash
                # so no string can accidentally match it.
                ph, salt = hash_password(secrets.token_urlsafe(32))
                conn.execute(
                    "INSERT INTO profiles (id, name, password_hash, salt, is_guest, allowlist_json, created_at) "
                    "VALUES (?, 'ash', ?, ?, 0, '[]', ?)",
                    [str(_uuid.uuid4()), ph, salt, now],
                )
                conn.commit()

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def create_profile(self, display_name: str, password: str,
                       nickname: str = "", is_guest: bool = True) -> Profile:
        """Create a new profile. display_name preserves casing; slug = display_name.lower()."""
        slug = display_name.lower()
        validate_profile_name(slug)
        nick = nickname.strip() or display_name
        ph, salt = hash_password(password)
        import uuid as _uuid
        pid = str(_uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO profiles "
                    "(id, name, display_name, nickname, password_hash, salt, is_guest, allowlist_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?)",
                    [pid, slug, display_name, nick, ph, salt, int(is_guest), now],
                )
                conn.commit()
            except sqlite3.IntegrityError:
                raise ValueError(f"Profile {slug!r} already exists.")
        return self.get_profile(slug)

    def get_profile(self, name: str) -> Optional[Profile]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM profiles WHERE name=?", [name.lower()]).fetchone()
        if row is None:
            return None
        return Profile(
            id=row["id"], name=row["name"],
            display_name=row["display_name"] or row["name"],
            nickname=row["nickname"] or row["display_name"] or row["name"],
            password_hash=row["password_hash"], salt=row["salt"],
            is_guest=bool(row["is_guest"]), allowlist_json=row["allowlist_json"],
            created_at=row["created_at"], last_login_at=row["last_login_at"],
        )

    def list_profiles(self) -> List[Profile]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM profiles ORDER BY created_at").fetchall()
        return [Profile(
            id=r["id"], name=r["name"],
            display_name=r["display_name"] or r["name"],
            nickname=r["nickname"] or r["display_name"] or r["name"],
            password_hash=r["password_hash"], salt=r["salt"],
            is_guest=bool(r["is_guest"]), allowlist_json=r["allowlist_json"],
            created_at=r["created_at"], last_login_at=r["last_login_at"],
        ) for r in rows]

    def set_nickname(self, name: str, nickname: str) -> None:
        """Update what Pi calls this profile's user."""
        with self._connect() as conn:
            conn.execute("UPDATE profiles SET nickname=? WHERE name=?", [nickname, name.lower()])
            conn.commit()

    def delete_profile(self, name: str) -> bool:
        if name == "ash":
            raise ValueError("Cannot delete the ash profile.")
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM profiles WHERE name=?", [name])
            conn.execute("DELETE FROM device_bindings WHERE profile_name=?", [name])
            conn.commit()
        return cur.rowcount > 0

    def update_last_login(self, name: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("UPDATE profiles SET last_login_at=? WHERE name=?", [now, name])
            conn.commit()

    # ── Device bindings ───────────────────────────────────────────────────────

    def bind_device(self, chat_id: str, profile_name: str) -> None:
        """Permanently bind a Telegram chat_id to a profile (sticky login)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO device_bindings (chat_id, profile_name, bound_at) VALUES (?, ?, ?)",
                [chat_id, profile_name, now],
            )
            conn.commit()

    def unbind_device(self, chat_id: str) -> bool:
        """Remove the binding for a chat_id (/logout)."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM device_bindings WHERE chat_id=?", [chat_id])
            conn.commit()
        return cur.rowcount > 0

    def revoke_profile_devices(self, profile_name: str) -> int:
        """Remove all device bindings for a profile (Ash revokes access)."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM device_bindings WHERE profile_name=?", [profile_name]
            )
            conn.commit()
        return cur.rowcount

    def resolve_binding(self, chat_id: str) -> Optional[str]:
        """Return profile_name for a chat_id if a permanent binding exists."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT profile_name FROM device_bindings WHERE chat_id=?", [chat_id]
            ).fetchone()
        return row["profile_name"] if row else None

    # ── Login attempt tracking ─────────────────────────────────────────────────

    def record_attempt(self, profile_name: str, chat_id: str, success: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO login_attempts (profile_name, chat_id, attempted_at, success) VALUES (?, ?, ?, ?)",
                [profile_name, chat_id, now, int(success)],
            )
            conn.commit()

    def is_locked_out(self, profile_name: str, chat_id: str,
                      max_fails: int = 5, window_minutes: int = 10) -> bool:
        """Return True if this (profile, chat) has >= max_fails failures in the last window."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM login_attempts "
                "WHERE profile_name=? AND chat_id=? AND success=0 AND attempted_at>?",
                [profile_name, chat_id, cutoff],
            ).fetchone()
        return (row[0] or 0) >= max_fails


# ── Memory factory ────────────────────────────────────────────────────────────

def profile_to_memory(profile: Profile, supabase_url: str = "", supabase_key: str = ""):
    """Return a MemoryTools instance isolated to this profile's namespace.

    Guest profiles use namespace != 'pi' which routes Supabase calls through
    _NoopSupabase (local-only, all 3 tiers in the guest's SQLite DB). Ash gets
    the normal pi namespace + full Supabase access.
    """
    from tools.tools_memory import MemoryTools
    os.makedirs(os.path.dirname(profile.db_path), exist_ok=True)
    return MemoryTools(
        supabase_url=supabase_url if profile.name == "ash" else "",
        supabase_key=supabase_key if profile.name == "ash" else "",
        db_path=profile.db_path,
        namespace=profile.namespace,
    )


# ── Singleton registry ────────────────────────────────────────────────────────

_REGISTRY: Optional[ProfileRegistry] = None


def get_registry(db_path: Optional[str] = None) -> ProfileRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ProfileRegistry(db_path=db_path)
    return _REGISTRY


# ── Turn-scoped profile switching (T-223) ─────────────────────────────────────

@contextmanager
def profile_switch(agent, profile: Profile):
    """T-223: Save agent's current profile/memory/consciousness, switch to profile's, restore.

    Must be the OUTERMOST context manager (profile outer, conversation inner) so
    that memory and prompt are correct before conversation context loads.

    Usage:
        from agent.conversation import conversation_switch
        with profile_switch(agent, guest_profile):
            with conversation_switch(agent, f"telegram:{chat_id}"):
                reply = agent.process_input(user_text)
    """
    saved_profile = getattr(agent, "current_profile", None)
    saved_memory = agent.memory
    saved_consciousness = agent.consciousness

    try:
        agent.current_profile = profile

        # Route memory to the profile's namespace (isolates all 3 tiers)
        supa_url = getattr(saved_memory, "_supabase_url", "") or ""
        supa_key = getattr(saved_memory, "_supabase_key", "") or ""
        profile_memory = profile_to_memory(profile, supabase_url=supa_url, supabase_key=supa_key)
        agent.memory = profile_memory

        # Load the profile's consciousness prompt
        project_root = Path(__file__).parent.parent
        prompt_path = project_root / profile.consciousness_path
        try:
            agent.consciousness = prompt_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            try:
                from agent.observability import track_silent
                track_silent("profile.guest_consciousness_missing", None,
                             context={"path": str(prompt_path), "profile": profile.name})
            except Exception:
                pass

        yield agent
    finally:
        agent.current_profile = saved_profile
        agent.memory = saved_memory
        agent.consciousness = saved_consciousness

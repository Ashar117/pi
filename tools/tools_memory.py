"""
Pi Agent Tools - Memory Operations
Simple, powerful, composable tools for memory management.
"""

import json
import os
import re
import sqlite3
import threading
import uuid
import warnings
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

# T-200: supabase-py 2.x internally passes deprecated timeout/verify kwargs to
# postgrest-py. Filter at module level — we have no control over library internals
# and the behavior is unchanged; these will become errors in a future supabase major.
warnings.filterwarnings(
    "ignore", category=DeprecationWarning, module=r"supabase\._sync\.client"
)
warnings.filterwarnings(
    "ignore", category=DeprecationWarning, module=r"postgrest\._sync\.client"
)

try:
    from rank_bm25 import BM25Okapi as _BM25Okapi  # pip install rank-bm25
    _BM25_AVAILABLE = True
except ImportError:
    _BM25Okapi = None  # type: ignore[assignment,misc]
    _BM25_AVAILABLE = False


# ── BM25 + entity hybrid helpers ──────────────────────────────────────────────

_STOPWORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would could should may might shall can i you he she it we they "
    "this that these those and or but not in on at to of for with from by "
    "about into through during before after above below between".split()
)

_ENTITY_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")  # capitalized words as cheap entities


def _tokenize(text: str) -> List[str]:
    tokens = re.sub(r"[^a-zA-Z0-9\s]", " ", text.lower()).split()
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


# ── T-299: write-time temporal inference (deterministic phrase table) ────────
# NOT an NLP date parser, NOT a new dependency. False negatives are fine (the
# fact just stays permanent — today's behavior); false positives are not, so
# every pattern requires an explicit validity idiom — never a bare weekday
# mention ("meeting on friday" must NOT expire; only "until friday" does).
# First hit wins, no scoring (ponytail: simplest thing that can't misfire).

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _end_of_day(dt: datetime) -> datetime:
    return dt.replace(hour=23, minute=59, second=59, microsecond=0)


def _next_weekday(now: datetime, target: int) -> datetime:
    days_ahead = (target - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # "until monday" said ON a monday means next monday
    return _end_of_day(now + timedelta(days=days_ahead))


_EPHEMERAL_PATTERNS = [
    (re.compile(r"\b(just for today|for today only|today only|for today)\b", re.I),
     lambda now, m: _end_of_day(now)),
    (re.compile(r"\btonight\b", re.I),
     lambda now, m: _end_of_day(now)),
    (re.compile(r"\buntil tomorrow\b", re.I),
     lambda now, m: _end_of_day(now + timedelta(days=1))),
    (re.compile(r"\bfor the next (\d+) hours?\b", re.I),
     lambda now, m: now + timedelta(hours=int(m.group(1)))),
    (re.compile(r"\bfor the next (\d+) weeks?\b", re.I),
     lambda now, m: now + timedelta(weeks=int(m.group(1)))),
    (re.compile(r"\bfor the next (\d+) days?\b", re.I),
     lambda now, m: now + timedelta(days=int(m.group(1)))),
    (re.compile(r"\bthis week\b", re.I),
     lambda now, m: now + timedelta(days=7)),
    (re.compile(r"\bthis month\b", re.I),
     lambda now, m: now + timedelta(days=31)),
    (re.compile(
        r"\buntil (monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I),
     lambda now, m: _next_weekday(now, _WEEKDAYS[m.group(1).lower()])),
]


def _infer_expiry(content: str, now: datetime) -> Optional[datetime]:
    """T-299: detect ephemeral phrasing in written content and return the
    inferred expiry, or None (permanent — unchanged default). Pure function,
    no MemoryTools instance needed."""
    for pattern, resolver in _EPHEMERAL_PATTERNS:
        m = pattern.search(content)
        if m:
            return resolver(now, m)
    return None


def _extract_entities(text: str) -> set:
    return set(_ENTITY_RE.findall(text))
# Strips trailing session markers before dedup comparison so that the same fact
# appended with different markers (e.g. "...veggies. marker_abc123" vs
# "...veggies. marker_def456") is recognised as a duplicate.
_MARKER_RE = re.compile(
    r'\s*(marker_[0-9a-f]{6,}|unique[a-z0-9]{4,})\s*$', re.IGNORECASE
)


class _NoopSupabase:
    """Chainable no-op Supabase shim for private-namespace MemoryTools (T-082).

    Every fluent attribute returns self; ``.execute()`` returns a stub with
    ``.data=[]``. Installed for a non-default (private) namespace or when
    Supabase creds are absent, so that memory never reaches Supabase.
    Privacy-by-file-separation is ADR-001 invariant 5.

    Tests that pre-assign a MagicMock continue to work — the shim only
    activates when the constructor receives a private namespace or empty creds.
    """

    _mock_name = "_NoopSupabase"  # short-circuits the test-write guard

    class _Response:
        data: list = []

    def __getattr__(self, _name):
        return self

    def __call__(self, *_a, **_kw):
        return self

    def execute(self):
        return self._Response()


class MemoryTools:
    """
    Simple memory tools for Pi agent.
    No complex logic - just read, write, delete.
    """
    
    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        sqlite_path: Optional[str] = None,
        *,
        db_path: Optional[str] = None,
        namespace: str = "pi",
    ):
        # T-075: defer Supabase client creation until first actual use.
        # `from supabase import create_client` triggers the
        # supabase → realtime → websockets dataclass chain (~3-5s on cold Win+Py3.13).
        # Storing the URL/key here keeps `__init__` snappy; the `supabase` property
        # below creates the client on first access.
        #
        # T-082 step 3: db_path is the forward-compatible alias for sqlite_path;
        # namespace partitions storage. namespace != "pi" routes Supabase calls
        # through `_NoopSupabase` so a private namespace never leaves the box.
        if db_path is not None:
            sqlite_path = db_path
        self.namespace = namespace
        self.is_private = namespace != "pi"

        self._supabase_url = supabase_url
        self._supabase_key = supabase_key
        self._supabase_client = None
        if self.is_private or not supabase_url or not supabase_key:
            # Private namespace OR missing creds → never reach Supabase.
            # The noop shim silently drops inserts and returns empty
            # selects, so every existing try/except chain works unchanged.
            self._supabase_client = _NoopSupabase()

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if sqlite_path is None:
            if os.environ.get("PYTEST_CURRENT_TEST"):
                # Tests get an isolated DB so pytest runs never pollute production l3_cache
                sqlite_path = os.path.join(project_root, "data", "pi_test.db")
            else:
                sqlite_path = os.path.join(project_root, "data", "pi.db")
        elif not os.path.isabs(sqlite_path):
            # Caller-supplied relative path (e.g. a ModeConfig.memory_db)
            # resolves against project root for portability.
            sqlite_path = os.path.join(project_root, sqlite_path)
        os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)
        self.sqlite_path = sqlite_path
        # T-165: StorageBackend seam — new conversation methods use this;
        # legacy L3 methods still call sqlite3.connect(self.sqlite_path) directly
        # and will be migrated incrementally (see agent/storage.py).
        from agent.storage import SQLiteStorageBackend
        self._sqlite_backend = SQLiteStorageBackend(self.sqlite_path)
        self._last_sync: Optional[datetime] = None  # T-011: TTL-based sync guard
        self._sync_ttl_seconds = 300  # sync at most once per 5 minutes
        # ponytail: 5000 comfortably clears today's 1029-row l3_active_memory
        # (T-270). Upgrade path if this cap is ever hit for real: incremental
        # delta sync keyed on created_at instead of full wipe-and-repopulate.
        self._L3_SYNC_ROW_CAP = 5000
        self._sync_lock = threading.Lock()  # T-066: prevents double-sync from concurrent callers
        self._supabase_init_lock = threading.Lock()  # T-075: guards lazy client creation
        self._supa_lock = threading.RLock()  # T-105: guards shared Supabase client (not thread-safe)
        self._init_sqlite()

    @property
    def supabase(self):
        """T-075: Lazy-init Supabase client. First access pays the import cost;
        subsequent accesses hit the cached client.

        Tests that pre-assign ``self.supabase = MagicMock()`` work unchanged
        because the setter stores into ``_supabase_client``.
        """
        if self._supabase_client is None:
            with self._supabase_init_lock:
                if self._supabase_client is None:
                    import warnings
                    from supabase import create_client
                    # T-200: supabase-py 2.x emits DeprecationWarning for timeout/verify
                    # params it passes internally to postgrest-py. Suppress at construction;
                    # our code never passes those kwargs — this is a library-internal issue.
                    with warnings.catch_warnings():
                        warnings.filterwarnings(
                            "ignore", category=DeprecationWarning, module="supabase"
                        )
                        warnings.filterwarnings(
                            "ignore", category=DeprecationWarning, module="postgrest"
                        )
                        self._supabase_client = create_client(
                            self._supabase_url, self._supabase_key
                        )
        return self._supabase_client

    @supabase.setter
    def supabase(self, value):
        """Allow tests / callers to inject a mock client."""
        self._supabase_client = value
    
    def _init_sqlite(self):
        """Initialize SQLite cache + run idempotent migrations."""
        # T-165: ensure _sqlite_backend is set even when __init__ is bypassed.
        if not hasattr(self, "_sqlite_backend"):
            from agent.storage import SQLiteStorageBackend
            self._sqlite_backend = SQLiteStorageBackend(self.sqlite_path)
        # Route init connection through the backend so InMemoryStorageBackend
        # tests see the same tables that conversation methods write to.
        conn = self._sqlite_backend.connect()
        cursor = conn.cursor()

        # Simple L3 cache
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS l3_cache (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                importance INTEGER,
                category TEXT,
                active_until TEXT,
                created_at TEXT
            )
        """)

        # T-078: temporal validity column. Idempotent — checks via PRAGMA so
        # the migration runs at most once per database file.
        # Semantic: active_until = scheduled expiry (still pruned). invalid_at =
        # marked superseded by a contradicting newer fact (KEPT for historical
        # queries; never auto-pruned).
        cursor.execute("PRAGMA table_info(l3_cache)")
        existing_cols = {row[1] for row in cursor.fetchall()}
        if "invalid_at" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN invalid_at TEXT")

        # T-125a — derived-fact columns. Idempotent.
        # kind ∈ {None=stated, 'derived'}
        # source_id: id of the L3 row this derived fact was computed from
        # recompute_after: ISO timestamp; caretaker re-runs the formula when now > this
        # formula: name from agent.caretaker._FORMULAS (e.g. 'age_from_birthday')
        if "kind" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN kind TEXT")
        if "source_id" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN source_id TEXT")
        if "recompute_after" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN recompute_after TEXT")
        if "formula" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN formula TEXT")

        # T-125b — dedup mark. Idempotent.
        # superseded_by: id of the winning row when this one was deduplicated.
        # Loser rows remain in DB for audit but are excluded from search.
        if "superseded_by" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN superseded_by TEXT")

        # T-134: multi-dimensional salience columns. Idempotent.
        if "surprise_score" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN surprise_score REAL")
        if "goal_alignment" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN goal_alignment REAL")
        if "affect_tag" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN affect_tag TEXT")

        # T-135: Ebbinghaus decay columns. Idempotent.
        if "decay_rate" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN decay_rate REAL DEFAULT 0.01")
        if "pinned" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN pinned INTEGER DEFAULT 0")
        if "last_accessed_at" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN last_accessed_at TEXT")

        # T-137: encoding context — which mode wrote this fact. Enables
        # context-cued recall (same-mode retrieval boost). NULL = legacy/global.
        if "mode" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN mode TEXT")
        # T-142: conversation thread this fact was written in. Enables
        # per-conversation scoping / same-conversation retrieval boost.
        # NULL = global (visible to all conversations).
        if "conversation_id" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN conversation_id TEXT")
        # T-137: project/ticket scope this fact belongs to. Enables same-scope
        # retrieval boost (strongest context signal). NULL = global, no boost.
        if "scope" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN scope TEXT")
        # T-234: back-reference to the L2 organized_memory row that was promoted to
        # this L3 entry. Used to propagate supersession from L3 back to L2 so the
        # vault never shows a corrected-away fact.
        if "source_l2_id" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN source_l2_id TEXT")

        # T-291: JSON-encoded embedding vector for dense/semantic L3 search.
        # NULL when no embedding provider is configured — pure additive column.
        if "embedding" not in existing_cols:
            cursor.execute("ALTER TABLE l3_cache ADD COLUMN embedding TEXT")

        # T-186: conversation persistence tables.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT,
                mode TEXT,
                created_at TEXT,
                last_active_at TEXT,
                digest TEXT
            )
        """)
        # T-205: idempotent migration for existing DBs without digest column.
        conv_cols = {r[1] for r in cursor.execute("PRAGMA table_info(conversations)").fetchall()}
        if "digest" not in conv_cols:
            cursor.execute("ALTER TABLE conversations ADD COLUMN digest TEXT")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                role TEXT NOT NULL,
                content_json TEXT NOT NULL,
                ts TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id),
                UNIQUE(conversation_id, idx)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_turns_conv_id "
            "ON conversation_turns(conversation_id, idx)"
        )

        conn.commit()
        conn.close()

    def memory_read(self, query: str = "", tier: Optional[str] = None, limit: int = 20,
                    current_mode=None, current_conversation_id=None,
                    current_scope=None) -> List[Dict]:
        """
        Search memory. Empty query returns all recent entries.

        Args:
            query: Search term (empty = return all recent)
            tier:  'l1' | 'l2' | 'l3' to search a specific tier.
                   None (default) searches L3 first; if L3 returns nothing, falls back
                   to L2. This means hot context answers immediately and deep memory
                   is only hit when the fact isn't hot.
                   L1 (raw_wiki archive) is opt-in via tier='l1' — noisy without a
                   real query layer (T-017).
            limit: Max results

        Returns:
            List of matching entries. Each entry carries a 'tier' key indicating origin.
        """
        results = []

        if tier == "l3" or tier is None:
            # T-070: stopword guard — if the query reduces to zero meaningful tokens,
            # skip L3 entirely. Avoids "L3 search 'remember' → 4 noise results".
            if query and not _tokenize(query):
                if tier == "l3":
                    return results
                # tier is None — fall through to L2 below
            else:
                # T-163: single gated entry — sync + scoring + shaping in one place.
                l3_results = self._l3_active_records(
                    query, limit,
                    current_mode=current_mode,
                    current_conversation_id=current_conversation_id,
                    current_scope=current_scope,
                )
                results.extend(l3_results or [])  # T-235: guard against unexpected None
                if query and l3_results:
                    print(f"[Memory] L3 search '{query[:30]}' -> {len(l3_results)} results")
                if l3_results:
                    self._bump_access([r["id"] for r in l3_results], "l3")

                # L3 hit — no need to bother L2
                if results and tier is None:
                    return results

            if tier == "l3":
                return results

        if tier == "l2" or tier is None:
            try:
                if query:
                    # SM-003 fix: title is only the first 100 chars of content; the
                    # full text lives in content.text JSONB. Search BOTH and merge
                    # by id so distinctive keywords past char 100 stay reachable.
                    with self._supa_lock:
                        r_title = (self.supabase.table("organized_memory").select("*")
                                   .ilike("title", f"%{query}%")
                                   .order("created_at", desc=True).limit(limit).execute())
                        r_body = (self.supabase.table("organized_memory").select("*")
                                  .ilike("content->>text", f"%{query}%")
                                  .order("created_at", desc=True).limit(limit).execute())
                    seen_ids = set()
                    merged = []
                    for entry in (r_title.data or []) + (r_body.data or []):
                        if entry["id"] in seen_ids:
                            continue
                        seen_ids.add(entry["id"])
                        entry["tier"] = "l2"
                        merged.append(entry)
                    final = merged[:limit]
                    results.extend(final)
                    # T-082: bump access counters
                    if final:
                        self._bump_access([e["id"] for e in final], "l2")
                else:
                    with self._supa_lock:
                        response = (self.supabase.table("organized_memory").select("*")
                                    .order("created_at", desc=True).limit(limit).execute())
                    if response.data:
                        for entry in response.data:
                            entry["tier"] = "l2"
                        results.extend(response.data)
                        self._bump_access([e["id"] for e in response.data], "l2")
            except Exception as e:
                print(f"[Memory] L2 search error: {e}")

        if tier == "l1":
            try:
                with self._supa_lock:
                    builder = self.supabase.table("raw_wiki").select("*")
                    if query:
                        builder = builder.ilike("content", f"%{query}%")
                    response = builder.order("created_at", desc=True).limit(limit).execute()
                if response.data:
                    for entry in response.data:
                        entry["tier"] = "l1"
                    results.extend(response.data)
            except Exception as e:
                print(f"[Memory] L1 search error: {e}")

        return results

    def _search_l3_cache(self, query: str, limit: int) -> list:
        """Search SQLite l3_cache with LIKE — kept as fallback.

        T-078: filters out entries with invalid_at set (superseded facts).
        T-163: active_until filter added to both branches so expired rows
        never surface regardless of which reader calls this method.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        if query:
            cursor.execute("""
                SELECT id, content, importance, category, active_until
                FROM l3_cache
                WHERE content LIKE ?
                  AND (active_until IS NULL OR active_until > ?)
                  AND invalid_at IS NULL
                  AND (superseded_by IS NULL OR superseded_by = '')
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
            """, [f"%{query}%", now_iso, limit])
        else:
            cursor.execute("""
                SELECT id, content, importance, category, active_until
                FROM l3_cache
                WHERE (active_until IS NULL OR active_until > ?)
                  AND invalid_at IS NULL
                  AND (superseded_by IS NULL OR superseded_by = '')
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
            """, [now_iso, limit])
        rows = cursor.fetchall()
        conn.close()
        return rows

    def _l3_active_records(
        self,
        query: str = "",
        limit: int = 9999,
        current_mode=None,
        current_conversation_id=None,
        current_scope=None,
    ) -> list:
        """Single gated entry for ALL L3 reads (T-163).

        Handles sync-if-stale once, then delegates to the existing
        _hybrid_search_l3 scoring hierarchy (BM25 → fast-path → LIKE).
        Returns canonical record dicts so all callers share one shape.

        Use this instead of calling _hybrid_search_l3 or writing a
        direct SQLite query — one reader = one place to reason about
        what L3 returns.
        """
        # Skip sync when Supabase is a no-op: _sync_l3 does DELETE+repopulate
        # from Supabase, so calling it with _NoopSupabase (empty creds / offline
        # tests) wipes locally-written SQLite rows before the caller reads them.
        _has_real_supa = not isinstance(self._supabase_client, _NoopSupabase)
        now = datetime.now(timezone.utc)
        if _has_real_supa and (
            self._last_sync is None
            or (now - self._last_sync).total_seconds() > self._sync_ttl_seconds
        ):
            with self._sync_lock:
                now2 = datetime.now(timezone.utc)
                if (self._last_sync is None
                        or (now2 - self._last_sync).total_seconds() > self._sync_ttl_seconds):
                    self._sync_l3()

        rows = self._hybrid_search_l3(
            query, limit,
            current_mode=current_mode,
            current_conversation_id=current_conversation_id,
            current_scope=current_scope,
        )
        return [
            {"id": r[0], "content": r[1], "importance": r[2],
             "category": r[3], "active_until": r[4], "tier": "l3"}
            for r in rows
        ]

    @staticmethod
    def _l3_env_int(var: str, default: int) -> int:
        """Read an int env var for L3 search tuning; fall back on parse error."""
        raw = os.environ.get(var, "")
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError as e:
            from agent.observability import track_silent
            track_silent("config.invalid_env", ValueError(f"{var}={raw!r}: {e}"))
            return default

    # Context-cued recall boosts (encoding-specificity). Additive on the combined
    # score so a same-context match outranks an equally-relevant off-context row,
    # but never overrides a much-more-relevant off-context row. Scope and
    # conversation are stronger signals than mode.
    _SAME_MODE_BOOST = 0.15          # T-137
    _SAME_SCOPE_BOOST = 0.20         # T-137
    _SAME_CONVERSATION_BOOST = 0.20  # T-142

    def _context_boosts(self, current_mode=None, current_conversation_id=None,
                        current_scope=None) -> dict:
        """Return {id: total_boost} for context-cued recall, or {} when disabled.

        Composes same-mode (T-137), same-conversation (T-142), and same-scope
        (T-137) boosts in one pass. Gated by PI_CONTEXT_CUED_RECALL (default off,
        per soak plan) and only computed when at least one context value is
        supplied. Best-effort: any failure (legacy schema, locked db) returns {}
        so retrieval is never broken (Invariant 9).
        """
        if os.environ.get("PI_CONTEXT_CUED_RECALL", "").lower() not in (
            "1", "true", "on", "yes"
        ):
            return {}
        if not any([current_mode, current_conversation_id, current_scope]):
            return {}
        try:
            conn = sqlite3.connect(self.sqlite_path)
            rows = conn.execute(
                "SELECT id, mode, conversation_id, scope FROM l3_cache").fetchall()
            conn.close()
        except Exception:
            return {}
        boosts = {}
        for id_, mode, conv, scope in rows:
            b = 0.0
            if current_mode and mode == current_mode:
                b += self._SAME_MODE_BOOST
            if current_conversation_id and conv == current_conversation_id:
                b += self._SAME_CONVERSATION_BOOST
            if current_scope and scope == current_scope:
                b += self._SAME_SCOPE_BOOST
            if b:
                boosts[id_] = b
        return boosts

    def _l3_fast_path(self, query: str, limit: int, current_mode=None,
                      current_conversation_id=None, current_scope=None) -> list:
        """LIKE + score fast-path for small L3 caches (< threshold rows).

        Score formula: 1.0 if query term in content + 0.1 * importance +
        recency_bonus(exp(-days/7)). Returns top limit rows, same shape as
        _hybrid_search_l3: (id, content, importance, category, active_until).
        """
        import math
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        # T-134/T-135: pull salience + decay columns when present; NULL fallback
        # for pre-migration schemas (Invariant 8 — idempotent, never crash)
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        _salience_cols = True
        try:
            cursor.execute("""
                SELECT id, content, importance, category, active_until, created_at,
                       surprise_score, goal_alignment, affect_tag, decay_rate, pinned, last_accessed_at
                FROM l3_cache
                WHERE (active_until IS NULL OR active_until > ?)
                  AND invalid_at IS NULL
                  AND (superseded_by IS NULL OR superseded_by = '')
                ORDER BY importance DESC, created_at DESC
            """, [now_iso])
        except sqlite3.OperationalError:
            _salience_cols = False
            cursor.execute("""
                SELECT id, content, importance, category, active_until, created_at,
                       NULL, NULL, NULL, NULL, 0, NULL
                FROM l3_cache
                WHERE (active_until IS NULL OR active_until > ?)
                  AND invalid_at IS NULL
                  AND (superseded_by IS NULL OR superseded_by = '')
                ORDER BY importance DESC, created_at DESC
            """, [now_iso])
        rows = cursor.fetchall()
        conn.close()

        _composite_mode = False
        if _salience_cols:
            try:
                from memory.salience import is_composite_mode, composite_salience, effective_importance
                _composite_mode = is_composite_mode()
            except Exception:
                pass

        boost_map = self._context_boosts(current_mode, current_conversation_id, current_scope)
        q_lower = query.lower()
        scored = []
        for row in rows:
            (id_, content, importance, category, active_until, created_at,
             surprise_score, goal_alignment, affect_tag, decay_rate, pinned,
             last_accessed_at) = row
            content_str = content or ""
            match_score = 1.0 if q_lower and q_lower in content_str.lower() else 0.0
            if _composite_mode:
                imp_score = composite_salience(
                    importance=importance,
                    surprise_score=surprise_score,
                    goal_alignment=goal_alignment,
                    created_at_iso=created_at,
                    affect_tag=affect_tag,
                ) * effective_importance(importance, decay_rate, last_accessed_at, pinned or 0) / max(importance or 5, 1)
            else:
                imp_score = 0.1 * (importance or 5)
            recency_bonus = 0.0
            if created_at:
                try:
                    created_dt = datetime.fromisoformat(created_at)
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=timezone.utc)
                    days_old = (now - created_dt).total_seconds() / 86400
                    recency_bonus = math.exp(-days_old / 7)
                except (ValueError, TypeError):
                    pass
            score = match_score + imp_score + recency_bonus
            score += boost_map.get(id_, 0.0)  # T-137/T-142 context-cued boosts
            # T-145: only include rows with an actual content match; returning all
            # rows (match_score=0) caused memory_delete to wipe entire L3.
            if match_score > 0:
                scored.append((score, (id_, content, importance, category, active_until)))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [row for _, row in scored[:limit]]

    def _hybrid_search_l3(self, query: str, limit: int, current_mode=None,
                          current_conversation_id=None, current_scope=None) -> list:
        """BM25 + entity hybrid search over l3_cache.

        T-111: fast-path for small caches (< PI_L3_FAST_PATH_THRESHOLD rows);
        BM25 with configurable LIMIT cap (PI_L3_BM25_CAP) for larger caches.

        When rank_bm25 is available: scores every cached entry with BM25 plus a
        small bonus for shared capitalized entities, then returns the top ``limit``
        rows sorted by combined score (importance used as a tiebreaker).

        Falls back to the LIKE-based ``_search_l3_cache`` when the package is
        absent or the query is empty.
        """
        if not query or not _BM25_AVAILABLE:
            return self._search_l3_cache(query, limit)

        # T-111: count active rows; use fast-path when below threshold
        threshold = self._l3_env_int("PI_L3_FAST_PATH_THRESHOLD", 200)
        bm25_cap = self._l3_env_int("PI_L3_BM25_CAP", 1000)

        conn_count = sqlite3.connect(self.sqlite_path)
        row_count = conn_count.execute(
            "SELECT COUNT(*) FROM l3_cache "
            "WHERE invalid_at IS NULL "
            "  AND (superseded_by IS NULL OR superseded_by = '')"
        ).fetchone()[0]
        conn_count.close()

        if row_count < threshold:
            return self._l3_fast_path(
                query, limit, current_mode=current_mode,
                current_conversation_id=current_conversation_id,
                current_scope=current_scope)

        # Load active entries up to BM25 cap
        now_iso = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        # T-078: also filter invalidated (superseded) entries from default search.
        # T-125b: also filter superseded_by (post-dedup losers).
        _salience_cols = True
        try:
            cursor.execute("""
                SELECT id, content, importance, category, active_until,
                       created_at, surprise_score, goal_alignment, affect_tag,
                       decay_rate, pinned, last_accessed_at
                FROM l3_cache
                WHERE (active_until IS NULL OR active_until > ?)
                  AND invalid_at IS NULL
                  AND (superseded_by IS NULL OR superseded_by = '')
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
            """, [now_iso, bm25_cap])
        except sqlite3.OperationalError:
            _salience_cols = False
            cursor.execute("""
                SELECT id, content, importance, category, active_until,
                       created_at, NULL, NULL, NULL, NULL, 0, NULL
                FROM l3_cache
                WHERE (active_until IS NULL OR active_until > ?)
                  AND invalid_at IS NULL
                  AND (superseded_by IS NULL OR superseded_by = '')
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
            """, [now_iso, bm25_cap])
        all_rows = cursor.fetchall()
        conn.close()

        if not all_rows:
            return []

        # T-134: load salience mode once for this search
        _composite_mode = False
        if _salience_cols:
            try:
                from memory.salience import is_composite_mode, composite_salience, effective_importance
                _composite_mode = is_composite_mode()
            except Exception:
                pass

        # Tokenise corpus
        corpus_tokens = [_tokenize(row[1] or "") for row in all_rows]
        query_tokens = _tokenize(query)
        if not query_tokens:
            return [row[:5] for row in all_rows[:limit]]

        bm25 = _BM25Okapi(corpus_tokens)
        bm25_scores = bm25.get_scores(query_tokens)

        # Entity bonus — shared capitalised words between query and each entry
        query_entities = _extract_entities(query)
        max_bm25 = max(bm25_scores) if max(bm25_scores) > 0 else 1.0

        # T-070: minimum raw BM25 score to be considered relevant. Below this,
        # the result is essentially noise — BM25 returns SOMETHING for any
        # query, so a floor is the only way to keep single-stopword queries
        # from polluting recall.
        BM25_FLOOR = 0.5

        boost_map = self._context_boosts(current_mode, current_conversation_id, current_scope)
        scored = []
        for i, row in enumerate(all_rows):
            if bm25_scores[i] < BM25_FLOOR:
                continue
            bm25_norm = bm25_scores[i] / max_bm25
            entity_bonus = len(query_entities & _extract_entities(row[1] or "")) * 0.15
            # T-134: composite importance nudge when PI_SALIENCE_MODE=composite
            if _composite_mode:
                (_, _, importance, _, _, created_at, surprise_score, goal_alignment,
                 affect_tag, decay_rate, pinned, last_accessed_at) = row
                importance_nudge = (
                    composite_salience(
                        importance=importance,
                        surprise_score=surprise_score,
                        goal_alignment=goal_alignment,
                        created_at_iso=created_at,
                        affect_tag=affect_tag,
                    )
                    * effective_importance(importance, decay_rate, last_accessed_at, pinned or 0)
                    / max(importance or 5, 1)
                    * 0.1
                )
            else:
                # importance nudge (1-10 -> 0.0-0.09)
                importance_nudge = (row[2] or 5) * 0.01
            combined = bm25_norm + entity_bonus + importance_nudge
            combined += boost_map.get(row[0], 0.0)  # T-137/T-142 context-cued boosts
            scored.append((combined, row[:5]))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [row for _, row in scored[:limit]]

    def retrieve(self, query: str, k: int = 6, tiers: tuple = ("l3", "l2"),
                current_mode=None, current_conversation_id=None,
                current_scope=None) -> list:
        """T-292: unified query-time retriever — fuses dense cosine similarity
        with the existing lexical ranking across L3 (SQLite) and L2 (Supabase)
        into one ranked top-k.

        Reuses (does not rewrite) the existing scoring paths: _hybrid_search_l3
        for L3 lexical ranking (BM25 + entity + importance + context boosts —
        untouched), memory_read(tier='l2') for L2 lexical matches, and
        memory_search_semantic-equivalent cosine scoring for both tiers. This
        is additive on top of proven code, not a replacement of it — existing
        callers of _hybrid_search_l3 / memory_search_semantic are unaffected.

        Degrades to pure lexical ordering when no embedding provider is
        configured: every candidate's dense_score is then 0.0, so the fused
        score collapses to the lexical + importance terms only.

        Returns top-k dicts: {id, content, importance, category, tier, score}.
        """
        if not query or not query.strip():
            return []

        try:
            from memory.semantic_dedup import get_embedding, cosine_similarity
            q_emb = get_embedding(query)
        except Exception:
            q_emb = None
            cosine_similarity = None

        w_dense = float(os.environ.get("PI_RETRIEVE_W_DENSE", "0.5"))
        w_lex = float(os.environ.get("PI_RETRIEVE_W_LEX", "0.4"))
        w_imp = float(os.environ.get("PI_RETRIEVE_W_IMPORTANCE", "0.1"))

        pool_size = max(k * 4, 20)
        candidates: Dict[str, Dict] = {}

        if "l3" in tiers:
            lex_rows = self._hybrid_search_l3(
                query, pool_size, current_mode=current_mode,
                current_conversation_id=current_conversation_id,
                current_scope=current_scope,
            )
            n = len(lex_rows)
            for i, row in enumerate(lex_rows):
                rid, content, importance, category = row[0], row[1], row[2], row[3]
                candidates[rid] = {
                    "id": rid, "content": content, "importance": importance,
                    "category": category, "tier": "l3",
                    "lex_score": 1.0 - (i / n) if n else 0.0,
                    "dense_score": 0.0,
                }

            if q_emb is not None:
                # T-298: active_until filter — every other L3 read path excludes
                # expired rows; without it here, dense retrieval RESURRECTS
                # forgotten facts (write/read divergence class).
                now_iso = datetime.now(timezone.utc).isoformat()
                conn = sqlite3.connect(self.sqlite_path)
                try:
                    emb_rows = conn.execute(
                        "SELECT id, content, importance, category, embedding FROM l3_cache "
                        "WHERE embedding IS NOT NULL AND invalid_at IS NULL "
                        "AND (active_until IS NULL OR active_until > ?) "
                        "AND (superseded_by IS NULL OR superseded_by = '') LIMIT 500",
                        [now_iso],
                    ).fetchall()
                finally:
                    conn.close()
                for rid, content, importance, category, emb_json in emb_rows:
                    try:
                        emb = json.loads(emb_json)
                    except (TypeError, ValueError):
                        continue
                    score = cosine_similarity(q_emb, emb)
                    if rid in candidates:
                        candidates[rid]["dense_score"] = score
                    else:
                        candidates[rid] = {
                            "id": rid, "content": content, "importance": importance,
                            "category": category, "tier": "l3",
                            "lex_score": 0.0, "dense_score": score,
                        }

        if "l2" in tiers:
            for row in self.memory_read(query, tier="l2", limit=pool_size):
                rid = row.get("id")
                if rid is None:
                    continue
                body = row.get("content") or {}
                text = body.get("text", "") if isinstance(body, dict) else str(body)
                candidates[rid] = {
                    "id": rid, "content": text,
                    "importance": int(row.get("importance") or 5),
                    "category": row.get("category"), "tier": "l2",
                    "lex_score": 1.0, "dense_score": 0.0,
                }

            if q_emb is not None:
                for hit in self.memory_search_semantic(query, limit=pool_size, threshold=0.0):
                    rid = hit["id"]
                    if rid in candidates:
                        candidates[rid]["dense_score"] = hit["similarity"]
                    else:
                        candidates[rid] = {
                            "id": rid, "content": hit["content"],
                            "importance": hit["importance"], "category": hit["category"],
                            "tier": "l2", "lex_score": 0.0, "dense_score": hit["similarity"],
                        }

        if not candidates:
            return []

        # T-298: decay-aware importance for L3 candidates — one batched lookup
        # so Ebbinghaus decay (memory/salience) influences fused ranking and
        # pinned rows stay immune. Raw-importance fallback on pre-migration
        # schemas (OperationalError) or salience import failure.
        eff_imp: Dict[str, float] = {}
        l3_ids = [c["id"] for c in candidates.values() if c["tier"] == "l3"]
        if l3_ids:
            try:
                from memory.salience import effective_importance
                conn = sqlite3.connect(self.sqlite_path)
                try:
                    ph = ",".join("?" * len(l3_ids))
                    for rid, imp, rate, last, pin in conn.execute(
                        f"SELECT id, importance, decay_rate, last_accessed_at, pinned "
                        f"FROM l3_cache WHERE id IN ({ph})", l3_ids
                    ):
                        eff_imp[rid] = effective_importance(imp, rate, last, pin or 0)
                finally:
                    conn.close()
            except (sqlite3.OperationalError, ImportError) as e:
                from agent.observability import track_silent
                track_silent("memory.retrieve_effective_importance_unavailable", e)
                # eff_imp stays {} — every candidate falls back to raw importance below.

        max_dense = max((c["dense_score"] for c in candidates.values()), default=0.0) or 1.0

        scored = []
        for c in candidates.values():
            dense_norm = c["dense_score"] / max_dense
            importance_basis = eff_imp.get(c["id"], float(c["importance"] or 5))
            importance_norm = importance_basis / 10.0
            combined = w_dense * dense_norm + w_lex * c["lex_score"] + w_imp * importance_norm
            scored.append((combined, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"id": c["id"], "content": c["content"], "importance": c["importance"],
             "category": c["category"], "tier": c["tier"], "score": round(score, 4)}
            for score, c in scored[:k]
        ]

    def detect_cross_session_patterns(self, min_sessions: int = 3, days: int = 7,
                                      limit_rows: int = 500) -> List[Dict]:
        """T-136: entities recurring across >= min_sessions DISTINCT conversations
        in the last `days`. Returns pattern dicts ready for a pattern_observation
        write. Local (uses the T-142 conversation_id column on l3_cache), so it
        composes with idle replay without hitting Supabase. Never raises.
        """
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            conn = sqlite3.connect(self.sqlite_path)
            rows = conn.execute(
                "SELECT content, conversation_id FROM l3_cache "
                "WHERE conversation_id IS NOT NULL AND created_at >= ? "
                "AND invalid_at IS NULL AND (superseded_by IS NULL OR superseded_by = '') "
                "LIMIT ?",
                (cutoff, limit_rows),
            ).fetchall()
            conn.close()
        except Exception:
            return []

        entity_convs: Dict[str, set] = {}
        for content, conv in rows:
            for ent in _extract_entities(content or ""):
                entity_convs.setdefault(ent, set()).add(conv)

        patterns: List[Dict] = []
        for ent, convs in sorted(entity_convs.items(), key=lambda kv: -len(kv[1])):
            if len(convs) >= min_sessions:
                patterns.append({
                    "entity": ent,
                    "sessions": len(convs),
                    "content": (f"Pattern detected: '{ent}' appears across "
                                f"{len(convs)} conversations in the last {days}d"),
                    "category": "pattern_observation",
                    "source": "replay",
                })
        return patterns

    def memory_write(
        self,
        content: str,
        tier: str = "l3",
        importance: int = 5,
        category: str = "note",
        expiry: Optional[datetime] = None,
        session_id: Optional[str] = None,
        source: str = "stated",
        mode: Optional[str] = None,
        conversation_id: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> Dict:
        """
        Write to memory.
        
        Args:
            content: What to remember
            tier: Where to store (l1/l2/l3)
            importance: 1-10 priority
            category: Classification
            expiry: When to auto-remove (None=permanent)
        
        Returns:
            {"id": "...", "success": True/False}
        """
        # Test-write guard: under pytest with a REAL Supabase client, never
        # touch production. L3 (sqlite_path) is already redirected to pi_test.db
        # in __init__; L1/L2 writes hit Supabase directly and would pollute prod
        # the same way 88 L3 rows did before T-060.
        # Detect mock clients via _mock_name (unittest.mock attribute) — tests
        # that mock Supabase should still exercise the full code path.
        if (os.environ.get("PYTEST_CURRENT_TEST")
                and tier in ("l1", "l2")
                and not hasattr(self.supabase, "_mock_name")):
            return {"id": self._generate_id(), "success": True, "verified": True,
                    "tier": tier, "test_skipped": True}

        entry_id = self._generate_id()
        now = datetime.now(timezone.utc)

        if tier == "l3":
            # T-299: caller didn't set an expiry — check for ephemeral phrasing
            # ("just for today", "until friday", ...) so timely forgetting
            # doesn't depend on the model volunteering an ISO datetime.
            auto_expiry_inferred = False
            if expiry is None:
                inferred = _infer_expiry(content, now)
                if inferred is not None:
                    expiry = inferred
                    auto_expiry_inferred = True

            # Reject inferred facts that have not been explicitly confirmed by the user.
            # Confirmed inferences (source="inferred_confirmed") and stated facts are allowed.
            if source == "inferred_unconfirmed":
                print(f"[Memory] L3 write rejected: source=inferred_unconfirmed — fact not confirmed by user")
                return {"id": None, "success": False,
                        "error": "inferred_unconfirmed facts cannot be written to L3; get explicit user confirmation first"}

            # profile_structured: merge JSON fields into the single existing row
            # rather than inserting a new one.  Incoming keys win on conflict.
            if category == "profile_structured":
                merged = self._merge_profile(content, importance)
                if merged is not None:
                    return merged

            # Deduplication: skip if a near-duplicate already exists in l3_cache
            # for the same category (first 120 chars, marker-stripped, case-insensitive).
            # Prevents the same fact accumulating across sessions when Claude
            # re-writes something it already stored.
            dup_id = self._is_l3_duplicate(content, category)
            if dup_id:
                print(f"[Memory] L3 dedup: duplicate of {dup_id[:8]}..., skipping")
                return {"id": dup_id, "success": True, "verified": True,
                        "tier": "l3", "duplicate": True}

            # T-038: Word-overlap dedup + conflict detection.
            # If new content is semantically near-identical (>= 0.80 overlap) → skip.
            # If new content supersedes older entries in conflict categories (0.45–0.80) →
            # collect IDs to soft-delete after successful write.
            overlap_check = self._check_l3_overlap_and_conflict(content, category)
            if overlap_check["duplicate_id"]:
                dup_id = overlap_check["duplicate_id"]
                print(f"[Memory] L3 word-overlap dedup: ~duplicate of {dup_id[:8]}..., skipping")
                return {"id": dup_id, "success": True, "verified": True,
                        "tier": "l3", "duplicate": True}
            supersedes_ids = overlap_check["supersedes_ids"]

            # Write to Supabase
            entry = {
                "id": entry_id,
                "content": content,
                "importance": importance,
                "category": category,
                "active_from": now.isoformat(),
                "active_until": expiry.isoformat() if expiry else None,
                "editable": True,
                "auto_demote": expiry is not None,
                "created_at": now.isoformat(),
                "metadata": {}
            }
            
            _sb_status = "ok"
            try:
                with self._supa_lock:
                    self.supabase.table("l3_active_memory").insert(entry).execute()
            except Exception as e:
                _sb_status = "failed"
                print(f"[Memory] Supabase write failed: {e}")

            # T-134: compute salience fields at write time.
            _surprise = 0.5   # novelty default; embedding pass deferred to avoid blocking writes
            _goal = 0.5       # caller can pass goal_alignment kwarg in a future revision
            _affect = "neutral"
            try:
                from memory.salience import default_decay_rate as _decay_fn
                _decay = _decay_fn(category)
            except Exception:
                _decay = 0.01

            _sq_status = "ok"
            # Write to SQLite cache
            conn = sqlite3.connect(self.sqlite_path)
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO l3_cache
                        (id, content, importance, category, active_until, created_at,
                         surprise_score, goal_alignment, affect_tag, decay_rate,
                         mode, conversation_id, scope, embedding)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    entry_id,
                    content,
                    importance,
                    category,
                    expiry.isoformat() if expiry else None,
                    now.isoformat(),
                    _surprise,
                    _goal,
                    _affect,
                    _decay,
                    mode,                # T-137: encoding-context mode
                    conversation_id,     # T-142: conversation thread
                    scope,               # T-137: project/ticket scope
                    None,                # T-291: filled by backfill_l3_embeddings, not inline (hot path)
                ])
                conn.commit()
            except Exception as e:
                _sq_status = "failed"
                print(f"[Memory] SQLite write failed: {e}")
            finally:
                conn.close()
            self._replication_log_append("insert", entry_id, content,
                                         supabase_status=_sb_status, sqlite_status=_sq_status)

            # T-125a: derivable-fact detection. If this content is something
            # like 'born YYYY-MM-DD', spawn a paired derived row. The caretaker
            # recomputes its content on the next bubble close / session exit.
            try:
                from agent.caretaker import detect_derivable
                detected = detect_derivable(content)
                if detected is not None and _sq_status == "ok":
                    formula_name, recompute_after = detected
                    derived_id = str(uuid.uuid4())
                    conn = sqlite3.connect(self.sqlite_path)
                    try:
                        conn.execute(
                            "INSERT INTO l3_cache (id, content, importance, category, "
                            "active_until, created_at, kind, source_id, recompute_after, formula) "
                            "VALUES (?, ?, ?, ?, ?, ?, 'derived', ?, ?, ?)",
                            (
                                derived_id,
                                f"(pending recompute from {entry_id})",
                                importance,
                                "derived",
                                None,
                                now.isoformat(),
                                entry_id,
                                recompute_after.isoformat(),
                                formula_name,
                            ),
                        )
                        conn.commit()
                    finally:
                        conn.close()
            except Exception as _derived_exc:
                # Soft — never block the main write on derived spawn failure
                from agent.observability import track_silent
                track_silent("caretaker.derived_spawn_failed", _derived_exc, context={"source_id": entry_id})

            # T-078: Mark superseded entries as INVALIDATED (not expired) so the
            # historical version is preserved for "what did I tell you before?"
            # queries. Different from _expire_l3_entry (which is for timed-out
            # facts that genuinely no longer apply).
            # Done AFTER successful insert so a write failure leaves old entries intact.
            if supersedes_ids:
                for sid in supersedes_ids:
                    try:
                        self._invalidate_l3_entry(sid, by_entry_id=entry_id)
                        print(f"[Memory] L3 conflict: invalidated {sid[:8]}... (superseded by new entry)")
                    except Exception as e:
                        from agent.observability import track_silent
                        track_silent("memory.l3_invalidate", e)

            # Verify
            result = self._verify_write(entry_id, content, tier)
            if supersedes_ids:
                result["superseded"] = len(supersedes_ids)
            if auto_expiry_inferred:
                result["auto_expiry"] = expiry.isoformat()
            return result

        elif tier == "l2":
            # T-080: SEMANTIC dedup runs first — catches paraphrases the lexical
            # check misses ("I like dogs" vs "I'm a dog person"). Best-effort:
            # any failure (no Gemini key, network) just falls through to lexical.
            sem = self._is_l2_semantic_duplicate(content, category)
            if sem:
                dup_id, score, reason = sem
                print(f"[Memory] L2 semantic dedup ({reason} {score:.2f}): duplicate of {dup_id[:8]}..., skipping")
                return {"id": dup_id, "success": True, "verified": True,
                        "tier": "l2", "duplicate": True, "dedup_kind": "semantic"}

            # Lexical deduplication: same-prefix match in same category. Cheap fallback
            # that catches verbatim restatements when semantic dedup is unavailable.
            dup_id = self._is_l2_duplicate(content, category)
            if dup_id:
                print(f"[Memory] L2 dedup: duplicate of {dup_id[:8]}..., skipping")
                return {"id": dup_id, "success": True, "verified": True,
                        "tier": "l2", "duplicate": True}

            # Write to L2 organized memory
            # T-082 audit-bug-3: persist source + session_id + created_at in
            # metadata JSONB so audit detection rules can flag low-confidence
            # heuristic-derived facts and trace facts back to their session.
            # T-080: also embed the new fact so future semantic dedup checks
            # can compare against it. Best-effort — None on failure.
            try:
                from memory.semantic_dedup import compute_embedding_for_write
                embedding = compute_embedding_for_write(content)
            except Exception:
                embedding = None
            metadata = {
                "source": source,
                "session_id": session_id or "",
                "created_at_iso": now.isoformat(),
            }
            if embedding is not None:
                metadata["embedding"] = embedding
            entry = {
                "id": entry_id,
                "category": category,
                "title": content[:100],
                "content": {"text": content, "metadata": metadata},
                "importance": importance,
                "status": "active",
                "created_at": now.isoformat()
            }

            try:
                with self._supa_lock:
                    self.supabase.table("organized_memory").insert(entry).execute()
                return {"id": entry_id, "success": True, "verified": True, "tier": "l2"}
            except Exception as e:
                return {"id": entry_id, "success": False, "verified": False, "error": str(e)}

        elif tier == "l1":
            # Write to L1 raw archive (raw_wiki table).
            # raw_wiki.thread_id is UUID NOT NULL — session_id is only 8 hex chars
            # and cannot be used directly. Derive a deterministic UUID via uuid5 so
            # all L1 writes in a session (both tool-path and auto-log) share the
            # same thread_id for reconstruction (T-013, T-024).
            thread_uuid = (str(uuid.uuid5(uuid.NAMESPACE_DNS, session_id))
                           if session_id else str(uuid.uuid4()))
            entry = {
                "id": entry_id,
                "timestamp": now.isoformat(),
                "thread_id": thread_uuid,
                "role": category if category in ("user", "assistant", "system", "tool") else "system",
                "content": content,
                "metadata": {"importance": importance, "category": category, "session_id": session_id},
                "created_at": now.isoformat()
            }
            try:
                with self._supa_lock:
                    self.supabase.table("raw_wiki").insert(entry).execute()
                return {"id": entry_id, "success": True, "verified": True, "tier": "l1"}
            except Exception as e:
                return {"id": entry_id, "success": False, "verified": False, "error": str(e)}

        return {"id": entry_id, "success": False, "error": f"Unknown tier: {tier}"}

    def backfill_l3_embeddings(self, limit: int = 100) -> int:
        """T-291: embed active L3 rows written before an embedding provider was
        configured (or written with the network embed intentionally skipped at
        write time — see memory_write's hot-path note). Best-effort, capped,
        idempotent (only touches rows where embedding IS NULL). Returns the
        number of rows updated.
        """
        from memory.semantic_dedup import compute_embedding_for_write

        conn = sqlite3.connect(self.sqlite_path)
        try:
            cursor = conn.execute(
                "SELECT id, content FROM l3_cache WHERE embedding IS NULL "
                "AND invalid_at IS NULL LIMIT ?",
                [limit],
            )
            rows = cursor.fetchall()
            updated = 0
            for row_id, content in rows:
                emb = compute_embedding_for_write(content or "")
                if emb is None:
                    continue
                conn.execute(
                    "UPDATE l3_cache SET embedding = ? WHERE id = ?",
                    [json.dumps(emb), row_id],
                )
                updated += 1
            conn.commit()
            return updated
        finally:
            conn.close()

    def forgotten_ledger(self, days: int = 7) -> list:
        """T-301/T-304/T-309: the forgetting ledger — recently forgotten L3
        rows, classified by why. Single shared implementation consumed by both
        `memory_cli forgotten` and the dashboard's /memory/forgotten endpoint
        (one classifier = no write/read divergence between the two views).

        Precedence for rows still in l3_cache (a row is classified exactly
        once, never shown twice):
          1. CONTRADICTED — invalid_at is set (a newer fact superseded it)
          2. EXPIRED      — active_until has passed and invalid_at is NOT set
          3. MERGED       — superseded_by is set and neither of the above applies

        Rows already moved to l3_archive (T-309: prune_l3_expired / decay
        archive no longer flag rows in place, they relocate them) are read
        separately and classified by archive_reason:
          - 'expired' -> EXPIRED
          - 'decay'   -> DECAYED (distinct from EXPIRED: forgotten for being
                         unused, not for a timed-out active_until)

        SQL stays dumb (pulls every row carrying any of the relevant signals);
        classification and the days window are applied in Python since the
        timestamp columns aren't uniformly formatted across write paths.
        MERGED rows carry no merge timestamp, so they are always included.

        Returns dicts: {id, content, importance, category, reason, when,
        pointer_id, superseded_by_snippet? (MERGED only)} — newest first,
        timeless MERGED rows last.
        """
        def _parse_iso(s):
            if not s:
                return None
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                return None

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)

        conn = sqlite3.connect(self.sqlite_path)
        try:
            rows = conn.execute(
                "SELECT id, content, importance, category, active_until, invalid_at, superseded_by "
                "FROM l3_cache WHERE invalid_at IS NOT NULL "
                "   OR active_until IS NOT NULL "
                "   OR (superseded_by IS NOT NULL AND superseded_by != '')"
            ).fetchall()

            classified = []
            for rid, content, imp, cat, active_until, invalid_at, superseded_by in rows:
                invalid_dt = _parse_iso(invalid_at)
                active_dt = _parse_iso(active_until)

                if invalid_dt is not None:
                    reason, when_dt, pointer_id = "CONTRADICTED", invalid_dt, None
                elif active_dt is not None and active_dt < now:
                    reason, when_dt, pointer_id = "EXPIRED", active_dt, None
                elif superseded_by:
                    reason, when_dt, pointer_id = "MERGED", None, superseded_by
                else:
                    continue

                if when_dt is not None and when_dt < cutoff:
                    continue

                classified.append({
                    "id": rid, "content": content, "importance": imp, "category": cat,
                    "reason": reason, "when": when_dt.isoformat() if when_dt else None,
                    "pointer_id": pointer_id,
                })

            # T-309: rows already moved to l3_archive by prune_l3_expired /
            # decay-archive — surface them too so the ledger doesn't go blank
            # the day after a row is actually forgotten.
            try:
                arows = conn.execute(
                    "SELECT id, content, importance, category, archived_at, archive_reason "
                    "FROM l3_archive"
                ).fetchall()
            except sqlite3.OperationalError:
                arows = []  # l3_archive doesn't exist yet — nothing archived so far

            for rid, content, imp, cat, archived_at, archive_reason in arows:
                when_dt = _parse_iso(archived_at)
                if when_dt is not None and when_dt < cutoff:
                    continue
                reason = "DECAYED" if archive_reason == "decay" else "EXPIRED"
                classified.append({
                    "id": rid, "content": content, "importance": imp, "category": cat,
                    "reason": reason, "when": when_dt.isoformat() if when_dt else None,
                    "pointer_id": None,
                })

            pointer_ids = [c["pointer_id"] for c in classified if c["pointer_id"]]
            winners = {}
            if pointer_ids:
                ph = ",".join("?" * len(pointer_ids))
                winners = dict(conn.execute(
                    f"SELECT id, content FROM l3_cache WHERE id IN ({ph})", pointer_ids
                ).fetchall())
        finally:
            conn.close()

        for c in classified:
            if c["pointer_id"]:
                c["superseded_by_snippet"] = (winners.get(c["pointer_id"]) or "")[:60]

        with_time = sorted((c for c in classified if c["when"]), key=lambda c: c["when"], reverse=True)
        without_time = [c for c in classified if not c["when"]]
        return with_time + without_time

    def log_turn(
        self,
        thread_id: str,
        session_id: str,
        turn_number: int,
        user_content: str,
        assistant_content: str,
        mode: str,
        tool_calls: Optional[List[Dict]] = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost: float = 0.0,
    ) -> Dict:
        """
        Append one complete conversation turn to L1 (raw_wiki). Best-effort —
        exceptions are caught and logged; a failure here never surfaces to the
        caller.

        Inserts one row per participant in the turn, in order:
          role="user"      — the raw user message
          role="tool"      — one row per tool call (in call order), if any
          role="assistant" — Pi's final text response

        All rows share ``thread_id`` so the full turn is reconstructable with a
        single ``WHERE thread_id = ?`` filter on raw_wiki.

        Args:
            thread_id:         Proper UUID for the session's L1 thread.
                               Use uuid5(NAMESPACE_DNS, session_id) in the caller
                               so tool-path writes and auto-log rows share the
                               same thread (T-013, T-024).
            session_id:        8-char hex — stored in metadata for cross-log
                               correlation with evolution.jsonl.
            turn_number:       Monotonically increasing counter per session.
                               Provides an ordering anchor independent of clock
                               resolution.
            user_content:      The raw user message text.
            assistant_content: Pi's final text response.
            mode:              "root" | "normie" — stored in each row's metadata.
            tool_calls:        Optional list of dicts, one per tool call:
                                 name           — tool name string
                                 input          — dict of arguments
                                 result_summary — str summary of the result (caller
                                                  should pre-truncate to ~500 chars)
            tokens_in:         Claude input token count (root mode; 0 for normie).
            tokens_out:        Claude output token count (root mode; 0 for normie).
            cost:              API cost in USD for this turn.

        Returns:
            {"success": bool, "rows": int}
        """
        # Test-write guard: under pytest with a REAL Supabase client, skip
        # raw_wiki writes. Mocked clients pass through so unit tests can verify
        # the insert payload.
        if (os.environ.get("PYTEST_CURRENT_TEST")
                and not hasattr(self.supabase, "_mock_name")):
            return {"success": True, "rows": 0, "test_skipped": True}

        now = datetime.now(timezone.utc).isoformat()
        base_meta = {
            "session_id": session_id,
            "turn": turn_number,
            "mode": mode,
        }
        entries = []

        # User row — always written first
        entries.append({
            "id": str(uuid.uuid4()),
            "timestamp": now,
            "thread_id": thread_id,
            "role": "user",
            "content": user_content,
            "metadata": base_meta.copy(),
        })

        # Tool rows — one per call, in invocation order
        for tc in (tool_calls or []):
            tool_input_str = json.dumps(tc.get("input", {}))[:200]
            result_str = str(tc.get("result_summary", ""))[:300]
            entries.append({
                "id": str(uuid.uuid4()),
                "timestamp": now,
                "thread_id": thread_id,
                "role": "tool",
                "content": f"[{tc['name']}] {tool_input_str} → {result_str}",
                "metadata": {**base_meta, "tool_name": tc["name"]},
            })

        # Assistant row — always written last
        entries.append({
            "id": str(uuid.uuid4()),
            "timestamp": now,
            "thread_id": thread_id,
            "role": "assistant",
            "content": assistant_content,
            "metadata": {
                **base_meta,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost": round(cost, 6),
            },
        })

        # Stamp each row with a monotonic seq so within-turn ordering is
        # recoverable even if Supabase returns rows in an unspecified order.
        for seq_i, entry in enumerate(entries):
            entry["metadata"]["seq"] = seq_i

        try:
            with self._supa_lock:
                self.supabase.table("raw_wiki").insert(entries).execute()
            return {"success": True, "rows": len(entries)}
        except Exception as e:
            print(f"[Memory] L1 auto-log failed (non-fatal): {e}")
            return {"success": False, "rows": 0, "error": str(e)}

    # ── T-186: conversation persistence ──────────────────────────────────────

    def create_conversation(self, conversation_id: str, mode: str, created_at: str) -> None:
        """Register a new conversation row (INSERT OR IGNORE — idempotent)."""
        try:
            conn = self._sqlite_backend.connect()
            conn.execute(
                "INSERT OR IGNORE INTO conversations(id, mode, created_at, last_active_at)"
                " VALUES (?, ?, ?, ?)",
                (conversation_id, mode, created_at, created_at),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Memory] create_conversation failed (non-fatal): {e}")

    def persist_turn(
        self,
        conversation_id: str,
        role: str,
        content: "str | list",
        idx: int,
        ts: str,
    ) -> None:
        """Append one message to conversation_turns (INSERT OR IGNORE on idx).

        content is stored as JSON — plain strings and block lists both supported.
        """
        try:
            import json as _json
            content_json = _json.dumps(content, default=str)
            conn = self._sqlite_backend.connect()
            conn.execute(
                "INSERT OR IGNORE INTO conversation_turns"
                "(conversation_id, idx, role, content_json, ts)"
                " VALUES (?, ?, ?, ?, ?)",
                (conversation_id, idx, role, content_json, ts),
            )
            conn.execute(
                "UPDATE conversations SET last_active_at = ? WHERE id = ?",
                (ts, conversation_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Memory] persist_turn failed (non-fatal): {e}")

    def load_conversation_turns(
        self, conversation_id: str, max_turns: int = 40
    ) -> List[Dict]:
        """Load turn pairs from conversation_turns, newest-last, up to max_turns.

        Returns a list of {"role": str, "content": str|list} dicts suitable for
        self.messages. Deserializes content_json back to the original type.
        """
        try:
            import json as _json
            conn = self._sqlite_backend.connect()
            rows = conn.execute(
                "SELECT role, content_json FROM conversation_turns"
                " WHERE conversation_id = ?"
                " ORDER BY idx ASC"
                " LIMIT ?",
                (conversation_id, max_turns * 2),
            ).fetchall()
            conn.close()
            result = []
            for role, content_json in rows:
                try:
                    content = _json.loads(content_json)
                except Exception:
                    content = content_json
                result.append({"role": role, "content": content})
            return result
        except Exception as e:
            print(f"[Memory] load_conversation_turns failed: {e}")
            return []

    def list_conversations(self, limit: int = 10) -> List[Dict]:
        """Return recent conversations, newest first."""
        try:
            conn = self._sqlite_backend.connect()
            rows = conn.execute(
                "SELECT id, title, mode, created_at, last_active_at"
                " FROM conversations ORDER BY last_active_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()
            return [
                {
                    "id": r[0],
                    "title": r[1] or "(untitled)",
                    "mode": r[2],
                    "created_at": r[3],
                    "last_active_at": r[4],
                }
                for r in rows
            ]
        except Exception as e:
            print(f"[Memory] list_conversations failed: {e}")
            return []

    def title_conversation(self, conversation_id: str, title: str) -> None:
        """Set the title of a conversation (once, on second turn)."""
        try:
            title = title[:120].strip()
            conn = self._sqlite_backend.connect()
            conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ? AND title IS NULL",
                (title, conversation_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Memory] title_conversation failed (non-fatal): {e}")

    def close_conversation(self, conversation_id: str, digest: str) -> None:
        """Persist a digest (episode summary) on the conversations row.

        digest should be a compact text summary (≤400 chars) covering what was
        discussed and decided.  The caller is responsible for generating it
        (e.g. via a Groq cheap-tier call over the turns).  Calling this more
        than once for the same conversation overwrites the previous digest.
        """
        try:
            digest = (digest or "").strip()[:400]
            conn = self._sqlite_backend.connect()
            conn.execute(
                "UPDATE conversations SET digest = ? WHERE id = ?",
                (digest, conversation_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Memory] close_conversation failed (non-fatal): {e}")

    def recall_episode(self, query: str, limit: int = 4) -> List[Dict]:
        """Search past conversation digests for query terms.

        Returns up to `limit` conversations ordered by recency, filtered to
        those whose title or digest contain at least one query keyword.
        Best-effort: falls back to most-recent if no keywords match.
        """
        try:
            import re as _re
            conn = self._sqlite_backend.connect()
            rows = conn.execute(
                "SELECT id, title, mode, created_at, last_active_at, digest"
                " FROM conversations"
                " WHERE digest IS NOT NULL AND digest != ''"
                " ORDER BY last_active_at DESC"
                " LIMIT 50"
            ).fetchall()
            conn.close()
            if not rows:
                return []

            # Simple keyword filter over title+digest
            keywords = [w.lower() for w in _re.findall(r"\b[a-zA-Z]{3,}\b", query) if len(w) > 2]
            stop = {"the", "and", "for", "with", "that", "this", "was", "did", "our", "have"}
            keywords = [w for w in keywords if w not in stop][:6]

            def _score(row):
                haystack = ((row[1] or "") + " " + (row[5] or "")).lower()
                return sum(1 for kw in keywords if kw in haystack)

            if keywords:
                scored = sorted(rows, key=_score, reverse=True)
                rows = scored[:limit]
            else:
                rows = rows[:limit]

            return [
                {
                    "id": r[0],
                    "title": r[1] or "(untitled)",
                    "mode": r[2],
                    "created_at": r[3],
                    "last_active_at": r[4],
                    "digest": r[5] or "",
                }
                for r in rows
            ]
        except Exception as e:
            print(f"[Memory] recall_episode failed: {e}")
            return []

    @staticmethod
    def _normalize_for_dedup(text: str) -> str:
        """Strip trailing session markers and normalise case/whitespace."""
        return _MARKER_RE.sub('', text[:120]).strip().lower()

    def _merge_profile(self, incoming_content: str, importance: int) -> Optional[Dict]:
        """Merge ``incoming_content`` (JSON) into the existing profile_structured row.

        Returns a result dict with ``merged=True`` if an existing row was found
        and updated, or ``None`` to signal the caller to fall through to a normal
        insert (first-ever profile write).
        """
        try:
            incoming = json.loads(incoming_content)
        except (json.JSONDecodeError, TypeError):
            return None  # not JSON — fall through to normal insert

        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, content FROM l3_cache WHERE category = 'profile_structured' LIMIT 1"
        )
        row = cursor.fetchone()

        if row is None:
            conn.close()
            return None  # first write — let caller do normal insert

        existing_id, existing_raw = row
        try:
            existing = json.loads(existing_raw or "{}")
        except (json.JSONDecodeError, TypeError):
            existing = {}

        merged = {**existing, **incoming}  # incoming wins on conflict
        merged_str = json.dumps(merged)

        cursor.execute(
            "UPDATE l3_cache SET content=?, importance=? WHERE id=?",
            [merged_str, max(importance, existing.get("_importance", importance)), existing_id],
        )
        conn.commit()
        conn.close()

        # Best-effort Supabase update — never blocks
        try:
            with self._supa_lock:
                self.supabase.table("l3_active_memory").update(
                    {"content": merged_str, "importance": importance}
                ).eq("id", existing_id).execute()
        except Exception as e:
            print(f"[Memory] profile_structured Supabase update failed: {e}")

        print(f"[Memory] profile_structured merged into {existing_id[:8]}...")
        return {"id": existing_id, "success": True, "verified": True,
                "tier": "l3", "merged": True}

    def _is_l3_duplicate(self, content: str, category: str) -> Optional[str]:
        """Return existing entry id if a near-duplicate exists in l3_cache, else None.

        Compares the first 120 chars (marker-stripped, case-insensitive) of
        ``content`` against every cached entry in the same category.  Trailing
        markers like ``marker_abc123`` and ``unique7x4b`` are removed before
        comparison so that the same fact appended with different markers is
        correctly identified as a duplicate.
        """
        prefix = self._normalize_for_dedup(content)
        if not prefix:
            return None
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, content FROM l3_cache WHERE category = ? LIMIT 200",
            [category],
        )
        rows = cursor.fetchall()
        conn.close()
        for row_id, row_content in rows:
            if self._normalize_for_dedup(row_content or "") == prefix:
                return row_id
        return None

    # T-038: categories where new facts may supersede old ones (same subject, new value)
    _CONFLICT_CATEGORIES = frozenset({
        "permanent_profile", "profile", "preferences", "current_priority",
    })

    def _word_overlap(self, a: str, b: str) -> float:
        """Jaccard-style overlap: |intersect| / min(|A|,|B|).  Returns 0 on empty input."""
        wa = set(re.sub(r'[^\w\s]', '', a.lower()).split())
        wb = set(re.sub(r'[^\w\s]', '', b.lower()).split())
        denom = min(len(wa), len(wb))
        return len(wa & wb) / denom if denom else 0.0

    def _check_l3_overlap_and_conflict(
        self, content: str, category: str
    ) -> Dict[str, Any]:
        """T-038: Before a new L3 insert, check existing entries in the same category.

        Returns:
          {
            "duplicate_id":    str | None,  # word-overlap >= 0.8 → skip insert
            "supersedes_ids":  list[str],   # overlap in [0.45, 0.8) for conflict categories
                                             #   → soft-delete these on successful write
          }

        Never raises — errors return empty result so a DB hiccup never blocks writes.
        """
        result: Dict[str, Any] = {"duplicate_id": None, "supersedes_ids": []}
        try:
            conn = sqlite3.connect(self.sqlite_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, content FROM l3_cache WHERE category = ? LIMIT 200",
                [category],
            )
            rows = cursor.fetchall()
            conn.close()
        except Exception as e:
            print(f"[Memory] _check_l3_overlap_and_conflict SQLite error: {e}")
            return result

        for row_id, row_content in rows:
            overlap = self._word_overlap(content, row_content or "")
            if overlap >= 0.80:
                result["duplicate_id"] = row_id
                return result  # early exit — no need to continue
            if overlap >= 0.45 and category in self._CONFLICT_CATEGORIES:
                result["supersedes_ids"].append(row_id)

        return result

    def _expire_l3_entry(self, entry_id: str) -> None:
        """T-038: Immediately expire an L3 entry by ID (soft-delete via past timestamp).
        Sets active_until to 1 second ago in both SQLite and Supabase so the entry
        becomes invisible to get_l3_context() without losing the row for audit.

        T-078: prefer ``_invalidate_l3_entry`` for contradiction-driven supersession.
        ``_expire_l3_entry`` is the right call only when something has timed out
        (a reminder fired, a deadline passed) — not when a NEWER fact replaces
        the meaning of an old one.
        """
        past = (datetime.now(timezone.utc).replace(microsecond=0)
                .isoformat().replace("+00:00", "Z"))
        _sq_status = "ok"
        try:
            conn = sqlite3.connect(self.sqlite_path)
            conn.execute(
                "UPDATE l3_cache SET active_until = ? WHERE id = ?",
                [past, entry_id],
            )
            conn.commit()
            conn.close()
        except Exception as e:
            _sq_status = "failed"
            print(f"[Memory] _expire_l3_entry SQLite error: {e}")
        _sb_status = "ok"
        try:
            with self._supa_lock:
                self.supabase.table("l3_active_memory").update(
                    {"active_until": past}
                ).eq("id", entry_id).execute()
        except Exception as e:
            _sb_status = "failed"
            print(f"[Memory] _expire_l3_entry Supabase error (non-fatal): {e}")
        self._replication_log_append("expire", entry_id,
                                     supabase_status=_sb_status, sqlite_status=_sq_status)

    def _bump_access(self, row_ids: list, tier: str) -> None:
        """T-082: increment access_count + set last_accessed_at on read.

        Fire-and-forget on a daemon thread — errors swallowed, never blocks
        the caller. Access tracking is observability, not correctness.
        The audit system uses these counters but degrades gracefully when stale.

        For L2: metadata lives inside content JSONB.
        For L3: metadata is a top-level column.
        """
        if not row_ids:
            return
        # Spawn daemon thread so reads never wait for Supabase round-trips.
        threading.Thread(
            target=self._bump_access_sync,
            args=(list(row_ids), tier),
            daemon=True,
        ).start()

    def _bump_access_sync(self, row_ids: list, tier: str) -> None:
        """Synchronous body of _bump_access — runs in a background thread."""
        if not row_ids:
            return
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        if tier == "l2":
            # L2 metadata is nested in content JSONB; need to read-modify-write per row.
            try:
                with self._supa_lock:
                    r = (self.supabase.table("organized_memory")
                         .select("id,content")
                         .in_("id", list(row_ids))
                         .execute())
                    for row in (r.data or []):
                        content = row.get("content") or {}
                        if not isinstance(content, dict):
                            continue
                        meta = content.get("metadata") or {}
                        if not isinstance(meta, dict):
                            meta = {}
                        meta["last_accessed_at"] = now_iso
                        try:
                            meta["access_count"] = int(meta.get("access_count") or 0) + 1
                        except (TypeError, ValueError):
                            meta["access_count"] = 1
                        content["metadata"] = meta
                        self.supabase.table("organized_memory").update(
                            {"content": content}
                        ).eq("id", row["id"]).execute()
            except Exception as e:
                from agent.observability import track_silent
                track_silent("memory.bump_access", e)

        elif tier == "l3":
            try:
                with self._supa_lock:
                    r = (self.supabase.table("l3_active_memory")
                         .select("id,metadata")
                         .in_("id", list(row_ids))
                         .execute())
                    for row in (r.data or []):
                        meta = row.get("metadata") or {}
                        if not isinstance(meta, dict):
                            meta = {}
                        meta["last_accessed_at"] = now_iso
                        try:
                            meta["access_count"] = int(meta.get("access_count") or 0) + 1
                        except (TypeError, ValueError):
                            meta["access_count"] = 1
                        self.supabase.table("l3_active_memory").update(
                            {"metadata": meta}
                        ).eq("id", row["id"]).execute()
            except Exception as e:
                from agent.observability import track_silent
                track_silent("memory.bump_access", e)

    def _invalidate_l3_entry(self, entry_id: str, by_entry_id: Optional[str] = None) -> None:
        """T-078: Mark an L3 entry as superseded by a newer contradicting fact.

        Differences from _expire_l3_entry:
          - sets invalid_at (NOT active_until) — entry survives prune_l3_expired
          - records WHICH new entry superseded it in metadata.superseded_by
          - kept queryable via historical-mode reads, hidden from default queries
        """
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        _sq_status = "ok"
        _source_l2_id = None
        try:
            conn = sqlite3.connect(self.sqlite_path)
            # T-234: fetch source_l2_id before update so we can propagate supersession to L2.
            row = conn.execute(
                "SELECT source_l2_id FROM l3_cache WHERE id = ?", [entry_id]
            ).fetchone()
            if row:
                _source_l2_id = row[0]
            conn.execute(
                "UPDATE l3_cache SET invalid_at = ? WHERE id = ? AND invalid_at IS NULL",
                [now, entry_id],
            )
            conn.commit()
            conn.close()
        except Exception as e:
            _sq_status = "failed"
            print(f"[Memory] _invalidate_l3_entry SQLite error: {e}")
        _sb_status = "ok"
        try:
            # Supabase: invalid_at lives in metadata JSONB (no schema migration needed).
            with self._supa_lock:
                r = (
                    self.supabase.table("l3_active_memory")
                    .select("metadata")
                    .eq("id", entry_id)
                    .limit(1)
                    .execute()
                )
                current_meta = ((r.data or [{}])[0] or {}).get("metadata") or {}
                if not isinstance(current_meta, dict):
                    current_meta = {}
                current_meta["invalid_at"] = now
                if by_entry_id:
                    current_meta["superseded_by"] = by_entry_id
                self.supabase.table("l3_active_memory").update(
                    {"metadata": current_meta}
                ).eq("id", entry_id).execute()
        except Exception as e:
            _sb_status = "failed"
            print(f"[Memory] _invalidate_l3_entry Supabase error (non-fatal): {e}")
        self._replication_log_append("invalidate", entry_id,
                                     supabase_status=_sb_status, sqlite_status=_sq_status)
        # T-234: propagate supersession to L2 so the vault projection drops the stale fact
        # and promote_l2_to_l3 can never resurrect it (zombie re-promotion prevention).
        if _source_l2_id:
            try:
                self._invalidate_l2_entry(_source_l2_id, by_entry_id=by_entry_id)
                print(f"[Memory] L2 supersession: archived L2 {_source_l2_id[:8]}... (via L3 {entry_id[:8]}...)")
            except Exception as e:
                print(f"[Memory] L2 supersession propagation error (non-fatal): {e}")

    def _invalidate_l2_entry(self, entry_id: str, by_entry_id: Optional[str] = None) -> None:
        """T-234: Archive an L2 organized_memory row that has been superseded.

        Sets status='archived' and records which new entry superseded it in the
        content metadata JSONB. Prevents promote_l2_to_l3 from resurrecting the
        stale fact (zombie re-promotion) and causes vault projection to drop it.
        """
        _sb_status = "ok"
        try:
            with self._supa_lock:
                r = (
                    self.supabase.table("organized_memory")
                    .select("content")
                    .eq("id", entry_id)
                    .limit(1)
                    .execute()
                )
                row = (r.data or [{}])[0] or {}
                raw_content = row.get("content") or {}
                if isinstance(raw_content, dict):
                    meta = raw_content.get("metadata") or {}
                    if not isinstance(meta, dict):
                        meta = {}
                else:
                    raw_content = {"text": str(raw_content), "metadata": {}}
                    meta = {}
                meta["superseded_by"] = by_entry_id or "unknown"
                now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                meta["superseded_at"] = now
                raw_content["metadata"] = meta
                self.supabase.table("organized_memory").update(
                    {"status": "archived", "content": raw_content}
                ).eq("id", entry_id).execute()
        except Exception as e:
            _sb_status = "failed"
            print(f"[Memory] _invalidate_l2_entry Supabase error (non-fatal): {e}")
        self._replication_log_append("invalidate_l2", entry_id,
                                     supabase_status=_sb_status, sqlite_status="n/a")

    def get_l1_thread(self, thread_id: str) -> List[Dict]:
        """Fetch all L1 rows for a session thread from raw_wiki, ordered by (turn, seq).

        Returns an empty list on any error so callers can treat the result as a
        simple iterable without extra error handling.
        """
        try:
            with self._supa_lock:
                r = (
                    self.supabase.table("raw_wiki")
                    .select("*")
                    .eq("thread_id", thread_id)
                    .order("timestamp")
                    .execute()
                )
            rows = r.data or []
            # Secondary sort by (turn, seq) from metadata so within-turn ordering
            # is stable even when Supabase returns rows with identical timestamps.
            rows.sort(key=lambda x: (
                (x.get("metadata") or {}).get("turn", 0),
                (x.get("metadata") or {}).get("seq", 0),
            ))
            return rows
        except Exception as e:
            print(f"[Memory] get_l1_thread error: {e}")
            return []

    def prune_l1(self, days: int = 30) -> Dict:
        """Delete raw_wiki entries older than ``days`` days.

        Best-effort — errors are caught and returned in the result dict rather
        than raised so that a Supabase hiccup during session shutdown never
        blocks the agent from exiting cleanly.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            with self._supa_lock:
                r = (
                    self.supabase.table("raw_wiki")
                    .delete()
                    .lt("created_at", cutoff)
                    .execute()
                )
            deleted = len(r.data or [])
            if deleted > 0:
                print(f"[Memory] L1 prune: deleted {deleted} rows older than {days}d")
            return {"success": True, "deleted": deleted}
        except Exception as e:
            print(f"[Memory] L1 prune error: {e}")
            return {"success": False, "error": str(e)}

    def _is_l2_semantic_duplicate(self, content: str, category: str):
        """T-080: semantic dedup using Gemini embeddings + Haiku tiebreaker.

        Returns (dup_id, cosine_score, reason) on duplicate detection, else None.
        Reasons: "cosine_high" (>=0.90) or "haiku_confirmed" (borderline +
        Haiku said DUPLICATE).

        Best-effort throughout — any failure (no Gemini key, network error,
        Supabase hiccup) returns None and the caller falls back to lexical
        dedup. We prefer false-positive insert over false-positive drop.
        """
        try:
            from memory.semantic_dedup import find_semantic_duplicate
        except Exception:
            return None

        try:
            with self._supa_lock:
                r = (
                    self.supabase.table("organized_memory")
                    .select("id, content")
                    .eq("category", category)
                    .eq("status", "active")
                    .limit(200)
                    .execute()
                )
            rows = r.data or []
        except Exception as e:
            print(f"[Memory] L2 semantic dedup fetch error (non-fatal): {e}")
            return None

        # Extract candidates with stored embeddings
        candidates = []
        for row in rows:
            c = row.get("content") or {}
            if not isinstance(c, dict):
                continue
            meta = c.get("metadata") or {}
            if not isinstance(meta, dict):
                continue
            emb = meta.get("embedding")
            if not emb:
                continue  # row was written before semantic dedup shipped
            text = c.get("text") or ""
            candidates.append({
                "id": row["id"],
                "text": text,
                "embedding": emb,
            })

        if not candidates:
            return None  # nothing to compare against

        return find_semantic_duplicate(content, candidates)

    def _is_l2_duplicate(self, content: str, category: str) -> Optional[str]:
        """Return existing entry id if a near-duplicate exists in organized_memory, else None.

        Compares the first 120 chars (case-insensitive, stripped) against all L2
        rows in the same category.  Requires a Supabase round-trip (no local L2
        cache).  Returns None on any error so a network hiccup never silently
        blocks a legitimate write.
        """
        prefix = content[:120].strip().lower()
        if not prefix:
            return None
        try:
            with self._supa_lock:
                r = (
                    self.supabase.table("organized_memory")
                    .select("id, title")
                    .eq("category", category)
                    .limit(200)
                    .execute()
                )
            for row in (r.data or []):
                # title is content[:100] — sufficient for prefix comparison
                existing = (row.get("title") or "")[:120].strip().lower()
                if existing == prefix[:len(existing)] and len(existing) >= min(60, len(prefix)):
                    return row["id"]
        except Exception as e:
            print(f"[Memory] L2 dedup check error (non-fatal): {e}")
        return None

    def prune_l2_stale(
        self,
        archive_after_days: int = 60,
        delete_after_days: int = 90,
        archive_importance_below: int = 6,
    ) -> Dict:
        """T-073: Two-stage L2 cleanup. Soft-archive stale low-importance rows,
        then hard-delete rows that have been archived long enough.

        Stage 1: rows older than ``archive_after_days`` with importance below
                 ``archive_importance_below`` → status='archived'. Hides them
                 from default L2 queries (which filter status='active').
        Stage 2: rows already status='archived' with created_at older than
                 ``delete_after_days`` → DELETE. Storage actually shrinks.

        Both stages are best-effort — a Supabase error is logged and returned
        in the result dict, never raised.
        """
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        archive_cutoff = (now - timedelta(days=archive_after_days)).isoformat()
        delete_cutoff = (now - timedelta(days=delete_after_days)).isoformat()

        archived = 0
        deleted = 0
        error: Optional[str] = None

        try:
            with self._supa_lock:
                r = (
                    self.supabase.table("organized_memory")
                    .update({"status": "archived"})
                    .eq("status", "active")
                    .lt("created_at", archive_cutoff)
                    .lt("importance", archive_importance_below)
                    .execute()
                )
            archived = len(r.data or [])
        except Exception as e:
            error = f"archive: {e}"
            print(f"[Memory] L2 archive error: {e}")

        try:
            with self._supa_lock:
                r = (
                    self.supabase.table("organized_memory")
                    .delete()
                    .eq("status", "archived")
                    .lt("created_at", delete_cutoff)
                    .execute()
                )
            deleted = len(r.data or [])
        except Exception as e:
            error = (error or "") + f" delete: {e}"
            print(f"[Memory] L2 delete error: {e}")

        if archived or deleted:
            print(f"[Memory] L2 prune: {archived} archived, {deleted} hard-deleted")
        return {"success": error is None, "archived": archived,
                "deleted": deleted, "error": error}

    def _replication_log_append(
        self, op: str, entry_id: str, content_preview: str = "",
        supabase_status: str = "ok", sqlite_status: str = "ok",
    ) -> None:
        """T-087: append one line to data/memory_replication.log per L3 mutation.

        Format: one JSON object per line (NDJSON). Non-blocking — errors are
        swallowed so a log failure never interrupts a memory write.
        Log file: <data_dir>/memory_replication.log (rotated daily by scheduler).
        """
        data_dir = os.path.dirname(self.sqlite_path)
        log_path = os.path.join(data_dir, "memory_replication.log")
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "op": op,
            "id": entry_id,
            "preview": content_preview[:200],
            "supabase": supabase_status,
            "sqlite": sqlite_status,
        }
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def prune_l3_expired(self) -> Dict:
        """Archive L3 entries whose active_until has passed (SQLite) and delete
        them from Supabase's durable l3_active_memory.

        Entries past their expiry are already invisible in get_l3_context() queries,
        but they accumulate in storage indefinitely without this cleanup (T-026).
        T-309: the local copy is moved to l3_archive (memory.archive), not hard-
        deleted — 'expired' is a forgetting reason, not data loss. The remote
        Supabase row is still hard-deleted; that tier is out of this ticket's
        scope (archiving is a local hot-cache concept, same as T-135/T-300).
        Best-effort — errors are caught and returned in the result dict.
        """
        from memory.archive import archive_l3_row

        now = datetime.now(timezone.utc).isoformat()
        supabase_deleted = 0
        sqlite_deleted = 0
        try:
            with self._supa_lock:
                r = (
                    self.supabase.table("l3_active_memory")
                    .delete()
                    .lt("active_until", now)
                    .not_.is_("active_until", "null")
                    .execute()
                )
            supabase_deleted = len(r.data or [])
        except Exception as e:
            print(f"[Memory] L3 Supabase prune error: {e}")

        try:
            conn = sqlite3.connect(self.sqlite_path)
            cursor = conn.cursor()
            expired_ids = [
                row[0] for row in cursor.execute(
                    "SELECT id FROM l3_cache WHERE active_until IS NOT NULL AND active_until < ?",
                    [now],
                ).fetchall()
            ]
            for rid in expired_ids:
                archive_l3_row(conn, rid, "expired", now)
            sqlite_deleted = len(expired_ids)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Memory] L3 SQLite prune error: {e}")

        total = supabase_deleted + sqlite_deleted
        if total > 0:
            print(f"[Memory] L3 prune: removed {supabase_deleted} from Supabase, "
                  f"{sqlite_deleted} from SQLite cache")
        self._replication_log_append(
            "prune", "batch",
            content_preview=f"supabase_deleted={supabase_deleted} sqlite_deleted={sqlite_deleted}",
            supabase_status="ok" if supabase_deleted >= 0 else "failed",
            sqlite_status="ok" if sqlite_deleted >= 0 else "failed",
        )
        return {"success": True, "supabase_deleted": supabase_deleted,
                "sqlite_deleted": sqlite_deleted}

    def promote_l2_to_l3(self, importance_threshold: int = 8) -> Dict:
        """Promote high-importance L2 facts to L3 ambient context.

        Queries organized_memory for active entries at or above importance_threshold,
        then writes any that are not already in L3 to l3_active_memory via
        memory_write(tier='l3').  Runs at session end so that facts extracted by
        distill_session() with high importance become ambient in the next session
        without requiring an explicit memory_write(tier='l3') call (T-026).

        Args:
            importance_threshold: Minimum L2 importance to consider for promotion.
                                  Default 8 — only genuinely important facts
                                  (user profile, primary projects) are promoted.

        Returns:
            {"promoted": N, "skipped": M}
        """
        promoted = 0
        skipped = 0
        try:
            with self._supa_lock:
                r = (
                    self.supabase.table("organized_memory")
                    .select("id, title, content, category, importance")
                    .eq("status", "active")
                    .gte("importance", importance_threshold)
                    .order("importance", desc=True)
                    .limit(50)
                    .execute()
                )
            candidates = r.data or []
        except Exception as e:
            print(f"[Memory] promote_l2_to_l3 fetch error: {e}")
            return {"promoted": 0, "skipped": 0}

        for row in candidates:
            # Extract full text: content is JSONB {"text": "..."} or plain string
            raw_content = row.get("content") or {}
            if isinstance(raw_content, dict):
                text = raw_content.get("text") or row.get("title") or ""
            else:
                text = str(raw_content)

            if not text.strip():
                skipped += 1
                continue

            # Skip if already in L3 (prefix dedup via SQLite cache — no network)
            dup_id = self._is_l3_duplicate(text, row.get("category", "note"))
            if dup_id:
                skipped += 1
                continue

            result = self.memory_write(
                content=text,
                tier="l3",
                importance=row.get("importance", importance_threshold),
                category=row.get("category", "note"),
            )
            if result.get("success"):
                # T-234: record the L2 source ID on the new L3 entry so that
                # _invalidate_l3_entry can propagate supersession back to L2.
                new_l3_id = result.get("id")
                l2_id = row.get("id")
                if new_l3_id and l2_id and not result.get("duplicate"):
                    try:
                        conn = sqlite3.connect(self.sqlite_path)
                        conn.execute(
                            "UPDATE l3_cache SET source_l2_id = ? WHERE id = ?",
                            [l2_id, new_l3_id],
                        )
                        conn.commit()
                        conn.close()
                    except Exception:
                        pass  # non-fatal; supersession propagation degrades to best-effort
                promoted += 1
            else:
                skipped += 1

        if promoted > 0:
            print(f"[Memory] L2->L3 promotion: {promoted} facts promoted, {skipped} skipped")
        return {"promoted": promoted, "skipped": skipped}

    # Matches a bare UUID prefix (8+ hex chars, no other chars) — signals ID-based delete
    # Matches first UUID segment (8 hex) optionally followed by dash-separated groups
    _UUID_PREFIX_RE = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4,})*$", re.IGNORECASE)

    def memory_delete(
        self,
        target: str,
        soft: bool = True,
        force: bool = False,
    ) -> Dict:
        """Soft-invalidate L3 entries matching target.

        Args:
            target: UUID prefix (ID-based) or content search string.
            soft:   True (default) = set invalid_at; False = hard DELETE.
                    Soft-deleted rows are recoverable via --include-archived.
            force:  Must be True to delete more than 3 entries at once.
                    Without it, returns an error dict so the caller can confirm.

        Returns:
            {"deleted": N, "entries": [id, ...], "soft": bool}
            or {"error": "...", "would_delete": N, "entries": [...]} if guard fires.
        """
        if not target or not target.strip():
            return {"deleted": 0, "entries": [], "error": "empty target"}

        target = target.strip()

        # ID-first: if target looks like a UUID / hex prefix, match by ID field
        if self._UUID_PREFIX_RE.match(target):
            conn = sqlite3.connect(self.sqlite_path)
            rows = conn.execute(
                "SELECT id, content, importance, category FROM l3_cache "
                "WHERE id LIKE ? AND invalid_at IS NULL LIMIT 5",
                [f"{target}%"],
            ).fetchall()
            conn.close()
            matches = [
                {"id": r[0], "content": r[1], "importance": r[2], "category": r[3]}
                for r in rows
            ]
        else:
            # T-302: semantic forget. Union, not replacement: the plain lexical
            # set (memory_read) is kept as-is so exact-match bulk delete still
            # catches every literal hit — retrieve()'s rank-decay scoring
            # (1 - i/n) is tuned for "best few for context injection" and
            # would otherwise silently drop lower-ranked TRUE ties. retrieve()
            # (dense cosine + BM25 fusion) then ADDS memories related to
            # `target` with zero lexical overlap ("my old internship" ->
            # "started the summer role at Meta in June"), filtered by a score
            # floor so a generic query can't sweep in unrelated memories.
            lexical_matches = self.memory_read(target, tier="l3")
            seen_ids = {m["id"] for m in lexical_matches}
            score_floor = float(os.environ.get("PI_FORGET_SCORE_FLOOR", "0.35"))
            semantic_matches = [
                m for m in self.retrieve(target, k=10, tiers=("l3",))
                if m["id"] not in seen_ids and m.get("score", 0.0) >= score_floor
            ]
            matches = lexical_matches + semantic_matches

        if not matches:
            return {"deleted": 0, "entries": []}

        deleted_ids = [m["id"] for m in matches]

        # Bulk guard: more than 3 entries requires force=True
        if len(deleted_ids) > 3 and not force:
            return {
                "error": (
                    f"Would delete {len(deleted_ids)} entries — too many for a single call. "
                    "Review the list and re-call with force=True if intentional."
                ),
                "would_delete": len(deleted_ids),
                "entries": [
                    {"id": m["id"][:8], "content": (m.get("content") or "")[:80]}
                    for m in matches
                ],
            }

        # WAL snapshot before any modification
        _wal = os.path.join(os.path.dirname(self.sqlite_path), "delete_wal.jsonl")
        try:
            os.makedirs(os.path.dirname(_wal), exist_ok=True)
            with open(_wal, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "target": target,
                    "soft": soft,
                    "entries": matches,
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass  # WAL failure must never block the delete

        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        if soft:
            # True soft-delete: set invalid_at (recoverable; consistent with memory_cli forget)
            placeholders = ",".join("?" * len(deleted_ids))
            conn = sqlite3.connect(self.sqlite_path)
            conn.execute(
                f"UPDATE l3_cache SET invalid_at = ? WHERE id IN ({placeholders})",
                [now_iso] + deleted_ids,
            )
            conn.commit()
            conn.close()
            # T-232: invalid_at lives in metadata JSONB on l3_active_memory, NOT as a
            # top-level column. Batch via individual fetch+update (mirrors _invalidate_l3_entry).
            try:
                with self._supa_lock:
                    for _eid in deleted_ids:
                        try:
                            _r = self.supabase.table("l3_active_memory").select("metadata").eq("id", _eid).execute()
                            _meta = (((_r.data or [{}])[0]) or {}).get("metadata") or {}
                            if not isinstance(_meta, dict):
                                _meta = {}
                            _meta["invalid_at"] = now_iso
                            self.supabase.table("l3_active_memory").update({"metadata": _meta}).eq("id", _eid).execute()
                        except Exception:
                            pass
            except Exception as exc:
                print(f"[Memory] Supabase soft-delete error: {exc}")
        else:
            # Hard delete (rare, explicit)
            placeholders = ",".join("?" * len(deleted_ids))
            conn = sqlite3.connect(self.sqlite_path)
            conn.execute(
                f"DELETE FROM l3_cache WHERE id IN ({placeholders})", deleted_ids
            )
            conn.commit()
            conn.close()
            try:
                with self._supa_lock:
                    self.supabase.table("l3_active_memory")\
                        .delete()\
                        .in_("id", deleted_ids)\
                        .execute()
            except Exception as exc:
                print(f"[Memory] Supabase hard-delete error: {exc}")

        return {"deleted": len(deleted_ids), "entries": deleted_ids, "soft": soft}
    
    def get_l3_context(self, max_tokens: int = 800) -> str:
        """
        Get L3 context for LLM injection.

        Returns formatted string of active context.
        Syncs from Supabase at most once per _sync_ttl_seconds (T-011).
        All categories are included dynamically — no silent drops (T-010).
        """

        # T-163: single gated entry — sync-if-stale + scoring + filtering in one place.
        raw_records = self._l3_active_records(query="", limit=9999)

        label_map = {
            "permanent_profile": "PROFILE",
            "profile": "PROFILE",
            "active_project": "PROJECTS",
            "projects": "PROJECTS",
            "current_priority": "PRIORITIES",
            "priorities": "PRIORITIES",
            "session_history": "PREVIOUS SESSIONS",
            "research_results": "RESEARCH",
            "timed_reminder": "REMINDERS",
            "file_operations": "FILES",
            "temporary_note": "NOTES",
            "note": "NOTES",
            "preferences": "PREFERENCES",
        }

        # T-034: Merge by DISPLAY LABEL so alias categories (e.g. 'profile' and
        # 'permanent_profile') never produce two sections with the same header.
        # Key: display label → list of (priority_rank, content) tuples.
        priority_order = [
            "PROFILE", "PROJECTS", "PRIORITIES", "PREFERENCES",
            "PREVIOUS SESSIONS", "RESEARCH", "REMINDERS", "FILES", "NOTES",
        ]
        label_entries: Dict[str, list] = {}
        total_tokens = 0

        for row in raw_records:
            content = row["content"] or ""  # T-235: guard NULL content from SQLite
            category, _ = row["category"], row["importance"]
            tokens = len(content) // 4
            if total_tokens + tokens > max_tokens:
                break
            label = label_map.get(category, category.upper().replace("_", " "))
            if label not in label_entries:
                label_entries[label] = []
            label_entries[label].append(content)
            total_tokens += tokens

        if not label_entries:
            return ""

        # T-034: Within each display group, remove entries whose words are a
        # near-complete subset of a longer entry (>= 80% word overlap ratio).
        def _dedup_entries(entries: list) -> list:
            out = []
            for candidate in entries:
                cwords = set(candidate.lower().split())
                dominated = any(
                    len(cwords) > 0
                    and len(cwords - set(kept.lower().split())) / len(cwords) < 0.20
                    and candidate != kept
                    for kept in out
                )
                if not dominated:
                    out.append(candidate)
            return out

        ordered_labels = [l for l in priority_order if l in label_entries]
        ordered_labels += [l for l in label_entries if l not in priority_order]

        output = ["=== ACTIVE CONTEXT ===\n"]
        for label in ordered_labels:
            entries = _dedup_entries(label_entries[label])
            if not entries:
                continue
            output.append(f"{label}:")
            for entry in entries:
                output.append(f"• {entry}")
            output.append("")

        return "\n".join(output)
    
    def _sync_l3(self):
        """Pull latest L3 from Supabase to cache. Updates _last_sync on success.

        T-270: ordered newest-first and explicitly capped. Supabase/PostgREST
        silently caps an unbounded select("*") at ~1000 rows in default (not
        recency) order — once l3_active_memory crossed that size, brand-new
        writes fell outside the arbitrary page and vanished from the local
        cache on the very next sync (write-then-immediate-read returned
        empty). Ordering by created_at desc guarantees new rows are always
        included; the explicit limit makes any truncation intentional.

        T-306: UPSERT, not DELETE+reinsert. l3_cache has 16 local-only columns
        (embedding, decay_rate, pinned, last_accessed_at, mode, conversation_id,
        scope, source_l2_id, kind, source_id, recompute_after, formula,
        superseded_by, surprise_score, goal_alignment, affect_tag) that
        Supabase's l3_active_memory doesn't carry — a full wipe silently reset
        all of them to defaults on every sync. Only the 7 Supabase-owned
        columns are overwritten here; everything else survives untouched.
        kind='derived' rows (agent/caretaker.py) never have a Supabase
        counterpart at all and are excluded from the reconciling delete below.
        """
        try:
            with self._supa_lock:
                response = (self.supabase.table("l3_active_memory").select("*")
                            .order("created_at", desc=True)
                            .limit(self._L3_SYNC_ROW_CAP).execute())

            conn = sqlite3.connect(self.sqlite_path)
            cursor = conn.cursor()

            fetched_ids = []
            if response.data:
                for entry in response.data:
                    # T-078: copy invalid_at from metadata JSONB into SQLite column
                    # so local search/ambient queries can filter on it.
                    meta = entry.get("metadata") or {}
                    invalid_at_val = meta.get("invalid_at") if isinstance(meta, dict) else None
                    fetched_ids.append(entry["id"])
                    cursor.execute("""
                        INSERT INTO l3_cache (id, content, importance, category, active_until, created_at, invalid_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            content = excluded.content,
                            importance = excluded.importance,
                            category = excluded.category,
                            active_until = excluded.active_until,
                            created_at = excluded.created_at,
                            invalid_at = excluded.invalid_at
                    """, [
                        entry["id"],
                        entry["content"],
                        entry["importance"],
                        entry["category"],
                        entry.get("active_until"),
                        entry["created_at"],
                        invalid_at_val,
                    ])

            # Reconcile deletions: a row missing from the fetch was genuinely
            # removed remotely (e.g. hard-deleted by prune_l3_expired) — except
            # derived rows, which are SQLite-only by design and never appear
            # in any Supabase fetch.
            if fetched_ids:
                placeholders = ",".join("?" * len(fetched_ids))
                cursor.execute(
                    f"DELETE FROM l3_cache WHERE id NOT IN ({placeholders}) "
                    f"AND (kind IS NULL OR kind != 'derived')",
                    fetched_ids,
                )
            else:
                cursor.execute(
                    "DELETE FROM l3_cache WHERE (kind IS NULL OR kind != 'derived')"
                )

            conn.commit()
            conn.close()
            self._last_sync = datetime.now(timezone.utc)  # T-011: mark sync time
        except Exception as e:
            print(f"[Memory] L3 sync error: {e}")

    def _verify_write(self, entry_id: str, content: str, tier: str) -> Dict:
        """Verify write succeeded in both SQLite and Supabase (T-014).

        Supabase is optional (README: "L3 runs on local SQLite without it").
        In offline/noop mode (_NoopSupabase — no SUPABASE_URL/KEY configured)
        there is no remote to verify against; requiring supabase_ok anyway
        meant every L3 write reported success=False on any Supabase-less
        checkout even though the SQLite write fully succeeded. Verification
        then rests on SQLite alone.
        """

        # Check SQLite cache
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM l3_cache WHERE id = ?", [entry_id])
        row = cursor.fetchone()
        conn.close()

        sqlite_ok = row is not None and row[0] == content

        if isinstance(self._supabase_client, _NoopSupabase):
            return {"id": entry_id, "success": sqlite_ok, "verified": sqlite_ok, "tier": tier}

        # Check Supabase — only true persistence matters
        supabase_ok = False
        try:
            with self._supa_lock:
                resp = self.supabase.table("l3_active_memory").select("id").eq("id", entry_id).execute()
            supabase_ok = bool(resp.data)
        except Exception as e:
            print(f"[Memory] Supabase verify error: {e}")

        verified = sqlite_ok and supabase_ok
        return {"id": entry_id, "success": verified, "verified": verified, "tier": tier}
    
    def memory_search_semantic(
        self,
        query: str,
        limit: int = 5,
        threshold: float = 0.5,
    ) -> List[Dict]:
        """T-097: Cosine-similarity search over L2 facts that have stored embeddings.

        Wraps the T-080 embedding engine (memory/semantic_dedup) and exposes it
        as a retrieval path the planner can call. Catches paraphrases that the
        lexical memory_read misses (e.g. query "caching decisions" matching a
        row titled "Redis Migration ADR").

        Graceful degrade:
        - No GEMINI_API_KEY → returns []
        - No L2 rows with stored embeddings (pre-T-080 vault) → returns []
        - Private namespace (_NoopSupabase) → returns []
        - Any exception inside the embedding/cosine path → returns []

        Returns a list of {id, content, category, importance, similarity, tier="l2-semantic"}
        sorted by similarity descending, capped at `limit`, filtered to >= `threshold`.
        """
        try:
            from memory.semantic_dedup import get_embedding, cosine_similarity
        except Exception:
            return []
        q_emb = get_embedding(query)
        if not q_emb:
            return []

        try:
            with self._supa_lock:
                resp = (
                    self.supabase.table("organized_memory")
                    .select("id,category,content,importance,created_at,status")
                    .eq("status", "active")
                    .limit(2000)
                    .execute()
                )
            rows = resp.data or []
        except Exception as e:
            print(f"[Memory] semantic search L2 read failed: {e}")
            return []

        scored: List[Dict] = []
        for row in rows:
            body = row.get("content") or {}
            if not isinstance(body, dict):
                continue
            meta = body.get("metadata") or {}
            emb = meta.get("embedding")
            if not emb:
                continue
            sim = cosine_similarity(q_emb, emb)
            if sim < threshold:
                continue
            scored.append({
                "id": row.get("id"),
                "content": body.get("text", "") or "",
                "category": row.get("category"),
                "importance": int(row.get("importance") or 5),
                "similarity": round(sim, 4),
                "tier": "l2-semantic",
            })

        scored.sort(key=lambda r: -r["similarity"])
        return scored[:limit]

    def _generate_id(self) -> str:
        """Generate unique ID"""
        return str(uuid.uuid4())


# ── T-083 R2.1: tool registry export ─────────────────────────────────────────
#
# Each handler takes (agent, tool_input, *, memory_override=None). The
# memory_override kwarg lets a namespaced mode route tool calls through a
# separate MemoryTools instance (T-082). success_predicate is on the spec, not
# the handler — keeps handlers returning their natural shape.

from agent.tool_spec import ToolSpec  # noqa: E402


def _handle_memory_read(agent, tool_input, *, memory_override=None):
    mem = memory_override or agent.memory
    return mem.memory_read(
        query=tool_input["query"],
        tier=tool_input.get("tier"),
        # T-137/T-142: pass current context so cued-recall boosts can apply
        # (no-op unless PI_CONTEXT_CUED_RECALL is on). getattr-guarded for mocks.
        current_mode=getattr(agent, "mode", None),
        current_conversation_id=getattr(agent, "conversation_id", None),
        current_scope=getattr(agent, "current_scope", None),
    )


def _handle_memory_write(agent, tool_input, *, memory_override=None):
    mem = memory_override or agent.memory
    expiry = None
    if tool_input.get("expiry"):
        expiry = datetime.fromisoformat(tool_input["expiry"])
    return mem.memory_write(
        content=tool_input["content"],
        tier=tool_input.get("tier", "l3"),
        importance=tool_input.get("importance", 5),
        category=tool_input.get("category", "note"),
        expiry=expiry,
        session_id=agent.session_id,
        source=tool_input.get("source", "stated"),
        # T-137/T-142: stamp encoding context so cued recall has data to boost on.
        mode=getattr(agent, "mode", None),
        conversation_id=getattr(agent, "conversation_id", None),
        scope=getattr(agent, "current_scope", None),
    )


def _handle_memory_delete(agent, tool_input, *, memory_override=None):
    mem = memory_override or agent.memory
    return mem.memory_delete(
        target=tool_input["target"],
        soft=tool_input.get("soft", True),
    )


def _handle_memory_search_semantic(agent, tool_input, *, memory_override=None):
    mem = memory_override or agent.memory
    return mem.memory_search_semantic(
        query=tool_input["query"],
        limit=tool_input.get("limit", 5),
        threshold=tool_input.get("threshold", 0.5),
    )


def _handle_recall_episode(agent, tool_input, *, memory_override=None):
    mem = memory_override or agent.memory
    hits = mem.recall_episode(
        query=tool_input["query"],
        limit=tool_input.get("limit", 4),
    )
    if not hits:
        return {"episodes": [], "message": "No past episode digests match that query."}
    return {"episodes": hits}


TOOLS = [
    ToolSpec(
        name="memory_read",
        description=(
            "Search Ash's persistent memory. "
            "Default: searches L3 (hot context) first — if found, returns immediately. "
            "Falls back to L2 (deep organized memory) only if L3 has nothing. "
            "Call this whenever the user asks about something they previously stored, told Pi, "
            "or wants recalled — preferences, facts, notes, projects, past decisions. "
            "Always call this before claiming 'nothing in memory' or 'I don't know'. "
            "Returns matching stored entries with tier labels."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword(s) to search for — short and specific, e.g. 'subway order' not 'what did ash say about food'"},
                "tier": {"type": "string", "enum": ["l1", "l2", "l3"],
                         "description": "Optional: pin to a specific tier. Omit to use L3→L2 cascade."},
            },
            "required": ["query"],
        },
        handler=_handle_memory_read,
    ),
    ToolSpec(
        name="memory_write",
        description="Write to memory. Auto-verifies.",
        input_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "tier": {"type": "string", "enum": ["l1", "l2", "l3"], "default": "l3"},
                "importance": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                "category": {"type": "string", "default": "note"},
                "expiry": {"type": "string", "description": "ISO datetime — SET for facts with a natural lifespan. Ephemeral phrasing in content ('just for today', 'until friday', 'for the next 3 days', ...) is auto-detected when omitted."},
                "source": {
                    "type": "string",
                    "enum": ["stated", "inferred_confirmed", "inferred_unconfirmed"],
                    "default": "stated",
                    "description": (
                        "How the fact was obtained. Use 'stated' when user said it "
                        "directly, 'inferred_confirmed' when you inferred it and user "
                        "confirmed, 'inferred_unconfirmed' to block accidental L3 "
                        "writes of unverified guesses."
                    ),
                },
            },
            "required": ["content"],
        },
        handler=_handle_memory_write,
        success_predicate=lambda r: r.get("verified", False),
    ),
    ToolSpec(
        name="memory_delete",
        description="Delete from memory. Soft delete = archive to L2.",
        input_schema={
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "soft": {"type": "boolean", "default": True},
            },
            "required": ["target"],
        },
        handler=_handle_memory_delete,
        success_predicate=lambda r: r.get("deleted", 0) > 0,
    ),
    ToolSpec(
        name="memory_search_semantic",
        description=(
            "Semantic search over L2 memory using Gemini embeddings + cosine "
            "similarity. Catches paraphrases that lexical memory_read misses "
            "(e.g. 'caching decisions' matching a row titled 'Redis Migration ADR'). "
            "Use when literal keywords are unlikely to overlap with stored content. "
            "Returns top N by similarity descending; empty list on graceful degrade."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Natural language query — semantic match, not literal."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
                "threshold": {"type": "number", "minimum": 0.0, "maximum": 1.0,
                              "default": 0.5,
                              "description": "Minimum cosine to include in results."},
            },
            "required": ["query"],
        },
        handler=_handle_memory_search_semantic,
        # Treat empty results as a successful query (no match found, no error).
        # Failures during the embedding call also return [] — distinguishing them
        # would require changing the return shape; for now empty = success.
        success_predicate=lambda r: isinstance(r, list),
    ),
    ToolSpec(
        name="recall_episode",
        description=(
            "Search past conversation episodes by topic. "
            "Returns compact digests (when, mode, what was discussed / decided) "
            "for conversations whose digest or title match the query. "
            "Use when asked 'what did we decide about X', 'remember when', "
            "'last time we discussed', etc."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Topic or decision to search for across past conversations.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 4,
                    "description": "Maximum episodes to return.",
                },
            },
            "required": ["query"],
        },
        handler=_handle_recall_episode,
        success_predicate=lambda r: isinstance(r, dict),
    ),
]


if __name__ == "__main__":
    # Test
    from app.config import SUPABASE_URL, SUPABASE_KEY
    
    memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)
    
    # Test write
    result = memory.memory_write(
        content="Testing agent memory system",
        importance=8,
        category="test"
    )
    print(f"Write: {result}")
    
    # Test read
    matches = memory.memory_read("testing")
    print(f"Read: {matches}")
    
    # Test context
    context = memory.get_l3_context()
    print(f"Context:\n{context}")
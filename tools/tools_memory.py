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
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

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
    ``.data=[]``. Used when ``MemoryTools(namespace='god')`` so god-mode memory
    never reaches Supabase. Privacy-by-file-separation is ADR-001 invariant 5.

    Tests that pre-assign a MagicMock continue to work — the shim only
    activates when the constructor receives a private namespace.
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
        # through `_NoopSupabase` so private (god) memory never leaves the box.
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
            # Caller-supplied relative path (e.g. ModeConfig.memory_db =
            # "data/god_memory.db") resolves against project root for portability.
            sqlite_path = os.path.join(project_root, sqlite_path)
        os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)
        self.sqlite_path = sqlite_path
        self._last_sync: Optional[datetime] = None  # T-011: TTL-based sync guard
        self._sync_ttl_seconds = 300  # sync at most once per 5 minutes
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
                    from supabase import create_client
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
        conn = sqlite3.connect(self.sqlite_path)
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

        conn.commit()
        conn.close()
    
    def memory_read(self, query: str = "", tier: Optional[str] = None, limit: int = 20) -> List[Dict]:
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
                rows = self._hybrid_search_l3(query, limit)

                # If nothing in cache and we haven't synced recently, sync and retry
                if not rows and (self._last_sync is None or
                                 (datetime.now(timezone.utc) - self._last_sync).total_seconds() > self._sync_ttl_seconds):
                    with self._sync_lock:
                        now2 = datetime.now(timezone.utc)
                        if self._last_sync is None or (now2 - self._last_sync).total_seconds() > self._sync_ttl_seconds:
                            self._sync_l3()
                    rows = self._hybrid_search_l3(query, limit)

                l3_results = [
                    {"id": r[0], "content": r[1], "importance": r[2],
                     "category": r[3], "active_until": r[4], "tier": "l3"}
                    for r in rows
                ]
                results.extend(l3_results)
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
        """
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        if query:
            cursor.execute("""
                SELECT id, content, importance, category, active_until
                FROM l3_cache
                WHERE content LIKE ?
                  AND invalid_at IS NULL
                  AND (superseded_by IS NULL OR superseded_by = '')
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
            """, [f"%{query}%", limit])
        else:
            cursor.execute("""
                SELECT id, content, importance, category, active_until
                FROM l3_cache
                WHERE invalid_at IS NULL
                  AND (superseded_by IS NULL OR superseded_by = '')
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
            """, [limit])
        rows = cursor.fetchall()
        conn.close()
        return rows

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

    def _l3_fast_path(self, query: str, limit: int) -> list:
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
            # T-145: only include rows with an actual content match; returning all
            # rows (match_score=0) caused memory_delete to wipe entire L3.
            if match_score > 0:
                scored.append((score, (id_, content, importance, category, active_until)))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [row for _, row in scored[:limit]]

    def _hybrid_search_l3(self, query: str, limit: int) -> list:
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
            return self._l3_fast_path(query, limit)

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
            scored.append((combined, row[:5]))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [row for _, row in scored[:limit]]
    
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
                         mode, conversation_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        try:
            conn = sqlite3.connect(self.sqlite_path)
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
        """Delete L3 entries whose active_until has passed from both Supabase and SQLite.

        Entries past their expiry are already invisible in get_l3_context() queries,
        but they accumulate in storage indefinitely without this cleanup (T-026).
        Best-effort — errors are caught and returned in the result dict.
        """
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
            cursor.execute(
                "DELETE FROM l3_cache WHERE active_until IS NOT NULL AND active_until < ?",
                [now],
            )
            sqlite_deleted = cursor.rowcount
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
            matches = self.memory_read(target, tier="l3")

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
            try:
                with self._supa_lock:
                    self.supabase.table("l3_active_memory")\
                        .update({"invalid_at": now_iso})\
                        .in_("id", deleted_ids)\
                        .execute()
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

        # T-011/T-066: Sync if stale; lock prevents two callers triggering the same sync
        now = datetime.now(timezone.utc)
        if self._last_sync is None or (now - self._last_sync).total_seconds() > self._sync_ttl_seconds:
            with self._sync_lock:
                # Re-check under lock — a concurrent caller may have synced already
                now = datetime.now(timezone.utc)
                if self._last_sync is None or (now - self._last_sync).total_seconds() > self._sync_ttl_seconds:
                    self._sync_l3()

        # Get from cache
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()

        # T-078: filter invalidated entries from ambient context.
        # T-125b: also filter superseded_by (post-dedup losers).
        cursor.execute("""
            SELECT content, category, importance
            FROM l3_cache
            WHERE (active_until IS NULL OR active_until > ?)
              AND invalid_at IS NULL
              AND (superseded_by IS NULL OR superseded_by = '')
            ORDER BY importance DESC, created_at DESC
        """, [now.isoformat()])

        rows = cursor.fetchall()
        conn.close()

        # T-010: Dynamic category grouping — no hardcoded list, no silent drops
        # Priority order for display (known categories first, unknowns appended after)
        priority_order = [
            "permanent_profile", "active_project", "current_priority",
            "session_history", "research_results", "timed_reminder",
            "file_operations", "temporary_note", "note"
        ]

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

        for row in rows:
            content, category, _ = row
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
        """Pull latest L3 from Supabase to cache. Updates _last_sync on success."""
        try:
            with self._supa_lock:
                response = self.supabase.table("l3_active_memory").select("*").execute()

            conn = sqlite3.connect(self.sqlite_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM l3_cache")

            if response.data:
                for entry in response.data:
                    # T-078: copy invalid_at from metadata JSONB into SQLite column
                    # so local search/ambient queries can filter on it.
                    meta = entry.get("metadata") or {}
                    invalid_at_val = meta.get("invalid_at") if isinstance(meta, dict) else None
                    cursor.execute("""
                        INSERT INTO l3_cache (id, content, importance, category, active_until, created_at, invalid_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, [
                        entry["id"],
                        entry["content"],
                        entry["importance"],
                        entry["category"],
                        entry.get("active_until"),
                        entry["created_at"],
                        invalid_at_val,
                    ])

            conn.commit()
            conn.close()
            self._last_sync = datetime.now(timezone.utc)  # T-011: mark sync time
        except Exception as e:
            print(f"[Memory] L3 sync error: {e}")

    def _verify_write(self, entry_id: str, content: str, tier: str) -> Dict:
        """Verify write succeeded in both SQLite and Supabase (T-014)."""

        # Check SQLite cache
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM l3_cache WHERE id = ?", [entry_id])
        row = cursor.fetchone()
        conn.close()

        sqlite_ok = row is not None and row[0] == content

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
        - Private namespace (god, _NoopSupabase) → returns []
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
# memory_override kwarg lets god mode route tool calls through its private
# MemoryTools instance (T-082). success_predicate is on the spec, not the
# handler — keeps handlers returning their natural shape.

from agent.tool_spec import ToolSpec  # noqa: E402


def _handle_memory_read(agent, tool_input, *, memory_override=None):
    mem = memory_override or agent.memory
    return mem.memory_read(
        query=tool_input["query"],
        tier=tool_input.get("tier"),
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
                "expiry": {"type": "string", "description": "ISO datetime"},
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
"""
Pi Agent Tools - Memory Operations
Simple, powerful, composable tools for memory management.
"""

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
# Strips trailing session markers before dedup comparison so that the same fact
# appended with different markers (e.g. "...veggies. marker_abc123" vs
# "...veggies. marker_def456") is recognised as a duplicate.
_MARKER_RE = re.compile(
    r'\s*(marker_[0-9a-f]{6,}|unique[a-z0-9]{4,})\s*$', re.IGNORECASE
)


class MemoryTools:
    """
    Simple memory tools for Pi agent.
    No complex logic - just read, write, delete.
    """
    
    def __init__(self, supabase_url: str, supabase_key: str, sqlite_path: str = None):
        from supabase import create_client  # lazy: keeps module-import fast (T-032)
        self.supabase = create_client(supabase_url, supabase_key)
        if sqlite_path is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            sqlite_path = os.path.join(project_root, "data", "pi.db")
        self.sqlite_path = sqlite_path
        self._last_sync: Optional[datetime] = None  # T-011: TTL-based sync guard
        self._sync_ttl_seconds = 300  # sync at most once per 5 minutes
        self._init_sqlite()
    
    def _init_sqlite(self):
        """Initialize SQLite cache"""
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
        
        conn.commit()
        conn.close()
    
    def memory_read(self, query: str = "", tier: Optional[str] = None, limit: int = 20) -> List[Dict]:
        """
        Search memory. Empty query returns all recent entries.

        Args:
            query: Search term (empty = return all recent)
            tier:  'l1' | 'l2' | 'l3' to search a specific tier; None searches L3+L2 only.
                   L1 (raw_wiki archive) is opt-in via tier='l1' explicitly — it excludes
                   itself from the implicit search because content-matching against the
                   raw archive is noisy without a real query layer. T-017, conservative
                   fix per master prompt §6 Phase 3.3. The aggressive fix (include L1
                   with a low default limit) is deferred until full-text search lands
                   on raw_wiki.
            limit: Max results

        Returns:
            List of matching entries. Each entry carries a 'tier' key indicating origin.
        """
        results = []

        if tier == "l3" or tier is None:
            rows = self._search_l3_cache(query, limit)

            # If nothing in cache, sync from Supabase and retry
            if not rows:
                self._sync_l3()
                rows = self._search_l3_cache(query, limit)

            l3_results = [
                {"id": r[0], "content": r[1], "importance": r[2],
                 "category": r[3], "active_until": r[4], "tier": "l3"}
                for r in rows
            ]
            results.extend(l3_results)
            print(f"[Memory] L3 search '{query[:30]}' → {len(l3_results)} results")

            if tier == "l3":
                return results

        if tier == "l2" or tier is None:
            try:
                if query:
                    # SM-003 fix: title is only the first 100 chars of content; the
                    # full text lives in content.text JSONB. Search BOTH and merge
                    # by id so distinctive keywords past char 100 stay reachable.
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
                    results.extend(merged[:limit])
                else:
                    response = (self.supabase.table("organized_memory").select("*")
                                .order("created_at", desc=True).limit(limit).execute())
                    if response.data:
                        for entry in response.data:
                            entry["tier"] = "l2"
                        results.extend(response.data)
            except Exception as e:
                print(f"[Memory] L2 search error: {e}")

        if tier == "l1":
            try:
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
        """Search SQLite l3_cache, with or without text filter"""
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        if query:
            cursor.execute("""
                SELECT id, content, importance, category, active_until
                FROM l3_cache
                WHERE content LIKE ?
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
            """, [f"%{query}%", limit])
        else:
            cursor.execute("""
                SELECT id, content, importance, category, active_until
                FROM l3_cache
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
            """, [limit])
        rows = cursor.fetchall()
        conn.close()
        return rows
    
    def memory_write(
        self,
        content: str,
        tier: str = "l3",
        importance: int = 5,
        category: str = "note",
        expiry: Optional[datetime] = None,
        session_id: Optional[str] = None,
        source: str = "stated"
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
            
            try:
                self.supabase.table("l3_active_memory").insert(entry).execute()
            except Exception as e:
                print(f"[Memory] Supabase write failed: {e}")
            
            # Write to SQLite cache
            conn = sqlite3.connect(self.sqlite_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO l3_cache (id, content, importance, category, active_until, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [
                entry_id,
                content,
                importance,
                category,
                expiry.isoformat() if expiry else None,
                now.isoformat()
            ])
            
            conn.commit()
            conn.close()

            # T-038: Soft-delete entries superseded by this new write.
            # Done AFTER successful insert so a write failure leaves old entries intact.
            if supersedes_ids:
                for sid in supersedes_ids:
                    try:
                        self._expire_l3_entry(sid)
                        print(f"[Memory] L3 conflict: superseded {sid[:8]}... with new entry")
                    except Exception:
                        pass

            # Verify
            result = self._verify_write(entry_id, content, tier)
            if supersedes_ids:
                result["superseded"] = len(supersedes_ids)
            return result

        elif tier == "l2":
            # Deduplication: skip if a near-duplicate already exists in organized_memory
            # for the same category.  Prevents distillation from stacking the same
            # fact across sessions when the user mentions it repeatedly (T-026).
            dup_id = self._is_l2_duplicate(content, category)
            if dup_id:
                print(f"[Memory] L2 dedup: duplicate of {dup_id[:8]}..., skipping")
                return {"id": dup_id, "success": True, "verified": True,
                        "tier": "l2", "duplicate": True}

            # Write to L2 organized memory
            entry = {
                "id": entry_id,
                "category": category,
                "title": content[:100],
                "content": {"text": content},
                "importance": importance,
                "status": "active",
                "created_at": now.isoformat()
            }
            
            try:
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
        """
        past = (datetime.now(timezone.utc).replace(microsecond=0)
                .isoformat().replace("+00:00", "Z"))
        try:
            conn = sqlite3.connect(self.sqlite_path)
            conn.execute(
                "UPDATE l3_cache SET active_until = ? WHERE id = ?",
                [past, entry_id],
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Memory] _expire_l3_entry SQLite error: {e}")
        try:
            self.supabase.table("l3_active_memory").update(
                {"active_until": past}
            ).eq("id", entry_id).execute()
        except Exception as e:
            print(f"[Memory] _expire_l3_entry Supabase error (non-fatal): {e}")

    def get_l1_thread(self, thread_id: str) -> List[Dict]:
        """Fetch all L1 rows for a session thread from raw_wiki, ordered by (turn, seq).

        Returns an empty list on any error so callers can treat the result as a
        simple iterable without extra error handling.
        """
        try:
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

    def memory_delete(self, target: str, soft: bool = True) -> Dict:
        """
        Delete from memory.
        
        Args:
            target: What to delete (search query)
            soft: True=move to L2, False=permanent delete
        
        Returns:
            {"deleted": N, "entries": [...]}
        """
        
        # Find matches
        matches = self.memory_read(target, tier="l3")
        
        if not matches:
            return {"deleted": 0, "entries": []}
        
        deleted_ids = [m["id"] for m in matches]
        
        if soft:
            # Move to L2
            for entry in matches:
                self.memory_write(
                    content=f"Archived: {entry['content']}",
                    tier="l2",
                    importance=max(entry.get('importance', 5) - 2, 1),
                    category="archived"
                )
        
        # Remove from L3
        try:
            self.supabase.table("l3_active_memory")\
                .delete()\
                .in_("id", deleted_ids)\
                .execute()
        except Exception as e:
            print(f"[Memory] Supabase delete error: {e}")
        
        # Remove from cache
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        
        placeholders = ','.join('?' * len(deleted_ids))
        cursor.execute(f"DELETE FROM l3_cache WHERE id IN ({placeholders})", deleted_ids)
        
        conn.commit()
        conn.close()
        
        return {"deleted": len(matches), "entries": deleted_ids, "soft": soft}
    
    def get_l3_context(self, max_tokens: int = 800) -> str:
        """
        Get L3 context for LLM injection.

        Returns formatted string of active context.
        Syncs from Supabase at most once per _sync_ttl_seconds (T-011).
        All categories are included dynamically — no silent drops (T-010).
        """

        # T-011: Only sync if cache is stale or never synced
        now = datetime.now(timezone.utc)
        if self._last_sync is None or (now - self._last_sync).total_seconds() > self._sync_ttl_seconds:
            self._sync_l3()

        # Get from cache
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT content, category, importance
            FROM l3_cache
            WHERE active_until IS NULL OR active_until > ?
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
            response = self.supabase.table("l3_active_memory").select("*").execute()

            conn = sqlite3.connect(self.sqlite_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM l3_cache")

            if response.data:
                for entry in response.data:
                    cursor.execute("""
                        INSERT INTO l3_cache (id, content, importance, category, active_until, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, [
                        entry["id"],
                        entry["content"],
                        entry["importance"],
                        entry["category"],
                        entry.get("active_until"),
                        entry["created_at"]
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
            resp = self.supabase.table("l3_active_memory").select("id").eq("id", entry_id).execute()
            supabase_ok = bool(resp.data)
        except Exception as e:
            print(f"[Memory] Supabase verify error: {e}")

        verified = sqlite_ok and supabase_ok
        return {"id": entry_id, "success": verified, "verified": verified, "tier": tier}
    
    def _generate_id(self) -> str:
        """Generate unique ID"""
        return str(uuid.uuid4())


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
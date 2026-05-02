"""
Pi Agent Tools - Memory Operations
Simple, powerful, composable tools for memory management.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from supabase import create_client


class MemoryTools:
    """
    Simple memory tools for Pi agent.
    No complex logic - just read, write, delete.
    """
    
    def __init__(self, supabase_url: str, supabase_key: str, sqlite_path: str = None):
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
        session_id: Optional[str] = None
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
            
            # Verify
            return self._verify_write(entry_id, content, tier)
        
        elif tier == "l2":
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
                return {"id": entry_id, "success": True, "tier": "l2"}
            except Exception as e:
                return {"id": entry_id, "success": False, "error": str(e)}

        elif tier == "l1":
            # Write to L1 raw archive (raw_wiki table)
            import uuid
            # T-013: use session_id as thread_id for coherent session threading
            thread = session_id if session_id else str(uuid.uuid4())
            entry = {
                "id": entry_id,
                "timestamp": now.isoformat(),
                "thread_id": thread,
                "role": category if category in ("user", "assistant", "system", "tool") else "system",
                "content": content,
                "metadata": {"importance": importance, "category": category},
                "created_at": now.isoformat()
            }
            try:
                self.supabase.table("raw_wiki").insert(entry).execute()
                return {"id": entry_id, "success": True, "tier": "l1"}
            except Exception as e:
                return {"id": entry_id, "success": False, "error": str(e)}

        return {"id": entry_id, "success": False, "error": f"Unknown tier: {tier}"}
    
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

        sections: Dict[str, list] = {}
        total_tokens = 0

        for row in rows:
            content, category, _ = row
            tokens = len(content) // 4
            if total_tokens + tokens > max_tokens:
                break
            if category not in sections:
                sections[category] = []
            sections[category].append(content)
            total_tokens += tokens

        if not sections:
            return ""

        # Format: known categories in priority order, then any remaining
        ordered_keys = [k for k in priority_order if k in sections]
        ordered_keys += [k for k in sections if k not in priority_order]

        output = ["=== ACTIVE CONTEXT ===\n"]
        label_map = {
            "permanent_profile": "PROFILE",
            "active_project": "PROJECTS",
            "current_priority": "PRIORITIES",
            "session_history": "PREVIOUS SESSIONS",
            "research_results": "RESEARCH",
            "timed_reminder": "REMINDERS",
            "file_operations": "FILES",
            "temporary_note": "NOTES",
            "note": "NOTES",
        }

        for key in ordered_keys:
            label = label_map.get(key, key.upper().replace("_", " "))
            entries = sections[key]
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
        import uuid
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
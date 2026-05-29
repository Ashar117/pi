"""core/knowledge_graph.py — L4 Knowledge Graph layer over Pi's memory.

Stores entities and directed relationships in SQLite (data/kg.db).
NetworkX is used for in-memory graph queries; the SQLite store is the
source of truth so the graph survives restarts.

Schema:
  kg_entities  — (id, name, kind, metadata_json, created_at)
  kg_edges     — (id, src_id, relation, dst_id, weight, created_at)

Tools exposed (wired in agent/tools.py):
  kg_add_triple  — assert entity1 -[relation]-> entity2
  kg_query       — find entities related to a name; optional depth
  kg_search      — full-text search over entity names
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

try:
    import networkx as nx
    _NX_AVAILABLE = True
except ImportError:
    nx = None  # type: ignore[assignment]
    _NX_AVAILABLE = False

_DB_PATH = Path(__file__).parent.parent / "data" / "kg.db"


def _db(path: Path = _DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(path: Path = _DB_PATH) -> None:
    with _db(path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS kg_entities (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                kind        TEXT DEFAULT 'concept',
                metadata    TEXT DEFAULT '{}',
                created_at  TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_kg_ent_name ON kg_entities(name COLLATE NOCASE);

            CREATE TABLE IF NOT EXISTS kg_edges (
                id          TEXT PRIMARY KEY,
                src_id      TEXT NOT NULL REFERENCES kg_entities(id),
                relation    TEXT NOT NULL,
                dst_id      TEXT NOT NULL REFERENCES kg_entities(id),
                weight      REAL DEFAULT 1.0,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kg_edge_src ON kg_edges(src_id);
            CREATE INDEX IF NOT EXISTS idx_kg_edge_dst ON kg_edges(dst_id);
        """)


class KnowledgeGraph:
    """SQLite-backed knowledge graph with optional NetworkX traversal."""

    def __init__(self, db_path: Path = _DB_PATH):
        self._path = db_path
        _init_db(db_path)
        self._graph: Optional[Any] = None  # loaded lazily

    # ── Entity helpers ─────────────────────────────────────────────────────

    def _get_or_create_entity(self, name: str, kind: str = "concept") -> str:
        """Return existing entity id or create a new one. Case-insensitive lookup."""
        name = name.strip()
        with _db(self._path) as conn:
            row = conn.execute(
                "SELECT id FROM kg_entities WHERE name=? COLLATE NOCASE", [name]
            ).fetchone()
            if row:
                return row[0]
            eid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO kg_entities (id,name,kind,metadata,created_at) VALUES (?,?,?,?,?)",
                [eid, name, kind, "{}", datetime.now(timezone.utc).isoformat()],
            )
            return eid

    # ── Core API ───────────────────────────────────────────────────────────

    def add_triple(
        self,
        entity1: str,
        relation: str,
        entity2: str,
        kind1: str = "concept",
        kind2: str = "concept",
        weight: float = 1.0,
    ) -> Dict:
        """Assert entity1 -[relation]-> entity2.

        Idempotent: if the same (src, relation, dst) triple already exists the
        weight is updated but no duplicate edge is created.

        Returns:
            {"src": str, "relation": str, "dst": str, "edge_id": str, "new": bool}
        """
        relation = relation.strip().lower().replace(" ", "_")
        src_id = self._get_or_create_entity(entity1, kind1)
        dst_id = self._get_or_create_entity(entity2, kind2)

        now = datetime.now(timezone.utc).isoformat()
        with _db(self._path) as conn:
            existing = conn.execute(
                "SELECT id FROM kg_edges WHERE src_id=? AND relation=? AND dst_id=?",
                [src_id, relation, dst_id],
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE kg_edges SET weight=? WHERE id=?",
                    [weight, existing[0]],
                )
                self._graph = None  # invalidate cache
                return {"src": entity1, "relation": relation, "dst": entity2,
                        "edge_id": existing[0], "new": False}

            edge_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO kg_edges (id,src_id,relation,dst_id,weight,created_at) VALUES (?,?,?,?,?,?)",
                [edge_id, src_id, relation, dst_id, weight, now],
            )
        self._graph = None
        return {"src": entity1, "relation": relation, "dst": entity2,
                "edge_id": edge_id, "new": True}

    def query(
        self,
        entity: str,
        depth: int = 2,
        max_results: int = 30,
    ) -> Dict:
        """Return entities reachable from ``entity`` within ``depth`` hops.

        Uses NetworkX BFS when available; falls back to iterative SQL joins.

        Returns:
            {"entity": str, "depth": int, "nodes": [...], "edges": [...]}
            node: {"name": str, "kind": str, "distance": int}
            edge: {"src": str, "relation": str, "dst": str, "weight": float}
        """
        with _db(self._path) as conn:
            row = conn.execute(
                "SELECT id FROM kg_entities WHERE name=? COLLATE NOCASE", [entity]
            ).fetchone()
            if not row:
                return {"entity": entity, "depth": depth,
                        "nodes": [], "edges": [], "error": "entity not found"}
            root_id = row[0]

        if _NX_AVAILABLE:
            return self._nx_query(entity, root_id, depth, max_results)
        return self._sql_query(entity, root_id, depth, max_results)

    def search(self, query: str, limit: int = 20) -> List[Dict]:
        """Full-text search over entity names (case-insensitive LIKE).

        Returns list of {"name": str, "kind": str} dicts.
        """
        with _db(self._path) as conn:
            rows = conn.execute(
                "SELECT name, kind FROM kg_entities WHERE name LIKE ? COLLATE NOCASE LIMIT ?",
                [f"%{query}%", limit],
            ).fetchall()
        return [{"name": r[0], "kind": r[1]} for r in rows]

    def stats(self) -> Dict:
        """Return basic graph statistics."""
        with _db(self._path) as conn:
            n_entities = conn.execute("SELECT COUNT(*) FROM kg_entities").fetchone()[0]
            n_edges = conn.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0]
            top_kinds = conn.execute(
                "SELECT kind, COUNT(*) as c FROM kg_entities GROUP BY kind ORDER BY c DESC LIMIT 5"
            ).fetchall()
            top_relations = conn.execute(
                "SELECT relation, COUNT(*) as c FROM kg_edges GROUP BY relation ORDER BY c DESC LIMIT 5"
            ).fetchall()
        return {
            "entities": n_entities,
            "edges": n_edges,
            "top_entity_kinds": [{"kind": r[0], "count": r[1]} for r in top_kinds],
            "top_relations": [{"relation": r[0], "count": r[1]} for r in top_relations],
        }

    # ── NetworkX query ─────────────────────────────────────────────────────

    def _load_nx_graph(self) -> Any:
        if self._graph is not None:
            return self._graph
        G = nx.DiGraph()
        with _db(self._path) as conn:
            for row in conn.execute("SELECT id, name, kind FROM kg_entities").fetchall():
                G.add_node(row[0], name=row[1], kind=row[2])
            for row in conn.execute(
                "SELECT src_id, relation, dst_id, weight FROM kg_edges"
            ).fetchall():
                G.add_edge(row[0], row[2], relation=row[1], weight=row[3])
        self._graph = G
        return G

    def _nx_query(
        self, entity: str, root_id: str, depth: int, max_results: int
    ) -> Dict:
        G = self._load_nx_graph()
        if root_id not in G:
            return {"entity": entity, "depth": depth, "nodes": [], "edges": []}

        visited: Dict[str, int] = {root_id: 0}
        queue = [root_id]
        collected_edges: List[Dict] = []

        for _ in range(depth):
            next_queue = []
            for node_id in queue:
                dist = visited[node_id]
                if dist >= depth:
                    continue
                # outgoing
                for nbr in G.successors(node_id):
                    if nbr not in visited:
                        visited[nbr] = dist + 1
                        next_queue.append(nbr)
                    edata = G.edges[node_id, nbr]
                    collected_edges.append({
                        "src": G.nodes[node_id].get("name", node_id),
                        "relation": edata.get("relation", "related"),
                        "dst": G.nodes[nbr].get("name", nbr),
                        "weight": edata.get("weight", 1.0),
                    })
                # incoming
                for nbr in G.predecessors(node_id):
                    if nbr not in visited:
                        visited[nbr] = dist + 1
                        next_queue.append(nbr)
                    edata = G.edges[nbr, node_id]
                    collected_edges.append({
                        "src": G.nodes[nbr].get("name", nbr),
                        "relation": edata.get("relation", "related"),
                        "dst": G.nodes[node_id].get("name", node_id),
                        "weight": edata.get("weight", 1.0),
                    })
            queue = next_queue

        nodes = [
            {"name": G.nodes[nid].get("name", nid),
             "kind": G.nodes[nid].get("kind", "concept"),
             "distance": dist}
            for nid, dist in sorted(visited.items(), key=lambda x: x[1])
            if nid != root_id
        ][:max_results]

        # Deduplicate edges
        seen_edges: set = set()
        deduped_edges = []
        for e in collected_edges:
            key = (e["src"], e["relation"], e["dst"])
            if key not in seen_edges:
                seen_edges.add(key)
                deduped_edges.append(e)

        return {
            "entity": entity,
            "depth": depth,
            "nodes": nodes,
            "edges": deduped_edges[:max_results],
        }

    # ── SQL fallback query ─────────────────────────────────────────────────

    def _sql_query(
        self, entity: str, root_id: str, depth: int, max_results: int
    ) -> Dict:
        """Iterative SQL BFS when NetworkX is not installed."""
        visited: Dict[str, int] = {root_id: 0}
        frontier = [root_id]
        collected_edges: List[Dict] = []

        with _db(self._path) as conn:
            for d in range(1, depth + 1):
                if not frontier:
                    break
                placeholders = ",".join("?" * len(frontier))
                rows = conn.execute(
                    f"SELECT e.src_id, e.relation, e.dst_id, e.weight, "
                    f"s.name, t.name "
                    f"FROM kg_edges e "
                    f"JOIN kg_entities s ON s.id=e.src_id "
                    f"JOIN kg_entities t ON t.id=e.dst_id "
                    f"WHERE e.src_id IN ({placeholders}) OR e.dst_id IN ({placeholders})",
                    frontier * 2,
                ).fetchall()
                next_frontier = []
                for row in rows:
                    src_id, relation, dst_id, weight, src_name, dst_name = row
                    collected_edges.append({
                        "src": src_name, "relation": relation,
                        "dst": dst_name, "weight": weight,
                    })
                    for nid in (src_id, dst_id):
                        if nid != root_id and nid not in visited:
                            visited[nid] = d
                            next_frontier.append(nid)
                frontier = next_frontier

            # Resolve names for visited nodes
            if len(visited) > 1:
                others = [nid for nid in visited if nid != root_id]
                placeholders = ",".join("?" * len(others))
                name_rows = conn.execute(
                    f"SELECT id, name, kind FROM kg_entities WHERE id IN ({placeholders})",
                    others,
                ).fetchall()
                name_map = {r[0]: (r[1], r[2]) for r in name_rows}
            else:
                name_map = {}

        nodes = [
            {"name": name_map.get(nid, (nid, "concept"))[0],
             "kind": name_map.get(nid, (nid, "concept"))[1],
             "distance": dist}
            for nid, dist in sorted(visited.items(), key=lambda x: x[1])
            if nid != root_id
        ][:max_results]

        seen_edges: set = set()
        deduped_edges = []
        for e in collected_edges:
            key = (e["src"], e["relation"], e["dst"])
            if key not in seen_edges:
                seen_edges.add(key)
                deduped_edges.append(e)

        return {
            "entity": entity,
            "depth": depth,
            "nodes": nodes,
            "edges": deduped_edges[:max_results],
        }

    # ── Bulk import from L2 ────────────────────────────────────────────────

    def ingest_from_text(self, text: str, source_entity: str = "") -> int:
        """Extract and store entity mentions from free text.

        Uses simple heuristics: capitalized 2-word phrases (proper nouns) and
        any word after 'of', 'for', 'by', 'about', 'called', 'named'.
        Returns the count of new triples added.
        """
        # Capitalized noun-phrases: "Project Pi", "Ash Ali", "Phase 8"
        phrases = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", text)
        # Single proper nouns (≥4 chars, capitalized, not at sentence start)
        singles = re.findall(r"(?<=[.!?]\s|[,;]\s)\b([A-Z][a-zA-Z]{3,})\b", text)
        entities = list(dict.fromkeys(phrases + singles))  # deduplicate, preserve order

        added = 0
        if source_entity and entities:
            for ent in entities[:20]:  # cap at 20 per call
                result = self.add_triple(source_entity, "mentions", ent)
                if result.get("new"):
                    added += 1
        return added

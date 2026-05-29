"""scripts/export_kg.py — Export L4 Knowledge Graph to Graphviz DOT for VS Code preview.

Usage:
    python scripts/export_kg.py                  # exports to data/kg_graph.dot
    python scripts/export_kg.py --open           # export + open in VS Code
    python scripts/export_kg.py --focus Ash      # only show nodes within 2 hops of entity
    python scripts/export_kg.py --format svg     # also render to SVG (needs graphviz CLI)

Install VS Code extension:
    "Graphviz Preview" by EFanZh (ext ID: efanzh.graphviz-preview)
    or search "Graphviz Interactive Preview" — either works.

Then right-click the .dot file → "Open Preview"
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_DB_PATH = _ROOT / "data" / "kg.db"
_OUT_DOT = _ROOT / "data" / "kg_graph.dot"

# Colour palette per entity kind
_KIND_COLORS = {
    "person":   ("#4A90D9", "#DDEEFF"),   # blue
    "project":  ("#E67E22", "#FEF0E0"),   # orange
    "tool":     ("#27AE60", "#E8F8F0"),   # green
    "file":     ("#8E44AD", "#F5EEF8"),   # purple
    "concept":  ("#7F8C8D", "#F0F0F0"),   # grey
    "place":    ("#C0392B", "#FDECEA"),   # red
    "org":      ("#2980B9", "#EBF5FB"),   # teal
}
_DEFAULT_COLOR = ("#555555", "#F8F8F8")


def _load_graph(focus: str | None = None, hops: int = 2):
    """Load entities + edges from SQLite. Optionally filter to N hops from focus."""
    if not _DB_PATH.exists():
        print(f"[export_kg] No KG database found at {_DB_PATH}")
        print("[export_kg] Pi hasn't built any graph triples yet.")
        print("[export_kg] Ask Pi to 'add to knowledge graph' or use kg_add_triple.")
        return [], []

    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row

    entities = {
        r["id"]: dict(r)
        for r in conn.execute("SELECT id, name, kind FROM kg_entities").fetchall()
    }
    edges = [
        dict(r)
        for r in conn.execute(
            "SELECT src_id, relation, dst_id, weight FROM kg_edges"
        ).fetchall()
    ]
    conn.close()

    if not focus:
        return list(entities.values()), edges

    # BFS to find nodes within N hops of focus entity
    focus_lower = focus.lower()
    seed_ids = {
        eid for eid, e in entities.items()
        if focus_lower in e["name"].lower()
    }
    if not seed_ids:
        print(f"[export_kg] Entity '{focus}' not found — exporting full graph.")
        return list(entities.values()), edges

    # Build adjacency for BFS
    adj: dict[str, set[str]] = {eid: set() for eid in entities}
    for e in edges:
        adj[e["src_id"]].add(e["dst_id"])
        adj[e["dst_id"]].add(e["src_id"])

    visited = set(seed_ids)
    frontier = set(seed_ids)
    for _ in range(hops):
        next_frontier = set()
        for nid in frontier:
            for nb in adj.get(nid, set()):
                if nb not in visited:
                    visited.add(nb)
                    next_frontier.add(nb)
        frontier = next_frontier

    filtered_entities = [e for e in entities.values() if e["id"] in visited]
    filtered_edges = [
        e for e in edges
        if e["src_id"] in visited and e["dst_id"] in visited
    ]
    return filtered_entities, filtered_edges


def _build_dot(entities, edges) -> str:
    lines = [
        "digraph KnowledgeGraph {",
        "    graph [",
        '        bgcolor="#1E1E2E"',
        '        fontname="Helvetica"',
        '        label="Pi Knowledge Graph"',
        '        labelloc="t"',
        '        fontcolor="#CDD6F4"',
        '        fontsize="18"',
        "        rankdir=LR",
        "        pad=0.5",
        "        splines=curved",
        "        overlap=false",
        "    ]",
        "    node [",
        '        fontname="Helvetica"',
        "        fontsize=12",
        "        style=filled",
        "        shape=roundedbox",
        "        penwidth=1.5",
        "    ]",
        "    edge [",
        '        fontname="Helvetica"',
        "        fontsize=10",
        "        penwidth=1.2",
        '        color="#585B70"',
        '        fontcolor="#CDD6F4"',
        "    ]",
        "",
    ]

    # Nodes
    id_map = {e["id"]: e for e in entities}
    for ent in entities:
        kind = ent.get("kind", "concept")
        border_color, fill_color = _KIND_COLORS.get(kind, _DEFAULT_COLOR)
        label = ent["name"].replace('"', '\\"')
        node_id = ent["id"].replace("-", "_")
        lines.append(
            f'    {node_id} ['
            f'label="{label}\\n({kind})" '
            f'fillcolor="{fill_color}" '
            f'color="{border_color}" '
            f'fontcolor="#1E1E2E"'
            f"];"
        )

    lines.append("")

    # Edges
    for edge in edges:
        src = edge["src_id"].replace("-", "_")
        dst = edge["dst_id"].replace("-", "_")
        rel = edge["relation"].replace('"', '\\"')
        weight = float(edge.get("weight") or 1.0)
        width = min(1.0 + weight * 0.5, 4.0)
        lines.append(
            f'    {src} -> {dst} [label="{rel}" penwidth={width:.1f}];'
        )

    lines.append("}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Export Pi KG to Graphviz DOT")
    parser.add_argument("--focus",  default=None, help="Centre graph on this entity")
    parser.add_argument("--hops",   type=int, default=2, help="Hops from focus (default 2)")
    parser.add_argument("--open",   action="store_true", help="Open in VS Code after export")
    parser.add_argument("--format", default=None, choices=["svg", "png"], help="Also render image")
    parser.add_argument("--out",    default=str(_OUT_DOT), help="Output .dot file path")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    entities, edges = _load_graph(focus=args.focus, hops=args.hops)

    if not entities:
        # Write a placeholder so VS Code still opens something
        placeholder = (
            'digraph KnowledgeGraph {\n'
            '    graph [bgcolor="#1E1E2E" label="Pi Knowledge Graph — empty" '
            'fontcolor="#CDD6F4" fontsize=18]\n'
            '    empty [label="No triples yet.\\nAsk Pi to kg_add_triple." '
            'shape=note style=filled fillcolor="#313244" fontcolor="#CDD6F4"]\n'
            '}\n'
        )
        out_path.write_text(placeholder)
        print(f"[export_kg] Empty graph written to {out_path}")
    else:
        dot_src = _build_dot(entities, edges)
        out_path.write_text(dot_src, encoding="utf-8")
        print(f"[export_kg] Exported {len(entities)} nodes, {len(edges)} edges → {out_path}")

    # Optionally render to image using graphviz CLI
    if args.format:
        try:
            img_path = out_path.with_suffix(f".{args.format}")
            subprocess.run(
                ["dot", f"-T{args.format}", str(out_path), "-o", str(img_path)],
                check=True,
            )
            print(f"[export_kg] Rendered → {img_path}")
        except FileNotFoundError:
            print("[export_kg] graphviz CLI ('dot') not found — install from graphviz.org for image export")
        except subprocess.CalledProcessError as e:
            print(f"[export_kg] Render failed: {e}")

    if args.open:
        try:
            subprocess.run(["code", str(out_path)], check=False)
            print("[export_kg] Opened in VS Code - right-click the file -> Open Preview")
        except FileNotFoundError:
            print(f"[export_kg] VS Code CLI not found - open manually: {out_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Analyze a food taxonomy JSON (like ./categories.json).

Expected format:
- Top-level is a dict: { "<category_id>": { "parents": [...], "children": [...], ... }, ... }

Notes:
- The taxonomy is a DAG-like graph in practice (some nodes have multiple parents).
- "Level" below is computed as the *minimum* distance (shortest path) from any root.
  This is the most useful definition for "how many categories are on each level?".
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
import random
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Set, Tuple


@dataclass(frozen=True)
class TaxonomyStats:
    file_bytes: int
    node_count: int
    edge_count_parent_links: int
    root_count: int
    missing_parent_links: int
    unreachable_count: int
    cycle_node_count: int
    max_level: int
    counts_by_level: Dict[int, int]
    leaf_counts_by_level: Dict[int, int]


def _load_taxonomy(path: Path) -> Dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise SystemExit(f"Expected top-level JSON object (dict). Got: {type(obj).__name__}")
    return obj


def _iter_parents(node_payload: Any) -> List[str]:
    if not isinstance(node_payload, dict):
        return []
    parents = node_payload.get("parents")
    if isinstance(parents, list):
        return [p for p in parents if isinstance(p, str) and p]
    return []

def _best_label(payload: Any) -> Optional[str]:
    """
    Try to extract a human-friendly label (prefer English).
    Typical schema: {"name": {"en": "...", "fr": "...", ...}}
    """
    if not isinstance(payload, dict):
        return None
    name = payload.get("name")
    if isinstance(name, dict):
        en = name.get("en")
        if isinstance(en, str) and en.strip():
            return en.strip()
        # fall back to first non-empty string value
        for v in name.values():
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def build_graph(tax: Dict[str, Any]) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]], int]:
    """
    Returns:
      - parent_map: node -> set(parents)
      - child_map: parent -> set(children) (derived from parents)
      - edge_count_parent_links: total number of (node -> parent) links in input
    """
    parent_map: Dict[str, Set[str]] = {k: set() for k in tax.keys()}
    child_map: DefaultDict[str, Set[str]] = defaultdict(set)
    edge_count = 0

    for node, payload in tax.items():
        parents = _iter_parents(payload)
        for p in parents:
            edge_count += 1
            parent_map[node].add(p)
            child_map[p].add(node)

    # Ensure all nodes are present as keys in child_map for iteration convenience.
    for node in tax.keys():
        child_map.setdefault(node, set())

    return parent_map, dict(child_map), edge_count


def find_roots(parent_map: Dict[str, Set[str]], all_nodes: Set[str]) -> Tuple[Set[str], int]:
    """
    Root is defined as:
      - node with no known parents in the taxonomy, OR
      - all its parents are missing from the taxonomy file (external parent)

    Returns (roots, missing_parent_links)
    """
    roots: Set[str] = set()
    missing_parent_links = 0

    for node, parents in parent_map.items():
        if not parents:
            roots.add(node)
            continue
        present_parents = {p for p in parents if p in all_nodes}
        missing_parent_links += len(parents) - len(present_parents)
        if not present_parents:
            roots.add(node)

    return roots, missing_parent_links


def compute_levels_shortest_path(
    roots: Set[str], child_map: Dict[str, Set[str]]
) -> Dict[str, int]:
    """
    Breadth-first search from all roots to compute minimum distance (level).
    Roots are assigned level 0.
    """
    level: Dict[str, int] = {}
    q: deque[str] = deque()

    for r in roots:
        level[r] = 0
        q.append(r)

    while q:
        u = q.popleft()
        base = level[u]
        for v in child_map.get(u, ()):
            cand = base + 1
            prev = level.get(v)
            if prev is None or cand < prev:
                level[v] = cand
                q.append(v)

    return level


def compute_levels_covering_all_nodes(
    all_nodes: Set[str],
    roots: Set[str],
    child_map: Dict[str, Set[str]],
) -> Tuple[Dict[str, int], Set[str]]:
    """
    Compute levels from roots, and if any nodes are not reachable (disconnected graph),
    treat the unreachable nodes as additional roots to ensure all nodes get a level.

    Returns (level_by_node, unreachable_nodes_from_initial_roots)
    """
    levels = compute_levels_shortest_path(roots, child_map)
    unreachable = all_nodes.difference(levels.keys())
    if unreachable:
        extra = compute_levels_shortest_path(unreachable, child_map)
        for n, lv in extra.items():
            prev = levels.get(n)
            if prev is None or lv < prev:
                levels[n] = lv
    return levels, unreachable


def detect_cycle_nodes(child_map: Dict[str, Set[str]], nodes: Set[str]) -> Set[str]:
    """
    DFS-based cycle detection. Returns the set of nodes that are part of at least one cycle.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {n: WHITE for n in nodes}
    stack: List[str] = []
    index_in_stack: Dict[str, int] = {}
    cycle_nodes: Set[str] = set()

    def dfs(u: str) -> None:
        color[u] = GRAY
        index_in_stack[u] = len(stack)
        stack.append(u)

        for v in child_map.get(u, ()):
            if v not in nodes:
                continue
            if color[v] == WHITE:
                dfs(v)
            elif color[v] == GRAY:
                # Found a back-edge to v; nodes from v..end are in a cycle.
                start = index_in_stack.get(v)
                if start is not None:
                    cycle_nodes.update(stack[start:])

        stack.pop()
        index_in_stack.pop(u, None)
        color[u] = BLACK

    for n in nodes:
        if color[n] == WHITE:
            dfs(n)

    return cycle_nodes


def analyze(path: Path) -> TaxonomyStats:
    tax = _load_taxonomy(path)
    all_nodes = set(tax.keys())
    parent_map, child_map, edge_count = build_graph(tax)

    roots, missing_parent_links = find_roots(parent_map, all_nodes)
    levels, unreachable = compute_levels_covering_all_nodes(all_nodes, roots, child_map)

    cycle_nodes = detect_cycle_nodes(child_map, all_nodes)

    counts_by_level = Counter(levels.values())
    max_level = max(counts_by_level.keys()) if counts_by_level else 0

    leaf_counts_by_level: Counter[int] = Counter()
    for n, lvl in levels.items():
        if len(child_map.get(n, set())) == 0:
            leaf_counts_by_level[lvl] += 1

    return TaxonomyStats(
        file_bytes=path.stat().st_size,
        node_count=len(all_nodes),
        edge_count_parent_links=edge_count,
        root_count=len(roots),
        missing_parent_links=missing_parent_links,
        unreachable_count=len(unreachable),
        cycle_node_count=len(cycle_nodes),
        max_level=max_level,
        counts_by_level=dict(sorted(counts_by_level.items(), key=lambda kv: kv[0])),
        leaf_counts_by_level=dict(sorted(leaf_counts_by_level.items(), key=lambda kv: kv[0])),
    )

def levels_with_examples(
    tax: Dict[str, Any], level_by_node: Dict[str, int], max_per_level: int
) -> Dict[int, List[Tuple[str, Optional[str]]]]:
    by_level: DefaultDict[int, List[str]] = defaultdict(list)
    for node, lvl in level_by_node.items():
        by_level[lvl].append(node)

    examples: Dict[int, List[Tuple[str, Optional[str]]]] = {}
    for lvl, nodes in by_level.items():
        nodes_sorted = sorted(nodes)
        sample = random.sample(nodes_sorted, min(max_per_level, len(nodes_sorted)))
        examples[lvl] = [(n, _best_label(tax.get(n)), _iter_parents(tax.get(n))) for n in sample]
    return dict(sorted(examples.items(), key=lambda kv: kv[0]))


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    units = ["KiB", "MiB", "GiB", "TiB"]
    x = float(n)
    for u in units:
        x /= 1024.0
        if x < 1024.0:
            return f"{x:.2f} {u}"
    return f"{x:.2f} PiB"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Analyze a food taxonomy categories.json")
    ap.add_argument(
        "--file",
        default=str(Path(__file__).resolve().parents[1] / "categories.json"),
        help="Path to categories.json (default: repo root ./categories.json)",
    )
    ap.add_argument(
        "--examples",
        type=int,
        default=0,
        help="Show N example category IDs per level (deterministic: lexicographically first IDs).",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of human text.",
    )
    args = ap.parse_args(argv)

    path = Path(args.file).expanduser().resolve()
    tax = _load_taxonomy(path)
    stats = analyze(path)

    if args.json:
        out = {
            "file": str(path),
            "file_bytes": stats.file_bytes,
            "node_count": stats.node_count,
            "edge_count_parent_links": stats.edge_count_parent_links,
            "root_count": stats.root_count,
            "missing_parent_links": stats.missing_parent_links,
            "unreachable_count": stats.unreachable_count,
            "cycle_node_count": stats.cycle_node_count,
            "max_level": stats.max_level,
            "counts_by_level": stats.counts_by_level,
            "leaf_counts_by_level": stats.leaf_counts_by_level,
        }
        if args.examples and args.examples > 0:
            parent_map, child_map, _ = build_graph(tax)
            roots, _missing = find_roots(parent_map, set(tax.keys()))
            level_by_node, _unreachable = compute_levels_covering_all_nodes(
                set(tax.keys()), roots, child_map
            )
            ex = levels_with_examples(tax, level_by_node, args.examples)
            out["examples_by_level"] = {
                str(lvl): [{"id": cid, "label": label} for cid, label in items]
                for lvl, items in ex.items()
            }
        print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print(f"File: {path}")
    print(f"Size: {_format_bytes(stats.file_bytes)} ({stats.file_bytes} bytes)")
    print(f"Nodes: {stats.node_count}")
    print(f"Parent-links (edges): {stats.edge_count_parent_links}")
    print(f"Roots (level 0 sources): {stats.root_count}")
    print(f"Missing-parent links: {stats.missing_parent_links}")
    print(f"Disconnected/unreachable-from-roots nodes: {stats.unreachable_count}")
    print(f"Cycle nodes detected: {stats.cycle_node_count}")
    print(f"Max level (shortest-path): {stats.max_level}")
    print("Counts by level (level -> node count):")
    for lvl in range(0, stats.max_level + 1):
        print(f"  {lvl}: {stats.counts_by_level.get(lvl, 0)}")

    print("Leaf counts by level (level -> leaf node count):")
    for lvl in range(0, stats.max_level + 1):
        print(f"  {lvl}: {stats.leaf_counts_by_level.get(lvl, 0)}")

    if args.examples and args.examples > 0:
        parent_map, child_map, _ = build_graph(tax)
        roots, _missing = find_roots(parent_map, set(tax.keys()))
        level_by_node, _unreachable = compute_levels_covering_all_nodes(
            set(tax.keys()), roots, child_map
        )
        examples = levels_with_examples(tax, level_by_node, args.examples)
        print(f"\nExamples per level (first {args.examples} IDs):")
        for lvl in range(0, stats.max_level + 1):
            items = examples.get(lvl, [])
            print(f"  {lvl}:")
            if not items:
                print("    (none)")
                continue
            for cid, label, parents in items:
                for p in parents:
                    parent_level = level_by_node.get(p, 0)
                    if parent_level != lvl - 1:
                        print(f"    - {p}, level: {parent_level}")
                if label:
                    print(f"    - {cid} — {label} (parents: {parents})")
                else:
                    print(f"    - {cid}")



    return 0


if __name__ == "__main__":
    raise SystemExit(main())



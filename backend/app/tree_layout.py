"""Hyperbolic-ish tree layout with proper parent_taxid linkage."""
from __future__ import annotations

import math
from typing import Any


def count_leaves(node: dict[str, Any]) -> int:
    children = node.get("children") or []
    if not children:
        return 1
    return sum(count_leaves(c) for c in children)


def layout(root: dict[str, Any], scale: float = 1000.0) -> list[dict[str, Any]]:
    """Return a flat list of positioned nodes.

    Each dict: {taxid, name, rank, parent_taxid, x, y, depth, leaf_count}

    parent_taxid is always the taxid of the immediate parent. For synthetic
    (None-taxid) ancestors from legacy data, we use a negative synthetic ID
    so the linkage still works in the frontend.
    """
    nodes: list[dict[str, Any]] = []

    def compute_leaves(n: dict[str, Any]) -> int:
        n["_leaves"] = count_leaves(n)
        for c in n.get("children") or []:
            compute_leaves(c)
        return n["_leaves"]

    compute_leaves(root)

    # Synthetic-id generator for nodes without a real taxid.
    # These negative IDs are stable within a single layout call — the client
    # just needs them to be consistent for parent-child lookup.
    counter = {"next": -1}

    def resolve_id(node: dict[str, Any]) -> int:
        if node.get("taxid"):
            return node["taxid"]
        # Assign a synthetic ID based on position in tree.
        if "_synthetic_id" not in node:
            node["_synthetic_id"] = counter["next"]
            counter["next"] -= 1
        return node["_synthetic_id"]

    def place(
        node: dict[str, Any],
        depth: int,
        angle_start: float,
        angle_end: float,
        parent_id: int | None,
    ) -> None:
        angle_center = (angle_start + angle_end) / 2
        radius = scale * (1 - 1.0 / (depth + 1)) if depth > 0 else 0.0
        x = radius * math.cos(angle_center)
        y = radius * math.sin(angle_center)

        my_id = resolve_id(node)

        nodes.append({
            "taxid": my_id,
            "name": node["name"],
            "rank": node.get("rank"),
            "parent_taxid": parent_id,
            "x": x,
            "y": y,
            "depth": depth,
            "leaf_count": node["_leaves"],
        })

        children = node.get("children") or []
        if not children:
            return

        total_leaves = sum(c["_leaves"] for c in children)
        if total_leaves == 0:
            return

        if depth == 0:
            child_start = 0.0
            child_span = 2 * math.pi
        else:
            parent_span = angle_end - angle_start
            child_span = parent_span * 0.85
            child_start = angle_center - child_span / 2

        cursor = child_start
        for c in children:
            weight = c["_leaves"] / total_leaves
            wedge = child_span * weight
            place(c, depth + 1, cursor, cursor + wedge, my_id)
            cursor += wedge

    place(root, 0, 0.0, 2 * math.pi, None)
    return nodes

"""Concept graph helpers shared by `import_graph` and the teacher's graph page.

- find_prerequisite_cycle: a cycle in directed edges, if any.
- readiness_checks: the preregistered hypotheses' preconditions (docs/preregistration.md).
- layout: a layered drawing (prerequisite depth top to bottom) for the admin graph page.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .models import Concept, ConceptEdge

# Preconditions of the preregistered hypotheses (docs/preregistration.md).
MIN_CONCEPTS, MAX_CONCEPTS = 20, 40
MIN_H1_PER_GROUP = 4  # PREREQUISITE and RELATED edges
MIN_H2_PER_GROUP = 3  # structure→algorithm and structure→structure edges


def find_prerequisite_cycle(edges: list[tuple[str, str]]) -> list[str] | None:
    """A cycle such as ["a", "b", "a"] if the directed edges contain one, else None."""
    graph: dict[str, list[str]] = {}
    for s, t in edges:
        graph.setdefault(s, []).append(t)
    color: dict[str, str] = {}  # "open" while on the DFS path, "done" afterwards

    def dfs(node: str, path: list[str]) -> list[str] | None:
        color[node] = "open"
        path.append(node)
        for nxt in graph.get(node, []):
            if color.get(nxt) == "open":
                return path[path.index(nxt):] + [nxt]
            if nxt not in color:
                found = dfs(nxt, path)
                if found:
                    return found
        path.pop()
        color[node] = "done"
        return None

    for start in list(graph):
        if start not in color:
            found = dfs(start, [])
            if found:
                return found
    return None


@dataclass
class Check:
    ok: bool
    en: str  # import_graph output
    mn: str  # graph page


def readiness_checks(concepts: list[Concept], approved_edges: list[ConceptEdge]) -> list[Check]:
    """Whether the approved graph meets the hypotheses' preconditions."""
    by_rel = Counter(e.relation for e in approved_edges)
    S, A = Concept.Kind.STRUCTURE, Concept.Kind.ALGORITHM
    graph_edges = [e for e in approved_edges if e.relation != ConceptEdge.Relation.CONTROL]
    s_to_a = sum(e.source.kind == S and e.target.kind == A for e in graph_edges)
    s_to_s = sum(e.source.kind == S and e.target.kind == S for e in graph_edges)
    no_kind = sorted(c.code for c in concepts if not c.kind)
    pre, rel = by_rel["PREREQUISITE"], by_rel["RELATED"]
    n = len(concepts)
    return [
        Check(MIN_CONCEPTS <= n <= MAX_CONCEPTS, f"concepts {n} (need {MIN_CONCEPTS}–{MAX_CONCEPTS})",
              f"Concept {n} (шаардлага {MIN_CONCEPTS}–{MAX_CONCEPTS})"),
        Check(not no_kind, f"every concept has a kind (missing: {', '.join(no_kind) or 'none'})",
              f"Concept бүр төрөлтэй (төрөлгүй: {', '.join(no_kind) or 'байхгүй'})"),
        Check(pre >= MIN_H1_PER_GROUP, f"H1: PREREQUISITE edges {pre} (need ≥ {MIN_H1_PER_GROUP})",
              f"H1: PREREQUISITE холбоос {pre} (≥ {MIN_H1_PER_GROUP} хэрэгтэй)"),
        Check(rel >= MIN_H1_PER_GROUP, f"H1: RELATED edges {rel} (need ≥ {MIN_H1_PER_GROUP}; otherwise H1 is dropped)",
              f"H1: RELATED холбоос {rel} (≥ {MIN_H1_PER_GROUP} хэрэгтэй, үгүй бол H1 хасагдана)"),
        Check(s_to_a >= MIN_H2_PER_GROUP, f"H2: structure→algorithm edges {s_to_a} (need ≥ {MIN_H2_PER_GROUP})",
              f"H2: бүтэц → алгоритм холбоос {s_to_a} (≥ {MIN_H2_PER_GROUP} хэрэгтэй)"),
        Check(s_to_s >= MIN_H2_PER_GROUP, f"H2: structure→structure edges {s_to_s} (need ≥ {MIN_H2_PER_GROUP})",
              f"H2: бүтэц → бүтэц холбоос {s_to_s} (≥ {MIN_H2_PER_GROUP} хэрэгтэй)"),
        Check(True, f"control edges {by_rel['CONTROL']} (optional)", f"CONTROL холбоос {by_rel['CONTROL']} (заавал биш)"),
    ]  # fmt: skip


# --- Layout ---------------------------------------------------------------------------

NODE_W, NODE_H, GAP_X, GAP_Y, MARGIN = 168, 50, 22, 84, 24
ARC_ROOM = 40
KIND_ORDER = {Concept.Kind.STRUCTURE: 0, Concept.Kind.ALGORITHM: 1, Concept.Kind.OTHER: 2, "": 3}


@dataclass
class Node:
    concept: Concept
    level: int  # prerequisite depth; -1 = no edges at all (drawn in a separate bottom row)
    x: float = 0
    y: float = 0
    in_cycle: bool = False
    questions: int = 0
    label: str = ""

    @property
    def cx(self):
        return self.x + NODE_W / 2

    @property
    def name_y(self):
        return self.y + 20

    @property
    def code_y(self):
        return self.y + 37


@dataclass
class Edge:
    edge: ConceptEdge
    path: str
    label_x: float = 0
    label_y: float = 0


@dataclass
class Layout:
    nodes: list[Node]
    edges: list[Edge]
    width: float
    height: float
    cycle: list[str] | None
    isolated_y: float | None = None
    rows: list[list[Node]] = field(default_factory=list)


def _short(text: str, limit: int = 20) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def layout(concepts: list[Concept], edges: list[ConceptEdge], questions: dict[int, int] | None = None) -> Layout:
    """Prerequisite depth gives the row; a barycenter pass orders each row to cut crossings.
    RELATED and CONTROL edges are drawn but do not change the rows."""
    by_id = {c.id: c for c in concepts}
    pre = [(e.source_id, e.target_id) for e in edges if e.relation == ConceptEdge.Relation.PREREQUISITE]
    cycle_codes = find_prerequisite_cycle([(by_id[s].code, by_id[t].code) for s, t in pre])
    touched = {e.source_id for e in edges} | {e.target_id for e in edges}

    # Longest-path depth over prerequisite edges (Kahn). Nodes left over sit on a cycle.
    parents, children = defaultdict(list), defaultdict(list)
    for s, t in pre:
        parents[t].append(s)
        children[s].append(t)
    indeg = {c.id: len(parents[c.id]) for c in concepts}
    level = {cid: 0 for cid in indeg}
    queue = [cid for cid, d in indeg.items() if d == 0]
    done = set()
    while queue:
        cid = queue.pop()
        done.add(cid)
        for nxt in children[cid]:
            level[nxt] = max(level[nxt], level[cid] + 1)
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    stuck = {cid for cid in indeg if cid not in done}
    top = max((level[cid] for cid in done if cid in touched), default=-1)
    for cid in stuck:
        level[cid] = top + 1

    nodes = {}
    for c in concepts:
        lvl = level[c.id] if c.id in touched else -1
        nodes[c.id] = Node(c, lvl, in_cycle=c.id in stuck, questions=(questions or {}).get(c.id, 0),
                           label=_short(c.name or c.code))  # fmt: skip

    rows: list[list[Node]] = []
    for lvl in range(max((n.level for n in nodes.values()), default=-1) + 1):
        row = [n for n in nodes.values() if n.level == lvl]
        if lvl == 0:
            row.sort(key=lambda n: (KIND_ORDER.get(n.concept.kind, 3), n.concept.code))
        else:
            position = {n.concept.id: i for i, n in enumerate(node for r in rows for node in r)}

            def barycenter(n):
                ps = [position[p] for p in parents[n.concept.id] if p in position]
                return (sum(ps) / len(ps) if ps else float("inf"), n.concept.code)

            row.sort(key=barycenter)
        if row:
            rows.append(row)
    isolated = sorted((n for n in nodes.values() if n.level == -1), key=lambda n: n.concept.code)

    per_row = max([len(r) for r in rows] + [min(len(isolated), 5), 1])
    width = 2 * MARGIN + per_row * NODE_W + (per_row - 1) * GAP_X
    y = MARGIN + ARC_ROOM  # room for arcs between concepts of the first row
    for row in rows:
        row_w = len(row) * NODE_W + (len(row) - 1) * GAP_X
        x0 = (width - row_w) / 2
        for i, n in enumerate(row):
            n.x, n.y = x0 + i * (NODE_W + GAP_X), y
        y += NODE_H + GAP_Y
    isolated_y = None
    if isolated:
        isolated_y = y - GAP_Y / 2 + 8 if rows else y
        y = isolated_y + 30
        for start in range(0, len(isolated), per_row):
            chunk = isolated[start:start + per_row]
            for i, n in enumerate(chunk):
                n.x, n.y = MARGIN + i * (NODE_W + GAP_X), y
            y += NODE_H + 18
        y -= 18
    height = y + MARGIN if (rows or isolated) else 2 * MARGIN

    # Spread several edges leaving (or entering) one node across its width.
    out_edges, in_edges = defaultdict(list), defaultdict(list)
    for e in edges:
        out_edges[e.source_id].append(e)
        in_edges[e.target_id].append(e)
    for group in (*out_edges.values(), *in_edges.values()):
        group.sort(key=lambda e: (nodes[e.target_id].cx, nodes[e.source_id].cx))

    def anchor(node_id, e, group):
        k = len(group[node_id])
        i = group[node_id].index(e)
        return nodes[node_id].x + NODE_W * (i + 1) / (k + 1)

    drawn = []
    for e in edges:
        s, t = nodes[e.source_id], nodes[e.target_id]
        sx, tx = anchor(e.source_id, e, out_edges), anchor(e.target_id, e, in_edges)
        if t.y > s.y:  # downwards: bottom of source to top of target
            y1, y2 = s.y + NODE_H, t.y - 4
            bend = (y2 - y1) / 2
            path = f"M{sx:.1f},{y1:.1f} C{sx:.1f},{y1 + bend:.1f} {tx:.1f},{y2 - bend:.1f} {tx:.1f},{y2:.1f}"
            lx, ly = (sx + tx) / 2, (y1 + y2) / 2
        elif t.y == s.y:  # same row: arc above the row
            y1, y2 = s.y, t.y - 4
            lift = min(ARC_ROOM, 20 + abs(tx - sx) / 8)
            path = f"M{sx:.1f},{y1:.1f} C{sx:.1f},{y1 - lift:.1f} {tx:.1f},{y2 - lift:.1f} {tx:.1f},{y2:.1f}"
            lx, ly = (sx + tx) / 2, y1 - lift * 0.75
        else:  # upwards: top of source to bottom of target
            y1, y2 = s.y, t.y + NODE_H + 4
            bend = (y1 - y2) / 2
            path = f"M{sx:.1f},{y1:.1f} C{sx:.1f},{y1 - bend:.1f} {tx:.1f},{y2 + bend:.1f} {tx:.1f},{y2:.1f}"
            lx, ly = (sx + tx) / 2, (y1 + y2) / 2
        drawn.append(Edge(e, path, lx, ly))

    ordered = [n for r in rows for n in r] + isolated
    return Layout(ordered, drawn, width, height, cycle_codes, isolated_y, rows)

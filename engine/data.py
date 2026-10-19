"""Loading the research export into an ordered interaction sequence.

Inputs are the files produced by `manage.py export_events` (CSV) and
`manage.py export_graph` (JSON); the engine never touches the database.
Only graded events (correct is 0/1) are used; pending ones are dropped and counted.
"""

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Interaction:
    idx: int  # position in replay order
    event_id: str
    student: str
    t: float  # seconds since epoch (UTC)
    question: str
    concepts: tuple[tuple[int, float], ...]  # (concept index, a_qi)
    y: int
    score: float
    attempt_index: int
    hint_count: int
    response_time_sec: float | None
    assistance: int | None
    difficulty: int


@dataclass(frozen=True)
class Edge:
    source: int
    target: int
    relation: str
    initial_weight: float


@dataclass
class Dataset:
    interactions: list[Interaction]
    concepts: list[str]
    edges: list[Edge]
    questions: list[str]
    source_sha256: str = ""
    dropped: dict[str, int] = field(default_factory=dict)

    @property
    def students(self) -> list[str]:
        return sorted({i.student for i in self.interactions})

    def subset(self, indices) -> "Dataset":
        keep = set(indices)
        return Dataset(
            [i for i in self.interactions if i.idx in keep],
            self.concepts, self.edges, self.questions, self.source_sha256, self.dropped,
        )  # fmt: skip


def _parse_time(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def load_graph(path: str | Path, *, approved_only: bool = True) -> tuple[list[str], list[dict]]:
    graph = json.loads(Path(path).read_text(encoding="utf-8"))
    edges = [e for e in graph["edges"] if e.get("approved") or not approved_only]
    return [c["code"] for c in graph["concepts"]], edges


def load_dataset(events_csv: str | Path, graph_json: str | Path, *, approved_edges_only: bool = True) -> Dataset:
    raw = Path(events_csv).read_bytes()
    concepts, raw_edges = load_graph(graph_json, approved_only=approved_edges_only)
    cindex = {code: i for i, code in enumerate(concepts)}
    edges = [
        Edge(cindex[e["source"]], cindex[e["target"]], e["relation"], float(e["initial_weight"])) for e in raw_edges
    ]

    rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
    rows.sort(key=lambda r: (r["timestamp"], r["event_id"]))  # the replay order of the guide (§7)
    dropped = {"ungraded": 0}
    interactions, questions = [], {}
    for row in rows:
        if row["correct"] == "":
            dropped["ungraded"] += 1
            continue
        mapping = json.loads(row["concepts"])
        unknown = [m["concept"] for m in mapping if m["concept"] not in cindex]
        if unknown:
            raise ValueError(f"event {row['event_id']} uses concepts missing from the graph: {unknown}")
        questions.setdefault(row["question_id"], len(questions))
        interactions.append(
            Interaction(
                idx=len(interactions),
                event_id=row["event_id"],
                student=row["student_id"],
                t=_parse_time(row["timestamp"]),
                question=row["question_id"],
                concepts=tuple((cindex[m["concept"]], float(m["weight"])) for m in mapping),
                y=int(row["correct"]),
                score=float(row["score"]) if row["score"] != "" else float(row["correct"]),
                attempt_index=int(row["attempt_index"]),
                hint_count=int(row["hint_count"] or 0),
                response_time_sec=float(row["response_time_sec"]) if row["response_time_sec"] else None,
                assistance=int(row["assistance_flag"]) if row["assistance_flag"] != "" else None,
                difficulty=int(row["difficulty"]),
            )
        )
    return Dataset(interactions, concepts, edges, list(questions), hashlib.sha256(raw).hexdigest(), dropped)

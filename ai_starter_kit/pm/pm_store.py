"""Task store, part of pm/pm.py: constants, the Task record, loading and validation, the index with the
derived status, the invariants (violations a command must not add) and the follow-ups."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import json
import shutil
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import background  # noqa: E402
import speclib  # noqa: E402
import txn  # noqa: E402
from speclib import (ECR_DIR, SPEC_DIR, children, descendants, load_ecrs, load_requirements,  # noqa: E402
                     sha256, stale_links, suspects)
from tasklib import plain_text  # noqa: E402
from tasklib import (REPO_DIR, STRUCTURE, PM_DIR, TID_RE, QID_RE, TASK_KINDS, WORKFLOW,  # noqa: E402
                     NOTE_KINDS, WAITING, load_waiting,
                     StoreError, check_order, current_stage, load_workflow,
                     LANGUAGE, LANGUAGE_KEYS, VIEWS,
                     find_area, find_topic, load_language, load_questions, load_structure, load_views, need, now, read_counters,
                     read_json, split_group, take_number, write_json)


STATES = {"todo": "TODO.md", "wip": "WIP.md", "done": "DONE.md", "backlog": "BACKLOG.md", "dropped": "DROPPED.md"}
STATE_ORDER = ["wip", "todo", "backlog", "done", "dropped"]       # order within a topic in ALL_TASKS.md
ALL_FILE = "ALL_TASKS.md"
QUESTIONS_FILES = {"open": "QUESTIONS_OPEN.md", "closed": "QUESTIONS_CLOSED.md"}
NEXT_UP = 5                                                         # ready tasks listed first in TODO.md
META = "meta.json"
TEXT = "task.md"
ATTACH = "files"
KEYS = {"id", "title", "group", "phase", "order", "context", "depends", "needs", "questions", "kind",
        "superseded_by", "implements"}


@dataclass
class Task:
    number: int
    title: str
    order: int
    group: str
    area: str
    topic: str
    phase: str
    body: str
    state: str
    path: Path
    depends: list[int]
    needs: list[str]
    context: str = ""
    questions: list[str] = field(default_factory=list)
    kind: str = "work"
    superseded_by: list[int] = field(default_factory=list)
    implements: list[str] = field(default_factory=list)

    @property
    def task_id(self) -> str:
        return f"T-{self.number:04d}"


# ---------------------------------------------------------------- loading and validation

def parse_id(value: object, where: str) -> int:
    match = TID_RE.match(value) if isinstance(value, str) else None
    need(bool(match), where, f"bad task ID {value!r} (must be T-NNNN)")
    return int(match.group(1))


def string_list(meta: dict, key: str, where: Path) -> list[str]:
    value = meta.get(key, [])
    need(isinstance(value, list) and all(isinstance(v, str) and v for v in value), where,
         f"{key} must be a list of non-empty strings")
    return value


def parse_task(task_dir: Path, state: str, structure: dict, questions: dict) -> Task:
    meta_path, text_path = task_dir / META, task_dir / TEXT
    rel = str(meta_path.relative_to(REPO_DIR))
    for path in (meta_path, text_path):
        need(path.is_file(), task_dir, f"no {path.name}")
    extra = sorted(p.name for p in task_dir.iterdir()
                   if p.name not in (META, TEXT) and not (p.name == ATTACH and p.is_dir()))
    need(not extra, task_dir, f"unexpected files {', '.join(extra)} (attachments go in {ATTACH}/)")
    meta = read_json(meta_path)
    need(isinstance(meta, dict), meta_path, "must hold one JSON object")
    unknown = sorted(set(meta) - KEYS)
    need(not unknown, meta_path, f"unknown keys {', '.join(unknown)}")
    number = parse_id(meta.get("id"), rel)
    need(task_dir.name == meta["id"], meta_path, f"id {meta['id']} does not match the directory {task_dir.name}")
    title = meta.get("title")
    plain_text(title, meta_path, "title")
    order = meta.get("order", number)
    need(isinstance(order, int) and not isinstance(order, bool), meta_path, "order must be an integer")
    for key in ("group", "phase", "context"):
        need(isinstance(meta.get(key, ""), str), meta_path, f"{key} must be a string")
    group = meta.get("group", "")
    area, topic = split_group(structure, group, rel) if group else ("", "")
    phase = meta.get("phase", "")
    need(not phase or phase in [p["name"] for p in structure["phases"]], meta_path,
         f"unknown phase {phase!r} (python3 pm/pm.py phase list)")
    need(isinstance(meta.get("depends"), list), meta_path, "depends must be a list of task IDs ([] for none)")
    depends = [parse_id(d, f"{rel} depends") for d in meta["depends"]]
    qs = string_list(meta, "questions", meta_path)
    for q in qs:
        need(bool(QID_RE.match(q)), meta_path, f"bad question ID {q!r}")
        need(q in questions, meta_path, f"unknown question {q} (no pm/questions/{q}.json)")
    kind = meta.get("kind", "work")
    need(kind in TASK_KINDS, meta_path, f"kind must be one of {', '.join(TASK_KINDS)}")
    need(isinstance(meta.get("superseded_by", []), list), meta_path, "superseded_by must be a list of task IDs")
    superseded_by = [parse_id(d, f"{rel} superseded_by") for d in meta.get("superseded_by", [])]
    return Task(number=number, title=title, order=order, group=group, area=area, topic=topic, phase=phase,
                body=text_path.read_text().strip("\n"), state=state, path=task_dir, depends=depends,
                needs=string_list(meta, "needs", meta_path), context=meta.get("context", ""), questions=qs,
                kind=kind, superseded_by=superseded_by, implements=string_list(meta, "implements", meta_path))


def load_tasks(structure: dict, questions: dict) -> list[Task]:
    tasks = []
    for state in STATES:
        state_dir = PM_DIR / state
        for path in sorted(state_dir.iterdir()) if state_dir.is_dir() else []:
            if path.name == ".gitkeep":
                continue
            need(path.is_dir() and bool(TID_RE.match(path.name)), path,
                 f"only task directories T-NNNN belong in {state}/")
            tasks.append(parse_task(path, state, structure, questions))
    seen: dict[int, Path] = {}
    for task in tasks:
        need(task.number not in seen, task.path, f"{task.task_id} also in {seen.get(task.number)}")
        seen[task.number] = task.path
    check_depends({t.number: t for t in tasks})
    next_task = read_counters()["next_task"]
    too_high = [t.task_id for t in tasks if t.number >= next_task]
    need(not too_high, "pm/counters.json", f"task IDs at or above next_task ({next_task}): {', '.join(too_high)}")
    return tasks


def check_depends(by_number: dict[int, Task]) -> None:
    for task in by_number.values():
        for dep in task.depends:
            need(dep != task.number, task.path, "depends on itself")
            need(dep in by_number, task.path, f"depends on T-{dep:04d}, which has no task directory")
        for new in task.superseded_by:
            need(new in by_number and new != task.number, task.path, f"superseded_by T-{new:04d}: no such task")
    cycle = graph_cycle({n: list(t.depends) for n, t in by_number.items()})
    need(not cycle, "tasks", "dependency cycle, no overview written: " + " -> ".join(f"T-{n:04d}" for n in cycle))


# ---------------------------------------------------------------- derived status

class Index:
    def __init__(self, structure: dict, questions: dict, tasks: list[Task]):
        self.structure = structure
        self.questions = questions
        self.tasks = tasks
        self.by_number = {t.number: t for t in tasks}

    def open_depends(self, task: Task) -> list[Task]:
        return [self.by_number[d] for d in task.depends if self.by_number[d].state != "done"]

    def open_questions(self, task: Task) -> list[str]:
        return [q for q in task.questions if not self.questions[q]["answers"]]

    def status(self, task: Task) -> str:
        if task.state in ("done", "dropped", "backlog"):
            return task.state      # backlog: an idea, never "ready" (moving it to todo is the user's decision)
        if task.state == "wip":
            return "in progress"
        if self.open_depends(task):
            return "blocked"
        if self.open_questions(task):
            return "waiting"
        return "ready"

    def ref(self, task: Task) -> str:
        from pm_render import S        # the coloured status word (rendering; imported here: no import cycle)
        return f"{task.task_id} [{S(self.status(task))}]"

    def needed_by(self, task: Task) -> list[Task]:
        return sorted((t for t in self.tasks if task.number in t.depends), key=lambda t: t.number)

    def asked_by(self, qid: str) -> list[Task]:
        return sorted((t for t in self.tasks if qid in t.questions), key=lambda t: t.number)

    def label(self, task: Task, ungrouped: str = "Ungrouped") -> str:
        """'2.3 Area / Topic', '2.0 Area' or the text for ungrouped tasks (CLI: English; overviews: views.json)."""
        if not task.area:
            return ungrouped
        area = find_area(self.structure, task.area)
        if not task.topic:
            return f"{area['order']}.0 {area['name']}"
        return f"{area['order']}.{find_topic(area, task.topic)['order']} {area['name']} / {task.topic}"

    def sections(self, tasks: list[Task]):
        """Yield (area or None, topic or None, tasks) in N.M order; topic None = area-wide; area None = ungrouped."""
        for area in self.structure["areas"]:
            yield area, None, [t for t in tasks if t.area == area["name"] and not t.topic]
            for topic in area["topics"]:
                yield area, topic, [t for t in tasks if t.area == area["name"] and t.topic == topic["name"]]
        yield None, None, [t for t in tasks if not t.area]


def load_index() -> Index:
    load_language()
    structure = load_structure()
    questions = load_questions()
    ix = Index(structure, questions, load_tasks(structure, questions))
    ix.workflow = load_workflow()
    ix.reqs = load_requirements()
    ix.ecrs = load_ecrs(ix.reqs)
    ix.suspect = suspects(ix.reqs)
    for t in ix.tasks:
        for r in t.implements:
            need(r in ix.reqs, t.path, f"implements unknown requirement {r}")
    ix.waiting = load_waiting()
    for item in ix.waiting["items"]:
        need(not item["task"] or parse_id(item["task"], "waiting") in ix.by_number, WAITING,
             f"waiting item {item['id']}: no task {item['task']}")
    return ix


def graph_cycle(graph: dict) -> list:
    """One cycle of a directed graph {node: [successors]} as a closed path, or [] (iterative DFS)."""
    color: dict = {}            # 1 = on the current path, 2 = finished
    for root in sorted(graph):
        if root in color:
            continue
        path, stack = [root], [iter(graph[root])]
        color[root] = 1
        while stack:
            nxt = next(stack[-1], None)
            if nxt is None:
                color[path.pop()] = 2
                stack.pop()
            elif color.get(nxt) == 1:
                return path[path.index(nxt):] + [nxt]
            elif nxt not in color and nxt in graph:
                color[nxt] = 1
                path.append(nxt)
                stack.append(iter(graph[nxt]))
    return []


def invariants(ix: Index) -> list[str]:
    """Violations: states no command may produce. Every semantic rule of the store is here; `check` fails
    on any of them, and a command that would add one is refused and rolled back (pm/txn.py)."""
    out = []
    for t in ix.tasks:
        deps = [ix.by_number[d] for d in t.depends]
        if t.state == "done":
            bad = [d.task_id for d in deps if d.state not in ("done", "dropped")]
            if bad:
                out.append(f"{t.task_id} is done but depends on unfinished {', '.join(bad)}")
        if t.state == "wip":
            bad = [d.task_id for d in deps if d.state not in ("done", "wip")]
            if bad:
                out.append(f"{t.task_id} is in progress but depends on {', '.join(bad)} (neither done nor in progress)")
    # supersede links: no cycles (two tasks replacing each other)
    cyc = graph_cycle({t.number: list(t.superseded_by) for t in ix.tasks})
    if cyc:
        out.append("supersede cycle: " + " -> ".join(f"T-{n:04d}" for n in cyc))
    for r in ix.reqs.values():
        if r["status"] == "committed":
            drafts = [x["req"] for x in r["refines"] if ix.reqs[x["req"]]["status"] == "draft"]
            if drafts:
                out.append(f"{r['id']} is committed but refines draft {', '.join(drafts)} (commit the parent first)")
        if r["status"] == "sealed":
            open_ = [x["req"] for x in r["refines"] if ix.reqs[x["req"]]["status"] != "sealed"]
            if open_:
                out.append(f"{r['id']} is sealed but refines {', '.join(open_)}, which is not sealed")
    for e in ix.ecrs.values():
        out.extend(e["problems"])
    return out


def followups(ix: Index) -> list[str]:
    """Expected consequences of an approved change that later commands must resolve (e.g. after an ECR
    retired a requirement). They are allowed, reported as warnings, and `check` fails until they are fixed."""
    out = []
    for t in ix.tasks:
        for r in t.implements:
            if ix.reqs[r]["status"] == "retired" and t.state not in ("done", "dropped"):
                out.append(f"{t.task_id} implements retired {r}: supersede the task or change --implements")
    return out


def problem_set() -> set[str]:
    return set(invariants(load_index()))


def req_list(ix: Index, text: str) -> list[str]:
    ids = [x.strip() for x in text.split(",") if x.strip()]
    for r in ids:
        need(r in ix.reqs, "--implements", f"unknown requirement {r} (python3 pm/pm.py spec)")
        need(ix.reqs[r]["status"] != "retired", "--implements", f"{r} is retired")
    return ids


def wid(number: int) -> str:
    """Waiting items are shown as W-NNNN, like the other IDs (stored as a number in pm/waiting.json)."""
    return f"W-{number:04d}"

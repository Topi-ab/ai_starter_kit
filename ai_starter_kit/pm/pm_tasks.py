"""Task store, part of pm/pm.py: commands that change tasks and the shared helpers (guarded writes): new,
edit, move, note, journal, waiting, supersede, stage."""

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
from pm_store import *  # noqa: E402,F401,F403
from pm_render import *  # noqa: E402,F401,F403


# ---------------------------------------------------------------- commands that change JSON

def guarded(changes: list[tuple[Path, object]]) -> None:
    """Write the JSON files, validate the whole store, and restore the old files if it is invalid."""
    old = {path: path.read_text() if path.exists() else None for path, _ in changes}
    for path, data in changes:
        write_json(path, data)
    try:
        load_index()
    except StoreError:
        for path, text in old.items():
            if text is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(text)
        raise


def id_list(text: str) -> list[str]:
    ids = [x.strip() for x in text.split(",") if x.strip()]
    for x in ids:
        parse_id(x, "--depends")
    return ids


def task_by_id(ix: Index, tid: str) -> Task:
    task = ix.by_number.get(parse_id(tid, "task"))
    need(task is not None, "task", f"no task {tid}")
    return task


def stage_rules(ix: Index) -> tuple[dict, dict]:
    stage = current_stage(ix.workflow)
    return stage, stage["rules"]


def cmd_new(a) -> int:
    ix = load_index()
    if a.group:
        split_group(ix.structure, a.group, "--group")
    stage, rules = stage_rules(ix)
    kind = a.kind or rules["task_kinds"][0]
    need(kind in rules["task_kinds"], "new", f"workflow stage {stage['name']!r} allows only "
         f"{', '.join(rules['task_kinds'])} tasks (python3 pm/pm.py stage)")

    title = " ".join(a.title.split())
    plain_text(title, "new", "title")
    meta: dict = {"title": title}
    if kind != "work":
        meta["kind"] = kind
    if a.group:
        meta["group"] = a.group
    if a.phase:
        meta["phase"] = a.phase
    meta["context"] = a.context or ""
    if a.implements:
        meta["implements"] = req_list(ix, a.implements)
    meta["depends"] = id_list(a.depends) if a.depends else []
    for d in meta["depends"]:
        task_by_id(ix, d)
    need(bool(a.description and a.description.strip()), "new", "needs --description (what and why)")
    need(bool(a.done_when and a.done_when.strip()), "new", "needs --done-when (a concrete, checkable criterion)")
    values = {"{now}": now(), "{request}": " ".join((a.request or "").split()),
              "{description}": " ".join(a.description.split()), "{done_when}": " ".join(a.done_when.split())}
    lines = []
    for line in load_views(GENERATED_FILES)["task_template"]:
        fields = [f for f in values if f in line and f != "{now}"]
        if fields and not any(values[f] for f in fields):
            continue                    # e.g. no --request: leave the "Requested by the user" line out
        for f, v in values.items():
            line = line.replace(f, v)
        lines.append(line)
    number = take_number("next_task")
    tid = f"T-{number:04d}"
    task_dir = PM_DIR / a.state / tid
    need(not any((PM_DIR / st / tid).exists() for st in STATES), "new", f"{tid} exists already "
         "(pm/counters.json next_task is too low)")
    task_dir.mkdir(parents=True)
    (task_dir / TEXT).write_text("".join(line + "\n" for line in lines))
    try:
        guarded([(task_dir / META, {"id": tid, **meta})])
    except StoreError:
        (task_dir / TEXT).unlink()
        task_dir.rmdir()
        raise
    print(f"{tid} created: {task_dir.relative_to(REPO_DIR)}")
    return cmd_build()


def cmd_edit(a) -> int:
    ix = load_index()
    task = task_by_id(ix, a.task)
    meta_path = task.path / META
    meta = read_json(meta_path)
    for key in ("title", "group", "phase", "context"):
        value = getattr(a, key)
        if value is not None:
            if value == "" and key != "title":
                meta.pop(key, None) if key != "context" else meta.update(context="")
            else:
                meta[key] = value
    if a.kind is not None:
        stage, rules = stage_rules(ix)
        need(a.kind in rules["task_kinds"], "edit", f"workflow stage {stage['name']!r} allows only "
             f"{', '.join(rules['task_kinds'])} tasks")
        meta.pop("kind", None)
        if a.kind != "work":
            meta["kind"] = a.kind
    if a.implements is not None:
        meta.pop("implements", None)
        if a.implements:
            meta["implements"] = req_list(ix, a.implements)
    if a.order is not None:
        meta["order"] = a.order
    if a.depends is not None:
        meta["depends"] = id_list(a.depends)
    if a.clear_needs:
        meta.pop("needs", None)
    if a.needs:
        meta["needs"] = meta.get("needs", []) + a.needs
    guarded([(meta_path, meta)])
    print(f"{task.task_id} updated")
    return cmd_build()


def move_dir(task: Task, state: str) -> Path:
    target = PM_DIR / state / task.path.name
    target.parent.mkdir(exist_ok=True)
    need(not target.exists(), "move", f"{target.relative_to(REPO_DIR)} exists already")
    task.path.rename(target)      # a plain rename: git sees it as a rename when the change is committed
    return target


def append_line(task_dir: Path, line: str) -> None:
    text = (task_dir / TEXT).read_text().rstrip("\n")
    (task_dir / TEXT).write_text(f"{text}\n{line}\n")


def add_note(task_dir: Path, kind: str, text: str = "") -> None:
    """Append '- **<Label> <time>:** <text>' (label from views.json note_labels)."""
    label = load_views(GENERATED_FILES)["note_labels"][kind]
    text = " ".join(text.split())
    append_line(task_dir, f"- **{label} {now()}:** {text}" if text else f"- **{label}:** {now()}")


def ready_ids() -> set[str]:
    ix = load_index()
    return {t.task_id for t in ix.tasks if ix.status(t) == "ready"}


def report_ready(before: set[str]) -> None:
    after = ready_ids()
    new = sorted(after - before)
    print("now ready: " + (", ".join(new) if new else "(no new ready tasks)")
          + f"; all ready: {', '.join(sorted(after)) or '(none)'}")


TRANSITIONS = {("backlog", "todo"), ("todo", "wip"), ("wip", "done"), ("wip", "todo"), ("todo", "backlog"),
               ("backlog", "dropped"), ("todo", "dropped"), ("wip", "dropped")}


def cmd_move(a) -> int:
    ix = load_index()
    need(len(a.args) >= 2, "move", "usage: move T-NNNN [T-NNNN ...] STATE")
    *ids, state = a.args
    need(state in STATES, "move", f"STATE must be one of {', '.join(STATES)}")
    need(len(set(ids)) == len(ids), "move", "a task is named twice")
    reason = " ".join((a.reason or "").split())
    tasks = [task_by_id(ix, tid) for tid in ids]
    batch = {t.number for t in tasks}
    stage, rules = stage_rules(ix)
    if state in ("wip", "done"):
        need(rules["start_work"], "move", f"workflow stage {stage['name']!r} does not allow starting work; "
             "agree the scope with the user, then python3 pm/pm.py stage advance --approval ...")
    for task in tasks:
        need(task.state != state, "move", f"{task.task_id} is already in {state}/")
        if (task.state, state) not in TRANSITIONS:
            need(a.force and bool(reason), "move", f"{task.task_id}: {task.state} -> {state} is not a normal "
                 "transition (" + ", ".join(f"{x}->{y}" for x, y in sorted(TRANSITIONS)) + "); use --force with "
                 "--reason if it is really needed")
        if a.force:
            need(bool(reason), "move", "--force needs --reason")
            continue
        if state == "wip":
            blocking = [d.task_id for d in ix.open_depends(task) if d.state != "wip" and d.number not in batch]
            need(not blocking, "move", f"{task.task_id} depends on {', '.join(blocking)}, neither done nor in "
                 "progress (start them together: move ... wip; or --force)")
        if state == "done":
            blocking = [d.task_id for d in ix.open_depends(task) if d.number not in batch]
            need(not blocking, "move", f"{task.task_id} depends on {', '.join(blocking)}, not done yet "
                 "(finish them in the same move, or --force)")
        if state == "done":
            stale = [r for r in task.implements if r in ix.suspect]
            need(not stale, "move", f"{task.task_id} implements suspect requirements {', '.join(stale)} "
                 "(a parent changed): review them first (pm.py spec suspect), or --force")
        if state == "done" and ix.open_questions(task):
            print(f"warning: {task.task_id} still has open questions {', '.join(ix.open_questions(task))}",
                  file=sys.stderr)
    need(state != "dropped" or bool(reason), "move", "moving to dropped/ needs --reason")
    before = ready_ids()
    for task in tasks:
        target = move_dir(task, state)
        if state == "dropped":
            add_note(target, "dropped", reason)
        elif (task.state, state) not in TRANSITIONS:
            add_note(target, "moved", f"{task.state} -> {state} (forced): {reason}")
        elif state == "wip":
            add_note(target, "started", reason)
        elif state == "done":
            add_note(target, "done", reason)
        elif reason:
            add_note(target, "moved", f"-> {state}: {reason}")
        print(f"{task.task_id}: {task.state} -> {state}")
    cmd_build()
    report_ready(before)
    return 0




def cmd_note(a) -> int:
    ix = load_index()
    task = task_by_id(ix, a.task)
    need(bool(a.text.strip()), "note", "needs the text")
    add_note(task.path, a.kind, a.text)
    print(f"{task.task_id}: {a.kind} noted")
    return cmd_build()


JOURNAL = REPO_DIR / "JOURNAL.md"


def cmd_journal(a) -> int:
    """Add '- **HH:MM** text' at the top of today's '## YYYY-MM-DD' section of JOURNAL.md (newest first),
    with the time taken here, so no time stamp is guessed. A new day gets a new section above the others."""
    text = " ".join(a.text.split())
    need(bool(text), "journal", 'usage: journal "TEXT"')
    need(JOURNAL.exists(), "journal", f"no {JOURNAL.relative_to(REPO_DIR)}")
    day, hm = now().split(" ")
    lines = JOURNAL.read_text().splitlines()
    entry = f"- **{hm}** {text}"
    heading = f"## {day}"
    if heading in lines:
        i = lines.index(heading) + 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        lines[i:i] = [entry]
    else:
        first = next((i for i, line in enumerate(lines) if re.match(r"## \d{4}-\d{2}-\d{2}$", line)), len(lines))
        while first > 0 and not lines[first - 1].strip():
            first -= 1
        lines[first:first] = ["", heading, "", entry] + ([""] if first < len(lines) and lines[first].strip() else [])
    tmp = JOURNAL.with_suffix(".md.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(JOURNAL)
    print(f"{JOURNAL.relative_to(REPO_DIR)}: {day} {hm} entry added")
    return 0




def cmd_waiting(a) -> int:
    ix = load_index()
    data = ix.waiting
    if a.action == "add":
        need(len(a.args) == 1 and a.args[0].strip() != "", "waiting add", 'usage: waiting add "TEXT" [--task T-NNNN]')
        if a.task:
            task_by_id(ix, a.task)
        plain_text(" ".join(a.args[0].split()), "waiting add", "text")
        data["items"].append({"id": data["next"], "time": now(), "text": " ".join(a.args[0].split()),
                              "task": a.task or ""})
        data["next"] += 1
    elif a.action == "done":
        try:
            ids = {int(re.sub(r"^[Ww]-?", "", x)) for x in a.args}
        except ValueError:
            ids = set()
        need(bool(ids), "waiting done", "usage: waiting done W-NNNN [W-NNNN ...]")
        known = {item["id"] for item in data["items"]}
        need(ids <= known, "waiting done", f"no waiting item {', '.join(wid(i) for i in sorted(ids - known))}")
        data["items"] = [item for item in data["items"] if item["id"] not in ids]
    if a.action in ("add", "done"):
        guarded([(WAITING, data)])
        cmd_build()
    for item in data["items"]:
        task = f" ({item['task']})" if item["task"] else ""
        print(f"{wid(item['id'])}  {item['time']}  {item['text']}{task}")
    if not data["items"]:
        print("nothing is waiting for the user (besides numbered questions: question.py open)")
    return 0


def cmd_supersede(a) -> int:
    ix = load_index()
    old = task_by_id(ix, a.task)
    new_ids = id_list(a.by)
    need(bool(new_ids), "supersede", "--by needs at least one task ID")
    new = [task_by_id(ix, n) for n in new_ids]
    need(old.number not in {t.number for t in new}, "supersede", "a task cannot supersede itself")
    need(bool(a.reason and a.reason.strip()), "supersede", "needs --reason (e.g. the decision: Q-0011 a)")
    meta = read_json(old.path / META)
    meta["superseded_by"] = sorted(set(meta.get("superseded_by", [])) | {t.task_id for t in new})
    guarded([(old.path / META, meta)])
    before = ready_ids()
    reason = " ".join(a.reason.split())
    target = old.path
    if old.state not in ("done", "dropped"):
        target = move_dir(old, "dropped")
        print(f"{old.task_id}: {old.state} -> dropped")
    add_note(target, "superseded", f"by {', '.join(t.task_id for t in new)}: {reason}")
    print(f"{old.task_id} superseded by {', '.join(t.task_id for t in new)}")
    cmd_build()
    ix = load_index()
    dependents = [t for t in ix.tasks if old.number in t.depends and t.state not in ("done", "dropped")]
    if dependents:
        print("still depending on " + old.task_id + " (change their depends to the new tasks with "
              "pm.py edit T-NNNN --depends ...): " + ", ".join(t.task_id for t in dependents))
    report_ready(before)
    return 0


def cmd_stage(a) -> int:
    wf = load_workflow()
    ids = [s["id"] for s in wf["stages"]]
    pos = ids.index(wf["current"])
    if a.action == "advance":
        need(pos + 1 < len(ids), "stage advance", f"{wf['stages'][pos]['name']!r} is the last stage")
        need(bool(a.approval and a.approval.strip()), "stage advance",
             '--approval "<the user\'s words>" is required: the user decides when a stage is complete')
        wf["history"].append({"from": ids[pos], "to": ids[pos + 1], "time": now(),
                              "approval": " ".join(a.approval.split())})
        wf["current"] = ids[pos + 1]
        old = WORKFLOW.read_text()
        write_json(WORKFLOW, wf)
        try:
            load_workflow()
        except StoreError:
            WORKFLOW.write_text(old)
            raise
        cmd_build()
        pos += 1
    for i, stage in enumerate(wf["stages"]):
        mark = "->" if i == pos else ("ok" if i < pos else "  ")
        r = stage["rules"]
        print(f"{mark} {i + 1}. {stage['name']} [{stage['id']}]: tasks {', '.join(r['task_kinds'])}; "
              f"start work: {'yes' if r['start_work'] else 'no'}")
        print(f"      {stage['description']}")
    for step in wf["history"]:
        print(f"history: {step['time']} {step['from']} -> {step['to']}: \"{step['approval']}\"")
    return 0

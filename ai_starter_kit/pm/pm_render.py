"""Task store, part of pm/pm.py: the generated overviews (TODO.md ... SPEC.md) and the read-only commands
(build, check, list, json)."""

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


# ---------------------------------------------------------------- rendering

SPEC_FILE = "SPEC.md"
GENERATED_FILES = list(STATES.values()) + [ALL_FILE] + list(QUESTIONS_FILES.values()) + [SPEC_FILE]
VIEW_TEXTS: dict = {}          # views.json, loaded by outputs() before rendering


def L(key: str) -> str:
    """A fixed text of the generated files, from views.json labels."""
    return VIEW_TEXTS["labels"][key]


def colour(word: str, status: str) -> str:
    """A status word in its colour (views.json status_colors, color_style); the brackets stay plain."""
    style, col = VIEW_TEXTS["color_style"], VIEW_TEXTS["status_colors"][status]
    if style == "latex":           # $\color{..}{..}$: rendered by VS Code's preview and by GitHub
        esc = "".join("\\" + c if c in "&%$#_{}" else c for c in word).replace("~", "\\textasciitilde{}")
        return f"$\\color{{{col}}}{{\\textsf{{{esc}}}}}$"
    if style == "html":            # VS Code's preview; GitHub drops the style
        return f'<span style="color:{col}">{html.escape(word)}</span>'
    return word


def S(status: str) -> str:
    """A status as shown to the user: the word from views.json status, in its colour."""
    return colour(VIEW_TEXTS["status"][status], status)


def header(filename: str) -> list[str]:
    """Agent note (HTML comment), then the user's title, intro paragraphs and links to the other files."""
    views = VIEW_TEXTS
    view = views["files"][filename]
    links = " · ".join(f"[{views['files'][f]['title']}]({f})" for f in GENERATED_FILES if f != filename)
    out = [f"<!-- {views['agent_note'].strip()} -->", f"# {view['title']}", ""]
    for paragraph in view["intro"]:
        out += [paragraph, ""]
    wf = load_workflow()
    stages = wf["stages"]
    pos = [s["id"] for s in stages].index(wf["current"])
    out += [f"**{L('workflow_stage')}:** {stages[pos]['name']} ({pos + 1}/{len(stages)})", ""]
    return out + [links, ""]


def dependency_lines(task: Task, ix: Index) -> list[str]:
    lines = [f"- **{L('meta_task')}**"] if task.kind == "meta" else []
    if task.superseded_by:
        lines.append(f"- **{L('superseded_by')}:** {', '.join(ix.ref(ix.by_number[n]) for n in task.superseded_by)}.")
    if task.context:
        lines.append(f"- **{L('context')}:** {task.context}")
    if task.implements:
        lines.append(f"- **{L('implements')}:** " + ", ".join(
            f"{r} {ix.reqs[r]['title']} [{ix.reqs[r]['status']}]" for r in task.implements) + ".")
    if task.phase:
        lines.append(f"- **{L('phase')}:** {task.phase}")
    deps = ", ".join(ix.ref(ix.by_number[d]) for d in task.depends) or L("none")
    lines.append(f"- **{L('depends_on')}:** {deps}.")
    if task.state not in ("done", "dropped"):
        waits = [t.task_id for t in ix.open_depends(task)] + ix.open_questions(task)
        if waits:
            lines.append(f"- **{L('waits_for')}:** {', '.join(waits)}.")
    answered = [q for q in task.questions if q not in ix.open_questions(task)]
    if answered:
        closed = QUESTIONS_FILES["closed"]
        lines.append(f"- **{L('answered_questions')}:** {', '.join(answered)} ({L('see')} [{closed}]({closed})).")
    if task.needs:
        lines.append(f"- **{L('also_needs')}:** {'; '.join(task.needs)}.")
    users = ix.needed_by(task)
    if users:
        lines.append(f"- **{L('needed_by')}:** {', '.join(ix.ref(t) for t in users)}.")
    return lines


def by_order(tasks: list[Task]) -> list[Task]:
    return sorted(tasks, key=lambda t: (t.order, t.number))


def section_heading(area: dict | None, topic: dict | None, level: str) -> list[str]:
    if area is None:
        return [f"{level} {L('ungrouped')}"]
    if topic is None:
        return [f"{level} {area['order']}.0 {area['name']} ({L('area_wide')})"]
    return [f"{level} {area['order']}.{topic['order']} {topic['name']}"] + (
        [f"*{topic['description']}*"] if topic["description"] else [])


def area_heading(area: dict | None) -> list[str]:
    if area is None:
        return [f"## {L('ungrouped')}", ""]
    return [f"## {area['order']} {area['name']}", ""] + ([f"*{area['description']}*", ""] if area["description"]
                                                         else [])


def grouped(ix: Index, tasks: list[Task], render_section) -> list[str]:
    """Area headings (##), section headings (###), then render_section(tasks) for each non-empty section."""
    out: list[str] = []
    shown_area = object()
    for area, topic, selected in ix.sections(tasks):
        if not selected:
            continue
        if area is not shown_area:
            out += area_heading(area)
            shown_area = area
        if area is not None:
            out += section_heading(area, topic, "###") + [""]
        out += render_section(selected)
    return out


def render_state(state: str, ix: Index) -> str:
    out = header(STATES[state])
    selected = [t for t in ix.tasks if t.state == state]
    if state == "todo":
        ready = [t for t in by_order(selected) if ix.status(t) == "ready"][:NEXT_UP]
        out += [f"## {L('next_up')}", ""]
        out += ([f"- {t.task_id} {t.title} [{S('ready')}] ({ix.label(t, L('ungrouped'))})" for t in ready]
                or [L("no_ready_task")])
        out.append("")

    def section(tasks: list[Task]) -> list[str]:
        lines = []
        for t in by_order(tasks):
            lines += [f"#### {t.task_id} {t.title} [{S(ix.status(t))}]", *dependency_lines(t, ix), "", t.body, ""]
        return lines

    out += grouped(ix, selected, section) if selected else [L("nothing_here"), ""]
    return "\n".join(out)


def count_row(ix: Index, label: str, tasks: list[Task]) -> str:
    def n(pred) -> str:
        k = sum(1 for t in tasks if pred(t))
        return str(k) if k else "·"
    cells = [n(lambda t, s=s: t.state in ("todo", "wip") and ix.status(t) == s)
             for s in ("ready", "waiting", "blocked", "in progress")]
    cells += [n(lambda t: t.state == "backlog"), n(lambda t: t.state == "done"), n(lambda t: t.state == "dropped")]
    return f"| {label} | " + " | ".join(cells) + " |"


def render_all(ix: Index) -> str:
    """Every task, by area N and topic N.M; one line per task; a summary table first."""
    out = header(ALL_FILE)
    columns = [S(x) for x in ("ready", "waiting", "blocked", "in progress")] + [L("backlog"), S("done"), S("dropped")]
    out += [f"## {L('summary')}", "", L("summary_caption"), "",
            f"| {L('area_topic')} | " + " | ".join(columns) + " |",
            "|---" * (len(columns) + 1) + "|"]
    for area in ix.structure["areas"]:
        in_area = [t for t in ix.tasks if t.area == area["name"]]
        out.append(count_row(ix, f"**{area['order']} {area['name']}**", in_area))
        wide = [t for t in in_area if not t.topic]
        if wide:
            out.append(count_row(ix, f"{area['order']}.0 ({L('area_wide')})", wide))
        for topic in area["topics"]:
            out.append(count_row(ix, f"{area['order']}.{topic['order']} {topic['name']}",
                                 [t for t in in_area if t.topic == topic["name"]]))
    ungrouped = [t for t in ix.tasks if not t.area]
    if ungrouped:
        out.append(count_row(ix, L("ungrouped"), ungrouped))
    out += [count_row(ix, f"**{L('all')}**", ix.tasks), ""]

    def section(tasks: list[Task]) -> list[str]:
        lines = []
        for t in sorted(tasks, key=lambda t: (STATE_ORDER.index(t.state), t.order, t.number)):
            notes = []
            if t.kind == "meta":
                notes.append(L("meta_task"))
            if t.phase:
                notes.append(t.phase)
            if t.superseded_by:
                notes.append(f"{L('superseded_by')} " + ", ".join(f"T-{n:04d}" for n in t.superseded_by))
            if t.state not in ("done", "dropped"):
                waits = [d.task_id for d in ix.open_depends(t)] + ix.open_questions(t)
                if waits:
                    notes.append(f"{L('waits_for')}: " + ", ".join(waits))
            tail = f" — {'; '.join(notes)}" if notes else ""
            where = STATES[t.state]
            lines.append(f"- **{t.task_id}** [{S(ix.status(t))}] {t.title} "
                         f"([{VIEW_TEXTS['files'][where]['title']}]({where})){tail}")
        return lines + [""]

    # Empty topics are listed too, so the whole structure is visible.
    body: list[str] = []
    for area in ix.structure["areas"]:
        body += area_heading(area)
        wide = [t for t in ix.tasks if t.area == area["name"] and not t.topic]
        if wide:
            body += section_heading(area, None, "###") + [""] + section(wide)
        for topic in area["topics"]:
            selected = [t for t in ix.tasks if t.area == area["name"] and t.topic == topic["name"]]
            body += section_heading(area, topic, "###") + [""]
            body += section(selected) if selected else [L("no_tasks"), ""]
    if ungrouped:
        body += area_heading(None) + section(ungrouped)
    out += body or [L("no_tasks_no_areas"), ""]
    return "\n".join(out)


def render_questions(which: str, ix: Index) -> str:
    """QUESTIONS_OPEN.md (oldest first) or QUESTIONS_CLOSED.md (newest first)."""
    out = header(QUESTIONS_FILES[which])
    if which == "open" and ix.waiting["items"]:
        out += [f"## {L('waiting_items')}", ""]
        for item in ix.waiting["items"]:
            task = f" ({ix.ref(ix.by_number[parse_id(item['task'], 'waiting')])})" if item["task"] else ""
            out.append(f"- **{wid(item['id'])}** ({item['time']}) {item['text']}{task}")
        out.append("")
    if which == "open":
        selected = [q for q in ix.questions.values() if not q["answers"]]
    else:
        selected = [q for q in reversed(list(ix.questions.values())) if q["answers"]]
    for q in selected:
        first, *rest = q["text"].splitlines()
        out += [f"## {q['id']} ({q['asked']}) {first}", ""] + rest + ([""] if rest else [])
        for opt in q["options"]:
            rec = f" ({L('recommended')})" if opt.get("recommended") else ""
            out.append(f"- **{opt['key']})**{rec} {opt['text']}")
        if q["options"]:
            out.append("")
        tasks = ", ".join(ix.ref(t) for t in ix.asked_by(q["id"]))
        if tasks:
            out += [f"{L('tasks')}: {tasks}.", ""]
        for ans in q["answers"]:
            note = f": {ans['note']}" if ans.get("note") else ""
            out.append(f"**{L('answer')} {ans['time']}: {ans['choice']}{note}**  ")
        if q["answers"]:
            out.append("")
    if not selected:
        out += [L("nothing_here"), ""]
    return "\n".join(out)


def ecr_impact(ix: Index, ecr: dict) -> list[Task]:
    """Tasks that implement a requirement the ECR changes or retires, plus the tasks depending on them."""
    reqs = {a["req"] for a in ecr["actions"] if a["action"] in ("change", "retire")}
    reqs |= set(descendants(ix.reqs, reqs))
    hit = {t.number for t in ix.tasks if set(t.implements) & reqs}
    while True:
        more = {t.number for t in ix.tasks if set(t.depends) & hit} - hit
        if not more:
            break
        hit |= more
    return sorted((ix.by_number[n] for n in hit), key=lambda t: t.number)


def render_spec(ix: Index) -> str:
    out = header(SPEC_FILE)
    users = {r: [t for t in ix.tasks if r in t.implements] for r in ix.reqs}
    for status, key in (("sealed", "spec_sealed"), ("committed", "spec_committed"), ("draft", "spec_draft"),
                        ("retired", "spec_retired")):
        selected = [r for r in ix.reqs.values() if r["status"] == status]
        out += [f"## {L(key)}", ""]
        for r in selected:
            out += [f"### {r['id']} {r['title']} ({L('version')} {r['version']})", ""]
            if r["source"]:
                out += [f"*{L('source')}: {r['source']}*", ""]
            if r["refines"]:
                out += [f"*{L('refines')}: " + ", ".join(f"{x['req']} v{x['version']}" for x in r["refines"]) + "*", ""]
            if r["id"] in ix.suspect:
                out += [f"**{L('suspect')}**", ""]
            out += [r["text"].strip(), ""]
            if users[r["id"]]:
                out += [f"{L('tasks')}: " + ", ".join(ix.ref(t) for t in users[r["id"]]) + ".", ""]
        if not selected:
            out += [L("nothing_here"), ""]
    active = [e for e in ix.ecrs.values() if e["state"] in ("open", "approved")]
    out += [f"## {L('ecr_heading')}", ""]
    for e in active:
        out += [f"### {e['id']} {e['title']} [{e['state']}]", "", (e["path"] / "ecr.md").read_text().strip(), "",
                f"**{L('ecr_actions')}:**", ""]
        for a in e["actions"]:
            target = a["req"] + " " + ix.reqs[a["req"]]["title"] if a["req"] else a["title"]
            out.append(f"- {a['action']}: {target}")
        impact = ecr_impact(ix, e)
        out += ["", f"**{L('ecr_impact')}:** " + (", ".join(ix.ref(t) for t in impact) or L("none")), ""]
    if not active:
        out += [L("nothing_here"), ""]
    return "\n".join(out)


def outputs(ix: Index) -> dict[Path, str]:
    VIEW_TEXTS.clear()
    VIEW_TEXTS.update(load_views(GENERATED_FILES))
    out = {REPO_DIR / name: render_state(state, ix) for state, name in STATES.items()}
    out[REPO_DIR / ALL_FILE] = render_all(ix)
    for which, name in QUESTIONS_FILES.items():
        out[REPO_DIR / name] = render_questions(which, ix)
    out[REPO_DIR / SPEC_FILE] = render_spec(ix)
    out[background.OUTPUT] = background.render()     # pm/RUN_IN_BACKGROUND.md (for the agent, no view texts)
    return out


# ---------------------------------------------------------------- commands: build, check, list, json

def cmd_build(a=None) -> int:
    ix = load_index()
    for problem in invariants(ix) + followups(ix):
        print(f"warning: {problem}", file=sys.stderr)
    for path, text in outputs(ix).items():
        if not path.exists() or path.read_text() != text:
            path.write_text(text)
            print(f"updated {path.name}")
    return 0


def cmd_check(a=None) -> int:
    ix = load_index()
    problems = invariants(ix) + followups(ix)
    if problems:
        print("\n".join(f"error: {p}" for p in problems), file=sys.stderr)
        return 1
    stale = [p.name for p, text in outputs(ix).items() if not p.exists() or p.read_text() != text]
    if stale:
        print(f"out of date (run python3 pm/pm.py): {', '.join(stale)}", file=sys.stderr)
        return 1
    print("tasks ok")
    return 0


def in_group(task: Task, group: str | None) -> bool:
    return not group or task.group == group or task.group.startswith(group + "/")


def cmd_list(a) -> int:
    ix = load_index()
    if a.group:
        split_group(ix.structure, a.group, "--group")
    wanted = a.status.replace("-", " ") if a.status else None
    for task in sorted(ix.tasks, key=lambda t: (STATE_ORDER.index(t.state), t.order, t.number)):
        st = ix.status(task)
        if (wanted and st != wanted) or not in_group(task, a.group) or (a.phase and task.phase != a.phase):
            continue
        waits = [t.task_id for t in ix.open_depends(task)] + ix.open_questions(task)
        note = f"  waits for {', '.join(waits)}" if waits and st not in ("done", "dropped") else ""
        if st == "ready" and task.needs:
            note += "  also needs: " + "; ".join(task.needs)
        kind = " [meta]" if task.kind == "meta" else ""
        print(f"{task.task_id}  {st:<11}  {task.state:<7}  {ix.label(task):<30}  {task.title}{kind}{note}")
    return 0


def cmd_json(a) -> int:
    ix = load_index()
    out = [{"id": t.task_id, "title": t.title, "state": t.state, "status": ix.status(t), "group": t.group,
            "label": ix.label(t), "kind": t.kind, "phase": t.phase, "order": t.order, "context": t.context,
            "superseded_by": [f"T-{n:04d}" for n in t.superseded_by],
            "depends": [f"T-{d:04d}" for d in t.depends], "waits_for": [w.task_id for w in ix.open_depends(t)],
            "questions": t.questions, "open_questions": ix.open_questions(t), "needs": t.needs,
            "needed_by": [u.task_id for u in ix.needed_by(t)], "path": str(t.path.relative_to(REPO_DIR))}
           for t in sorted(ix.tasks, key=lambda t: t.number)]
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0

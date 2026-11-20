#!/usr/bin/env python3
"""Task store: one directory per task; the overviews at the repository root are generated from it.

Everything this script reads is JSON (meta.json, structure.json, counters.json, views.json,
questions/*.json) or free text (task.md). Change the JSON with the commands below rather than by hand.

    pm/<state>/T-NNNN/meta.json   the task's metadata; <state> is backlog, todo, wip, done or dropped
    pm/<state>/T-NNNN/task.md     the task's text (markdown, no heading: the overview adds it)
    pm/<state>/T-NNNN/files/      optional attachments (not rendered)
    pm/structure.json             areas N with topics N.M, and phases
    pm/counters.json              the next free task and question numbers
    pm/questions/Q-NNNN.json      questions to the user (written by pm/question.py)
    pm/views.json                 title and intro text of each generated file (shown to the user)
    pm/language.json              languages for code, documentation, task files and chat

Generated for the user, never edit (and agents do not read them: use list/json): TODO.md WIP.md DONE.md
BACKLOG.md DROPPED.md (one state each, grouped by area), ALL_TASKS.md (every task by area N and topic N.M,
with a summary table), QUESTIONS_OPEN.md, QUESTIONS_CLOSED.md. Each starts with the agent note from
views.json as an HTML comment; everything after it is for the user.

Commands:
    pm.py                                    regenerate the overviews
    pm.py check                              validate; fail if an overview is out of date
    pm.py new <state> TITLE --description D --done-when W [--request "<user's words>"] [--group G]
                 [--phase P] [--depends T-1,T-2] [--context C] [--kind meta|work]
    pm.py note T-NNNN TEXT [--kind status|decision|result|not_done|correction|note]
                                                append a dated entry to task.md (the only way to write it after
                                                creation); move adds Started/Done/Dropped entries itself
    pm.py journal TEXT                          add "- **HH:MM** TEXT" under today's section of JOURNAL.md (newest
                                                first), with the time taken by the script
    pm.py waiting [add TEXT [--task T-NNNN] | done W-NNNN [W-NNNN ...]]
                                                plain chat questions to the user that are not answered yet
    pm.py edit T-NNNN [--title] [--group] [--phase] [--context] [--order N] [--depends IDS] [--needs X]...
                                                change meta.json fields ("" clears; --depends "" = none)
    pm.py move T-NNNN [T-NNNN ...] <state> [--reason TEXT] [--force]
                                                wip: every unfinished dependency must be in wip or in the
                                                same move; done: every dependency done or in the same move.
                                                Prints the tasks that became ready.
    pm.py supersede T-OLD --by T-NEW[,T-NEW2] --reason TEXT
                                                spec change: T-OLD is replaced; a done task stays done, an
                                                unfinished one moves to dropped; prints tasks still depending on it
    pm.py spec [list] | spec show R-NNNN | spec new TITLE --text T [--source S] [--refines R-1,R-2]
    pm.py spec link R-NNNN --refines R-1,R-2    set the parents of a draft requirement
    pm.py spec suspect                          requirements to review after a parent changed, and their tasks
    pm.py spec review R-NNNN [R-NNNN ...] --approval "<the user's words>"   the text still holds (parents first)
    pm.py spec retitle R-NNNN "Title"           rename a draft (a committed one: ecr change ... --title)
    pm.py spec commit R-NNNN [R-NNNN ...] --approval "<the user's words>"     lock agreed requirements
    pm.py spec seal R-NNNN [R-NNNN ...] --approval "<the user's words>"       seal committed requirements: no
                                                command changes them any more (they refine only sealed ones)
    pm.py spec reseal R-NNNN [--new-version] [--title T]   THE USER only, at a terminal, after their hand edit
                                                of text.md: records the hash (and a new version), updates the
                                                links, sets a new title
    pm.py ecr [list] | ecr show ECR-NNNN | ecr new TITLE --reason R
    pm.py ecr change ECR-NNNN R-NNNN [--text T] [--title T] | ecr retire ECR-NNNN R-NNNN
    pm.py ecr add ECR-NNNN TITLE --text T [--refines R-1]
    pm.py ecr approve|reject ECR-NNNN --approval "<the user's words>" | ecr apply ECR-NNNN
                                                requirements and change requests (pm/speclib.py)
    pm.py stage                              show the workflow stages and the current one
    pm.py stage advance --approval "<the user's words>"   go to the next stage (forward only)
    pm.py list [STATUS] [--group G] [--phase P]
    pm.py json
    pm.py group list
    pm.py group add AREA[/TOPIC] [--order N] [--description D]
    pm.py group rename OLD NEW               AREA -> AREA2, AREA/TOPIC -> AREA2/TOPIC2 (moves the topic),
                                                or AREA/TOPIC -> TOPIC2 (renames the topic in its area)
    pm.py group order AREA[/TOPIC] N         change an order number
    pm.py group describe AREA[/TOPIC] TEXT   change a description
    pm.py group remove AREA[/TOPIC]          only when no task uses it
    pm.py phase list | phase add NAME [--description D] | phase rename OLD NEW
    pm.py language                           show the language settings
    pm.py language set KEY LANGUAGE          KEY: code, docs, task_files, chat (only on the user's request)
    pm.py view                               show the title and intro of each generated file
    pm.py view set FILE [--title T] [--intro PARAGRAPH]...    replace them (--intro repeated = paragraphs)
    pm.py view note TEXT                     replace the agent note at the top of every generated file
    pm.py view label KEY TEXT                a fixed text of the generated files (headings, field names)
    pm.py view status STATUS TEXT            how a status is shown to the user (ready, waiting, ...)
    pm.py view template LINE...              the starting text of a new task.md ({now} = the time)

meta.json:
    {
      "id": "T-0010",
      "title": "Short imperative title",
      "group": "Area/Topic",            optional: "Area" (area-wide) or "Area/Topic", defined in structure.json
      "phase": "Phase 1",               optional: a phase defined in structure.json (shown as a tag)
      "order": 20,                      optional: position within its topic (default: the ID number)
      "context": "What this is part of and why (facts, other tasks by ID)",   optional, shown first
      "depends": ["T-0009"],            required: tasks that must be done first ([] = none)
      "needs": ["a shared machine"],    optional: prerequisites that are not tasks (shown, not tracked)
      "questions": ["Q-0004"],          optional: questions to the user this task waits for
      "kind": "meta",                   optional: "meta" (a deliverable or epic, done when the tasks it
                                        depends on are done) or "work" (default)
      "superseded_by": ["T-0014"],      set by `supersede`: the tasks that replace this one
      "implements": ["R-0003"]          optional: the requirements this task implements
    }

Workflow stages (pm/workflow.json) are passed forward only. Each stage's rules say which task kinds
may be created and whether work may start (move to wip/done). The kit starts with "scope" (scope
analysis: only meta tasks, no work started) and "build" (implementation).

Status is derived, never stored: done, dropped (does NOT satisfy a dependency), in progress (wip/),
blocked (a dependency not done), waiting (an open question), ready.
"""

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
from pm_tasks import *  # noqa: E402,F401,F403
from pm_spec import *  # noqa: E402,F401,F403
from pm_admin import *  # noqa: E402,F401,F403


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pm/pm.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("check").set_defaults(func=cmd_check)
    p = sub.add_parser("new")
    p.add_argument("state", choices=["backlog", "todo"], help="new tasks start as an idea or as decided work")
    p.add_argument("title")
    p.add_argument("--group")
    p.add_argument("--phase")
    p.add_argument("--depends", help="comma-separated task IDs")
    p.add_argument("--context")
    p.add_argument("--kind", choices=list(TASK_KINDS))
    p.add_argument("--description", help="what and why")
    p.add_argument("--done-when", dest="done_when", help="a concrete, checkable criterion")
    p.add_argument("--request", help="the user's words that caused the task (in their language)")
    p.add_argument("--implements", help="comma-separated requirement IDs")
    p.set_defaults(func=cmd_new)
    p = sub.add_parser("spec")
    p.add_argument("action", nargs="?",
                   choices=["list", "show", "new", "commit", "seal", "reseal", "link", "suspect", "review",
                            "retitle"],
                   default="list")
    p.add_argument("--refines", help="comma-separated parent requirement IDs")
    p.add_argument("args", nargs="*")
    p.add_argument("--text")
    p.add_argument("--source")
    p.add_argument("--approval")
    p.add_argument("--title", help="reseal: a new title for the sealed requirement (the user's tool)")
    p.add_argument("--new-version", dest="new_version", action="store_true",
                   help="reseal: the user's hand edit makes a new version (children become suspect)")
    p.set_defaults(func=cmd_spec)
    p = sub.add_parser("ecr")
    p.add_argument("action", nargs="?", default="list",
                   choices=["list", "show", "new", "change", "retire", "add", "approve", "reject", "apply"])
    p.add_argument("args", nargs="*")
    p.add_argument("--reason")
    p.add_argument("--text", help="change/add: the proposed text (else edit the file it names)")
    p.add_argument("--refines", help="add: comma-separated parent requirement IDs")
    p.add_argument("--title", help="change: a new title for the requirement")
    p.add_argument("--approval")
    p.set_defaults(func=cmd_ecr)
    p = sub.add_parser("note")
    p.add_argument("task")
    p.add_argument("text")
    p.add_argument("--kind", default="status",
                   choices=["status", "decision", "result", "not_done", "correction", "note"])
    p.set_defaults(func=cmd_note)
    p = sub.add_parser("journal")
    p.add_argument("text")
    p.set_defaults(func=cmd_journal)
    p = sub.add_parser("waiting")
    p.add_argument("action", nargs="?", choices=["list", "add", "done"], default="list")
    p.add_argument("args", nargs="*", help="add: TEXT; done: item numbers")
    p.add_argument("--task")
    p.set_defaults(func=cmd_waiting)
    p = sub.add_parser("edit")
    p.add_argument("task")
    for key in ("title", "group", "phase", "context"):
        p.add_argument(f"--{key}")
    p.add_argument("--order", type=int)
    p.add_argument("--depends", help='comma-separated task IDs; "" for none')
    p.add_argument("--needs", action="append", help="add a prerequisite that is not a task (repeat)")
    p.add_argument("--clear-needs", action="store_true")
    p.add_argument("--implements", help='comma-separated requirement IDs; "" for none')
    p.add_argument("--kind", choices=list(TASK_KINDS))
    p.set_defaults(func=cmd_edit)
    p = sub.add_parser("move")
    p.add_argument("args", nargs="+", metavar="T-NNNN... STATE")
    p.add_argument("--reason")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_move)
    p = sub.add_parser("supersede")
    p.add_argument("task")
    p.add_argument("--by", required=True, help="comma-separated IDs of the replacing tasks")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_supersede)
    p = sub.add_parser("stage")
    p.add_argument("action", nargs="?", choices=["show", "advance"], default="show")
    p.add_argument("--approval", help="advance: the user's words approving the end of the current stage")
    p.set_defaults(func=cmd_stage)
    p = sub.add_parser("list")
    p.add_argument("status", nargs="?",
                   choices=["ready", "waiting", "blocked", "in-progress", "done", "dropped", "backlog"])
    p.add_argument("--group")
    p.add_argument("--phase")
    p.set_defaults(func=cmd_list)
    sub.add_parser("json").set_defaults(func=cmd_json)
    p = sub.add_parser("group")
    p.add_argument("action", choices=["list", "add", "rename", "order", "remove", "describe"])
    p.add_argument("path", nargs="?", default="")
    p.add_argument("value", nargs="?", help="rename: NEW; order: N; describe: TEXT")
    p.add_argument("--order", type=int)
    p.add_argument("--description")
    p.set_defaults(func=cmd_group)
    p = sub.add_parser("phase")
    p.add_argument("action", choices=["list", "add", "rename"])
    p.add_argument("name", nargs="?")
    p.add_argument("new", nargs="?", help="rename: the new name")
    p.add_argument("--description")
    p.set_defaults(func=cmd_phase)
    p = sub.add_parser("language")
    p.add_argument("action", nargs="?", choices=["show", "set"], default="show")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_language)
    p = sub.add_parser("view")
    p.add_argument("action", nargs="?", choices=["show", "set", "note", "label", "status", "template", "color", "style"],
                   default="show")
    p.add_argument("file", nargs="?", help="set: generated file name; note: the note text; label/status: KEY")
    p.add_argument("value", nargs="?", help="label/status: the text")
    p.add_argument("lines", nargs="*", help="template: further lines")
    p.add_argument("--title")
    p.add_argument("--intro", action="append")
    p.set_defaults(func=cmd_view)
    a = ap.parse_args(argv)
    if a.cmd == "spec" and a.action == "reseal":
        speclib.RESEALING.update(a.args)       # the user's changed text must load for the reseal itself
    if a.cmd == "group" and a.action != "list":
        if not a.path:
            ap.error("group: needs AREA or AREA/TOPIC")
        if a.action in ("rename", "order", "describe") and a.value is None:
            ap.error(f"group {a.action}: needs a second argument")
        if a.action == "order":
            try:
                a.value = int(a.value)
            except ValueError:
                ap.error("group order: N must be an integer")
    return txn.run(lambda: a.func(a) if a.cmd else cmd_build(), problem_set, GENERATED_FILES)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

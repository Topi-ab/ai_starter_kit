"""Task store, part of pm/pm.py: areas and topics, phases, languages and the texts of the generated files
(group, phase, language, view)."""

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


def cmd_group(a) -> int:
    ix = load_index()
    s = ix.structure
    if a.action == "list":
        for area in s["areas"]:
            print(f"{area['order']}    {area['name']}" + (f"  — {area['description']}" if area["description"] else ""))
            for topic in area["topics"]:
                print(f"{area['order']}.{topic['order']}  {area['name']}/{topic['name']}"
                      + (f"  — {topic['description']}" if topic["description"] else ""))
        for phase in s["phases"]:
            print(f"phase  {phase['name']}" + (f"  — {phase['description']}" if phase["description"] else ""))
        return 0

    def parts(path: str) -> list[str]:
        p = path.split("/")
        need(len(p) in (1, 2) and all(p), "group", f"{path!r} must be AREA or AREA/TOPIC")
        return p

    p = parts(a.path)
    area = find_area(s, p[0])
    changes: list[tuple[Path, object]] = []
    if a.action == "add":
        if len(p) == 1:
            need(area is None, "group add", f"area {p[0]!r} exists")
            order = a.order or max([x["order"] for x in s["areas"]] + [0]) + 1
            s["areas"].append({"order": order, "name": p[0], "description": a.description or "", "topics": []})
        else:
            need(area is not None, "group add", f"no area {p[0]!r} (add it first)")
            need(find_topic(area, p[1]) is None, "group add", f"topic {a.path!r} exists")
            order = a.order or max([x["order"] for x in area["topics"]] + [0]) + 1
            area["topics"].append({"order": order, "name": p[1], "description": a.description or ""})
    elif a.action == "describe":
        need(area is not None, "group describe", f"no area {p[0]!r}")
        item = area if len(p) == 1 else find_topic(area, p[1])
        need(item is not None, "group describe", f"no topic {a.path!r}")
        item["description"] = " ".join(a.value.split())
    elif a.action == "order":
        need(area is not None, "group order", f"no area {p[0]!r}")
        item = area if len(p) == 1 else find_topic(area, p[1])
        need(item is not None, "group order", f"no topic {a.path!r}")
        item["order"] = check_order(a.value, "group order")
    elif a.action == "remove":
        users = [t.task_id for t in ix.tasks if in_group(t, a.path)]
        need(not users, "group remove", f"{a.path} is used by {', '.join(users)} (move them first)")
        need(area is not None, "group remove", f"no area {p[0]!r}")
        if len(p) == 1:
            s["areas"].remove(area)
        else:
            topic = find_topic(area, p[1])
            need(topic is not None, "group remove", f"no topic {a.path!r}")
            area["topics"].remove(topic)
    elif a.action == "rename":
        if len(p) == 2 and "/" not in a.value:
            a.value = f"{p[0]}/{a.value}"   # AREA/TOPIC -> TOPIC2: rename the topic in its area
        new = parts(a.value)
        need(len(new) == len(p), "group rename", "OLD and NEW must both be AREA, or both AREA/TOPIC")
        need(area is not None, "group rename", f"no area {p[0]!r}")
        if len(p) == 1:
            need(find_area(s, new[0]) is None, "group rename", f"area {new[0]!r} exists")
            area["name"] = new[0]
        else:
            topic = find_topic(area, p[1])
            need(topic is not None, "group rename", f"no topic {a.path!r}")
            target = find_area(s, new[0])
            need(target is not None, "group rename", f"no area {new[0]!r} (add it first)")
            need(find_topic(target, new[1]) is None, "group rename", f"topic {a.value!r} exists")
            area["topics"].remove(topic)
            topic["name"] = new[1]
            if target is not area:
                topic["order"] = max([x["order"] for x in target["topics"]] + [0]) + 1
            target["topics"].append(topic)
        for t in ix.tasks:
            if in_group(t, a.path):
                meta = read_json(t.path / META)
                meta["group"] = a.value + t.group[len(a.path):]
                changes.append((t.path / META, meta))
    else:
        need(False, "group", f"unknown action {a.action}")
    s["areas"].sort(key=lambda x: x["order"])
    for x in s["areas"]:
        x["topics"].sort(key=lambda t: t["order"])
    guarded([(STRUCTURE, s)] + changes)
    print(f"group {a.action} {a.path}: ok")
    return cmd_build()


def cmd_phase(a) -> int:
    s = load_index().structure
    if a.action == "list":
        for phase in s["phases"]:
            print(phase["name"] + (f"  — {phase['description']}" if phase["description"] else ""))
        return 0
    if a.action == "rename":
        names = [p["name"] for p in s["phases"]]
        need(a.name in names, "phase rename", f"no phase {a.name!r}")
        need(bool(a.new and a.new.strip()) and a.new not in names, "phase rename", "needs a new, unused name")
        next(p for p in s["phases"] if p["name"] == a.name)["name"] = a.new.strip()
        ix = load_index()
        changes = [(STRUCTURE, s)]
        for t in ix.tasks:
            if t.phase == a.name:
                meta = read_json(t.path / META)
                meta["phase"] = a.new.strip()
                changes.append((t.path / META, meta))
        guarded(changes)
        print(f"phase {a.name!r} -> {a.new.strip()!r} ({len(changes) - 1} tasks)")
        return cmd_build()
    need(bool(a.name), "phase add", "needs NAME")
    need(a.name not in [p["name"] for p in s["phases"]], "phase add", f"phase {a.name!r} exists")
    s["phases"].append({"name": a.name, "description": a.description or ""})
    guarded([(STRUCTURE, s)])
    return cmd_build()


def cmd_language(a) -> int:
    data = load_language()
    if a.action == "set":
        need(a.key in LANGUAGE_KEYS, "language set", f"KEY must be one of {', '.join(LANGUAGE_KEYS)}")
        need(bool(a.value and a.value.strip()), "language set", "needs a language name")
        data[a.key]["language"] = a.value.strip()
        write_json(LANGUAGE, data)
        load_language()
    for key in LANGUAGE_KEYS:
        print(f"{key:<11} {data[key]['language']:<12} {data[key]['covers']}")
    return 0


def cmd_view(a) -> int:
    views = load_views(GENERATED_FILES)
    if a.action == "set":
        need(a.file in views["files"], "view set", f"FILE must be one of {', '.join(GENERATED_FILES)}")
        need(a.title is not None or a.intro is not None, "view set", "needs --title and/or --intro")
        if a.title is not None:
            views["files"][a.file]["title"] = a.title
        if a.intro is not None:
            views["files"][a.file]["intro"] = a.intro
    elif a.action == "note":
        need(bool(a.file and a.file.strip()), "view note", "needs the note text")
        views["agent_note"] = a.file.strip()
    elif a.action in ("label", "status"):
        table = views["labels" if a.action == "label" else "status"]
        need(a.file in table, f"view {a.action}", f"KEY must be one of {', '.join(table)}")
        need(bool(a.value and a.value.strip()), f"view {a.action}", "needs the text")
        table[a.file] = a.value.strip()
    elif a.action == "color":
        need(a.file in views["status_colors"], "view color", f"STATUS must be one of {', '.join(views['status_colors'])}")
        need(bool(a.value), "view color", "needs a colour: a name (green) or #rrggbb")
        views["status_colors"][a.file] = a.value
    elif a.action == "style":
        need(a.file in ("latex", "html", "none"), "view style", "latex (VS Code and GitHub), html (VS Code) or none")
        views["color_style"] = a.file
    elif a.action == "template":
        lines = ([a.file] if a.file else []) + ([a.value] if a.value else []) + (a.lines or [])
        need(bool(lines), "view template", "needs the lines")
        views["task_template"] = lines
    if a.action in ("set", "note", "label", "status", "template", "color", "style"):
        old = VIEWS.read_text()
        write_json(VIEWS, views)
        try:
            load_views(GENERATED_FILES)
        except StoreError:
            VIEWS.write_text(old)
            raise
        return cmd_build()
    print(f"agent note: {views['agent_note']}")
    for name in GENERATED_FILES:
        view = views["files"][name]
        print(f"\n{name}: {view['title']}")
        for paragraph in view["intro"]:
            print(f"  {paragraph}")
    print("\nlabels:")
    for key, text in views["labels"].items():
        print(f"  {key:<20} {text}")
    print("\ntask_template:")
    for line in views["task_template"]:
        print(f"  {line}")
    print("\nstatus:")
    for key, text in views["status"].items():
        print(f"  {key:<20} {text}")
    return 0

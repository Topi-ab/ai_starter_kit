"""Shared storage layer for pm/pm.py and pm/question.py.

Everything the scripts read is JSON; Markdown is only written (generated) or carried as free text
(task.md). JSON is created and changed only through the two scripts: read_json() refuses a file that
is not in the exact form write_json() produces, so a hand edit is caught by the next run.

    pm/counters.json          {"next_task": 1, "next_question": 1}
    pm/structure.json         areas (level 1) with topics (level 2), each with an order number, and phases
    pm/questions/Q-NNNN.json  one question with its options and answers
    pm/views.json             the user-visible title and intro text of each generated file
    pm/language.json          which language to use for code, documentation, task files and chat
    pm/workflow.json          the workflow stages (forward only), the current stage and its history
    pm/waiting.json           things the user was asked in chat (not numbered questions) and not answered yet
"""

from __future__ import annotations

import datetime
import json
import os
import re
import tempfile
from pathlib import Path

PM_DIR = Path(__file__).resolve().parent
REPO_DIR = PM_DIR.parent
COUNTERS = PM_DIR / "counters.json"
STRUCTURE = PM_DIR / "structure.json"
QUESTIONS_DIR = PM_DIR / "questions"
VIEWS = PM_DIR / "views.json"
LANGUAGE = PM_DIR / "language.json"
WORKFLOW = PM_DIR / "workflow.json"
WAITING = PM_DIR / "waiting.json"
# Dated task.md entries written by `pm.py note` (and by move/supersede); their labels are in views.json.
NOTE_KINDS = ("status", "decision", "result", "not_done", "correction", "note", "started", "done", "dropped",
              "moved", "superseded")
TEMPLATE_FIELDS = ("{now}", "{request}", "{description}", "{done_when}")
STAGE_RULE_KEYS = {"task_kinds", "start_work"}
TASK_KINDS = ("meta", "work")
# Fixed texts of the generated files (translated with `pm.py view label` / `view status`).
LABEL_KEYS = ("next_up", "no_ready_task", "nothing_here", "no_tasks", "no_tasks_no_areas", "ungrouped", "area_wide",
              "context", "phase", "depends_on", "none", "waits_for", "answered_questions", "see", "also_needs",
              "needed_by", "summary", "summary_caption", "area_topic", "backlog", "all", "recommended", "tasks",
              "answer", "meta_task", "superseded_by", "workflow_stage", "waiting_items", "implements",
              "spec_committed", "spec_draft", "spec_retired", "ecr_heading", "ecr_actions", "ecr_impact",
              "version", "source", "refines", "suspect", "spec_sealed")
# labels added after the first release: a views.json from an older kit gets these defaults until
# `pm.py view label KEY TEXT` sets them (which writes the full views.json)
LATER_LABELS = {"spec_sealed": "Sealed requirements (only the user changes them, by hand)"}
STATUS_KEYS = ("ready", "waiting", "blocked", "in progress", "done", "dropped", "backlog")
COLOR_STYLES = ("latex", "html", "none")   # how status words are coloured in the overviews
LANGUAGE_KEYS = ("code", "docs", "task_files", "chat")

TID_RE = re.compile(r"^T-(\d{4})\Z")
QID_RE = re.compile(r"^Q-(\d{4})\Z")
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}\Z")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
OPTION_KEYS = "abcdefghij"
FREE_CHOICES = ("other", "superseded", "withdrawn")   # answers that are not an option letter
NOTE_REQUIRED = ("other", "superseded")               # the note holds the user's answer / the new question ID


class StoreError(Exception):
    pass


def now() -> str:
    """Local time, the one time stamp format used everywhere."""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def dump_json(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def read_json(path: Path) -> object:
    rel = path.relative_to(REPO_DIR)
    try:
        text = path.read_text()
        data = json.loads(text)
    except FileNotFoundError:
        raise StoreError(f"{rel}: missing") from None
    except json.JSONDecodeError as err:
        raise StoreError(f"{rel}: invalid JSON: {err}") from None
    if text != dump_json(data):
        raise StoreError(f"{rel}: not in the form the scripts write (edited by hand?). Change JSON only "
                         "through pm/pm.py and pm/question.py")
    return data


def write_json(path: Path, data: object) -> None:
    """Atomic write: an interrupted run leaves the old file, never half a file."""
    text = dump_json(data)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def need(cond: bool, where: Path | str, message: str) -> None:
    if not cond:
        where = where.relative_to(REPO_DIR) if isinstance(where, Path) else where
        raise StoreError(f"{where}: {message}")


def only_keys(obj: object, allowed: set[str], required: set[str], where: Path | str) -> dict:
    need(isinstance(obj, dict), where, "must be a JSON object")
    unknown = sorted(set(obj) - allowed)
    need(not unknown, where, f"unknown keys {', '.join(unknown)}")
    missing = sorted(required - set(obj))
    need(not missing, where, f"missing keys {', '.join(missing)}")
    return obj


# ---------------------------------------------------------------- counters

COUNTER_KEYS = {"next_task", "next_question", "next_requirement", "next_ecr"}


def read_counters() -> dict[str, int]:
    data = only_keys(read_json(COUNTERS), COUNTER_KEYS, COUNTER_KEYS, COUNTERS)
    for key, value in data.items():
        need(isinstance(value, int) and not isinstance(value, bool) and value >= 1, COUNTERS,
             f"{key} must be a positive integer")
    return data


def take_number(key: str) -> int:
    """Hand out the next number and raise the counter first: an interrupted run skips a number, never reuses one."""
    counters = read_counters()
    number = counters[key]
    counters[key] = number + 1
    write_json(COUNTERS, counters)
    return number


# ---------------------------------------------------------------- structure (areas, topics, phases)

def plain_text(value: object, where: Path | str, what: str) -> str:
    """A one-line text (titles, names): not blank, no control characters (they would break the overviews)."""
    need(isinstance(value, str) and value.strip() != "", where, f"{what} must be text")
    need(not CONTROL_RE.search(value), where, f"{what} must be one line without control characters: {value!r}")
    return value


def check_name(name: object, where: str) -> str:
    need(isinstance(name, str) and name.strip() == name and name != "", where, f"bad name {name!r}")
    plain_text(name, where, "name")
    need("/" not in name, where, f"name {name!r} must not contain '/'")
    return name


def check_group_name(name: object, where: str) -> str:
    """An area or topic name is a word, not a number: the overviews number areas and topics themselves
    (N, N.M), so '1' or '2.3' as a name is ambiguous."""
    check_name(name, where)
    need(not re.fullmatch(r"\d+(\.\d+)?", str(name)), where,
         f"group name {name!r} is only a number: use a word (e.g. 'Parser'); areas and topics are numbered "
         "by their order")
    return str(name)


def load_structure() -> dict:
    """{"areas": [{"order": N, "name", "description", "topics": [{"order": M, "name", "description"}]}],
        "phases": [{"name", "description"}]}

    N.M is the position of a topic: area N, topic M within it (M = 0 is reserved for area-wide tasks).
    Areas and topics are returned sorted by their order numbers."""
    data = only_keys(read_json(STRUCTURE), {"areas", "phases"}, {"areas", "phases"}, STRUCTURE)
    need(isinstance(data["areas"], list) and isinstance(data["phases"], list), STRUCTURE, "areas and phases must be lists")
    areas: set[str] = set()
    area_orders: set[int] = set()
    for i, area in enumerate(data["areas"]):
        where = f"{STRUCTURE.relative_to(REPO_DIR)} areas[{i}]"
        only_keys(area, {"order", "name", "description", "topics"}, {"order", "name", "topics"}, where)
        name = check_group_name(area["name"], where)
        check_order(area["order"], where)
        need(name not in areas, where, f"area {name!r} defined twice")
        areas.add(name)
        need(area["order"] not in area_orders, where, f"area order {area['order']} used twice")
        area_orders.add(area["order"])
        area.setdefault("description", "")
        need(isinstance(area["description"], str), where, "description must be a string")
        need(isinstance(area["topics"], list), where, "topics must be a list")
        topics: set[str] = set()
        topic_orders: set[int] = set()
        for j, topic in enumerate(area["topics"]):
            twhere = f"{where}.topics[{j}]"
            only_keys(topic, {"order", "name", "description"}, {"order", "name"}, twhere)
            tname = check_group_name(topic["name"], twhere)
            check_order(topic["order"], twhere)
            need(tname not in topics, twhere, f"topic {tname!r} defined twice in {name!r}")
            need(topic["order"] not in topic_orders, twhere, f"topic order {topic['order']} used twice in {name!r}")
            topics.add(tname)
            topic_orders.add(topic["order"])
            topic.setdefault("description", "")
            need(isinstance(topic["description"], str), twhere, "description must be a string")
    phases: set[str] = set()
    for i, phase in enumerate(data["phases"]):
        where = f"{STRUCTURE.relative_to(REPO_DIR)} phases[{i}]"
        only_keys(phase, {"name", "description"}, {"name"}, where)
        name = check_name(phase["name"], where)
        need(name not in phases, where, f"phase {name!r} defined twice")
        phases.add(name)
        phase.setdefault("description", "")
    data["areas"].sort(key=lambda a: a["order"])
    for area in data["areas"]:
        area["topics"].sort(key=lambda t: t["order"])
    return data


def check_order(value: object, where: str) -> int:
    need(isinstance(value, int) and not isinstance(value, bool) and value >= 1, where,
         f"order must be a positive integer, not {value!r}")
    return value


def find_area(structure: dict, name: str) -> dict | None:
    return next((a for a in structure["areas"] if a["name"] == name), None)


def find_topic(area: dict, name: str) -> dict | None:
    return next((t for t in area["topics"] if t["name"] == name), None)


def split_group(structure: dict, group: str, where: str) -> tuple[str, str]:
    """"Area" or "Area/Topic" -> (area, topic); both must be defined in structure.json."""
    parts = group.split("/")
    need(len(parts) in (1, 2) and all(parts), where, f"group {group!r} must be \"Area\" or \"Area/Topic\"")
    area = find_area(structure, parts[0])
    need(area is not None, where, f"unknown area {parts[0]!r} (python3 pm/pm.py group list)")
    if len(parts) == 2:
        need(find_topic(area, parts[1]) is not None, where,
             f"unknown topic {parts[1]!r} in area {parts[0]!r} (python3 pm/pm.py group list)")
        return parts[0], parts[1]
    return parts[0], ""


# ---------------------------------------------------------------- views (texts of the generated files)

def load_views(required: list[str]) -> dict:
    """{"agent_note": str, "files": {"TODO.md": {"title": str, "intro": [paragraph, ...]}, ...},
        "labels": {key: text}, "status": {status: text}, "note_labels": {kind: text},
        "status_colors": {status: colour}, "color_style": "latex" | "html" | "none",
        "task_template": [line, ...]}   ({now} {request} {description} {done_when}; a line whose fields
                                         are all empty is left out)

    agent_note is the first line of every generated file (an HTML comment); the rest is for the user."""
    keys = {"agent_note", "files", "labels", "status", "note_labels", "task_template", "status_colors", "color_style"}
    data = only_keys(read_json(VIEWS), keys, keys, VIEWS)
    need(data["color_style"] in COLOR_STYLES, VIEWS, f"color_style must be one of {', '.join(COLOR_STYLES)}")
    only_keys(data["status_colors"], set(STATUS_KEYS), set(STATUS_KEYS), f"{VIEWS.relative_to(REPO_DIR)} status_colors")
    for status, colour in data["status_colors"].items():
        need(isinstance(colour, str) and re.fullmatch(r"[A-Za-z]+|#[0-9A-Fa-f]{6}", colour) is not None, VIEWS,
             f"status_colors.{status}: a colour name (green) or #rrggbb, not {colour!r}")
    need(isinstance(data["task_template"], list) and all(isinstance(x, str) for x in data["task_template"]), VIEWS,
         "task_template must be a list of lines")
    for line in data["task_template"]:
        for field in re.findall(r"\{[a-z_]+\}", line):
            need(field in TEMPLATE_FIELDS, VIEWS, f"task_template: unknown field {field} "
                 f"(allowed: {' '.join(TEMPLATE_FIELDS)})")
    if isinstance(data["labels"], dict):
        for key, text in LATER_LABELS.items():
            data["labels"].setdefault(key, text)
    for key, wanted in (("labels", LABEL_KEYS), ("status", STATUS_KEYS), ("note_labels", NOTE_KINDS)):
        where = f"{VIEWS.relative_to(REPO_DIR)} {key}"
        only_keys(data[key], set(wanted), set(wanted), where)
        need(all(isinstance(v, str) and v.strip() for v in data[key].values()), where, "every text must be non-empty")
    need(isinstance(data["agent_note"], str) and data["agent_note"].strip() != "", VIEWS, "agent_note must be text")
    need("--" not in data["agent_note"], VIEWS, "agent_note must not contain '--' (it becomes an HTML comment)")
    need(isinstance(data["files"], dict), VIEWS, "files must be an object")
    missing = [f for f in required if f not in data["files"]]
    need(not missing, VIEWS, f"no entry for {', '.join(missing)}")
    unknown = [f for f in data["files"] if f not in required]
    need(not unknown, VIEWS, f"entries for files that are not generated: {', '.join(unknown)}")
    for name, view in data["files"].items():
        where = f"{VIEWS.relative_to(REPO_DIR)} files.{name}"
        only_keys(view, {"title", "intro"}, {"title", "intro"}, where)
        need(isinstance(view["title"], str) and view["title"].strip() != "", where, "title must be text")
        need(isinstance(view["intro"], list) and all(isinstance(x, str) for x in view["intro"]), where,
             "intro must be a list of paragraphs (strings)")
    return data


# ---------------------------------------------------------------- workflow stages

def load_workflow() -> dict:
    """{"stages": [{"id", "name", "description", "rules": {"task_kinds": [...], "start_work": bool}}],
        "current": stage id, "history": [{"from", "to", "time", "approval"}]}

    Stages are passed in order and only forward (`pm.py stage advance`)."""
    keys = {"stages", "current", "history"}
    data = only_keys(read_json(WORKFLOW), keys, keys, WORKFLOW)
    need(isinstance(data["stages"], list) and data["stages"], WORKFLOW, "stages must be a non-empty list")
    ids = []
    for i, stage in enumerate(data["stages"]):
        where = f"{WORKFLOW.relative_to(REPO_DIR)} stages[{i}]"
        only_keys(stage, {"id", "name", "description", "rules"}, {"id", "name", "description", "rules"}, where)
        need(isinstance(stage["id"], str) and stage["id"].isidentifier(), where, "id must be a short identifier")
        need(stage["id"] not in ids, where, f"stage {stage['id']!r} defined twice")
        ids.append(stage["id"])
        need(isinstance(stage["name"], str) and stage["name"].strip() != "", where, "name must be text")
        need(isinstance(stage["description"], str), where, "description must be text")
        rules = only_keys(stage["rules"], STAGE_RULE_KEYS, STAGE_RULE_KEYS, f"{where}.rules")
        need(isinstance(rules["task_kinds"], list) and rules["task_kinds"]
             and all(k in TASK_KINDS for k in rules["task_kinds"]), where,
             f"rules.task_kinds must be a non-empty list of {', '.join(TASK_KINDS)}")
        need(isinstance(rules["start_work"], bool), where, "rules.start_work must be true or false")
    need(data["current"] in ids, WORKFLOW, f"current must be one of {', '.join(ids)}")
    need(isinstance(data["history"], list), WORKFLOW, "history must be a list")
    position = 0
    for i, step in enumerate(data["history"]):
        where = f"{WORKFLOW.relative_to(REPO_DIR)} history[{i}]"
        only_keys(step, {"from", "to", "time", "approval"}, {"from", "to", "time", "approval"}, where)
        need(step["from"] == ids[position] and position + 1 < len(ids) and step["to"] == ids[position + 1], where,
             "every step must go from one stage to the next one (forward only)")
        need(isinstance(step["time"], str) and STAMP_RE.match(step["time"]), where, "time must be YYYY-MM-DD HH:MM")
        need(isinstance(step["approval"], str) and step["approval"].strip() != "", where,
             "approval must hold the user's words")
        position += 1
    need(data["current"] == ids[position], WORKFLOW, "current does not match the history")
    return data


def current_stage(workflow: dict) -> dict:
    return next(s for s in workflow["stages"] if s["id"] == workflow["current"])


# ---------------------------------------------------------------- waiting (plain chat questions)

def load_waiting() -> dict:
    """{"next": int, "items": [{"id": int, "time": "YYYY-MM-DD HH:MM", "text": str, "task": "T-NNNN" or ""}]}"""
    data = only_keys(read_json(WAITING), {"next", "items"}, {"next", "items"}, WAITING)
    need(isinstance(data["next"], int) and data["next"] >= 1, WAITING, "next must be a positive integer")
    need(isinstance(data["items"], list), WAITING, "items must be a list")
    ids = set()
    for i, item in enumerate(data["items"]):
        where = f"{WAITING.relative_to(REPO_DIR)} items[{i}]"
        only_keys(item, {"id", "time", "text", "task"}, {"id", "time", "text", "task"}, where)
        need(isinstance(item["id"], int) and 1 <= item["id"] < data["next"] and item["id"] not in ids, where,
             "id must be unique and below next")
        ids.add(item["id"])
        need(isinstance(item["time"], str) and STAMP_RE.match(item["time"]), where, "time must be YYYY-MM-DD HH:MM")
        need(isinstance(item["text"], str) and item["text"].strip() != "", where, "text must be non-empty")
        need(isinstance(item["task"], str) and (item["task"] == "" or bool(TID_RE.match(item["task"]))), where,
             "task must be T-NNNN or empty")
    return data


# ---------------------------------------------------------------- language

def load_language() -> dict:
    """{"code": {"language": "English", "covers": "..."}, "docs": {...}, "task_files": {...}, "chat": {...}}"""
    data = only_keys(read_json(LANGUAGE), set(LANGUAGE_KEYS), set(LANGUAGE_KEYS), LANGUAGE)
    for key in LANGUAGE_KEYS:
        where = f"{LANGUAGE.relative_to(REPO_DIR)} {key}"
        only_keys(data[key], {"language", "covers"}, {"language", "covers"}, where)
        need(isinstance(data[key]["language"], str) and data[key]["language"].strip() != "", where,
             "language must be a name, e.g. English")
        need(isinstance(data[key]["covers"], str), where, "covers must be text")
    return data


# ---------------------------------------------------------------- questions

def question_path(qid: str) -> Path:
    return QUESTIONS_DIR / f"{qid}.json"


def check_question(q: object, path: Path) -> dict:
    only_keys(q, {"id", "asked", "text", "options", "answers"}, {"id", "asked", "text", "options", "answers"}, path)
    need(isinstance(q["id"], str) and QID_RE.match(q["id"]) and path.stem == q["id"], path,
         f"id {q['id']!r} must be Q-NNNN and match the file name")
    need(isinstance(q["asked"], str) and STAMP_RE.match(q["asked"]), path, "asked must be YYYY-MM-DD HH:MM")
    need(isinstance(q["text"], str) and q["text"].strip(), path, "text must be a non-empty string")
    need(isinstance(q["options"], list), path, "options must be a list")
    keys = []
    for i, opt in enumerate(q["options"]):
        only_keys(opt, {"key", "text", "recommended"}, {"key", "text"}, f"{path.relative_to(REPO_DIR)} options[{i}]")
        need(opt["key"] == OPTION_KEYS[i] if i < len(OPTION_KEYS) else False, path, f"option {i} must have key "
             f"{OPTION_KEYS[i] if i < len(OPTION_KEYS) else '(at most 10 options)'}")
        need(isinstance(opt["text"], str) and opt["text"].strip(), path, f"option {opt['key']}: empty text")
        need(opt.get("recommended", False) in (True, False), path, "recommended must be true or false")
        need(not opt.get("recommended") or i == 0, path, "only the first option can be the recommended one")
        keys.append(opt["key"])
    need(isinstance(q["answers"], list), path, "answers must be a list")
    for i, ans in enumerate(q["answers"]):
        where = f"{path.relative_to(REPO_DIR)} answers[{i}]"
        only_keys(ans, {"time", "choice", "note"}, {"time", "choice"}, where)
        need(isinstance(ans["time"], str) and STAMP_RE.match(ans["time"]), where, "time must be YYYY-MM-DD HH:MM")
        need(ans["choice"] in keys or ans["choice"] in FREE_CHOICES, where,
             f"choice must be one of {', '.join(keys + list(FREE_CHOICES))}")
        need(isinstance(ans.get("note", ""), str), where, "note must be a string")
        need(ans["choice"] not in NOTE_REQUIRED or ans.get("note"), where, f"choice {ans['choice']!r} needs a note")
    return q


def load_questions() -> dict[str, dict]:
    questions: dict[str, dict] = {}
    if QUESTIONS_DIR.is_dir():
        for path in sorted(QUESTIONS_DIR.iterdir()):
            if path.name == ".gitkeep" or path.name.startswith("."):
                continue
            need(path.is_file() and QID_RE.match(path.stem) and path.suffix == ".json", path,
                 "only Q-NNNN.json files belong in pm/questions/")
            q = check_question(read_json(path), path)
            questions[q["id"]] = q
    next_q = read_counters()["next_question"]
    too_high = [qid for qid in questions if int(qid[2:]) >= next_q]
    need(not too_high, COUNTERS, f"question IDs at or above next_question ({next_q}): {', '.join(too_high)}")
    return questions


def is_open(q: dict) -> bool:
    return not q["answers"]


def format_question(q: dict) -> str:
    """The question as pasted into a chat message."""
    first, *rest = q["text"].strip().splitlines()
    out = [f"**{q['id']}:** {first}"] + [f"   {line}" for line in rest]
    for opt in q["options"]:
        rec = "(recommended) " if opt.get("recommended") else ""
        out.append(f"   {opt['key']}) {rec}{opt['text']}")
    for ans in q["answers"]:
        note = f" ({ans['note']})" if ans.get("note") else ""
        out.append(f"   [answered {ans['time']}: {ans['choice']}{note}]")
    return "\n".join(out)

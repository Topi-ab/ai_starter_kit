"""Requirements (pm/spec/) and engineering change requests (pm/ecr/): storage and validation.

    pm/spec/R-NNNN/meta.json   {"id", "title", "status": draft|committed|retired, "version", "source",
                                "refines": [{"req", "version"}],
                                "text_sha256", "history": [{"version", "time", "event", "ecr", "approval"}]}
    pm/spec/R-NNNN/text.md     the requirement text (free Markdown; a draft may change freely)
    pm/spec/R-NNNN/vK.md       the text of an earlier version K, kept when an ECR changes it

    pm/ecr/ECR-NNNN/meta.json  {"id", "title", "state": open|approved|rejected|applied, "created",
                                "actions": [{"action": change|retire|add, "req", "title", "file", "sha256"}],
                                "history": [{"state", "time", "approval"}]}
    pm/ecr/ECR-NNNN/ecr.md     why the change is needed (free Markdown)
    pm/ecr/ECR-NNNN/proposed/  the proposed texts: R-NNNN.md (change) or add-K.md (new requirement)

Requirements form a graph (a requirement may refine several others). A "refines" link records the
version of the parent it was last reviewed against; when the parent gets a newer version or is
retired, the child is *suspect* (and so are its own children) until it is reviewed
(`spec review`) or changed. Suspect requirements block marking the tasks implementing them done.

A committed or retired requirement is locked: its text.md must match text_sha256, so a hand edit is
caught by `check`. It changes only through an ECR (open -> approved -> applied). An approved ECR is
locked too: its proposed files must match the sha256 recorded at approval.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from tasklib import PM_DIR, REPO_DIR, STAMP_RE, StoreError, need, only_keys, plain_text, read_counters, read_json

SPEC_DIR = PM_DIR / "spec"
ECR_DIR = PM_DIR / "ecr"
RID_RE = re.compile(r"^R-(\d{4})\Z")
EID_RE = re.compile(r"^ECR-(\d{4})\Z")
REQ_STATES = ("draft", "committed", "sealed", "retired")
RESEALING: set[str] = set()     # set by `pm.py spec reseal` (run by the user): their changed text loads
ECR_STATES = ("open", "approved", "rejected", "applied")
ECR_FORWARD = {"open": ("approved", "rejected"), "approved": ("applied", "rejected")}
ACTIONS = ("change", "retire", "add")
REQ_EVENTS = ("committed", "changed", "retired", "added", "reviewed", "linked", "retitled", "sealed", "resealed")
META = "meta.json"
TEXT = "text.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(REPO_DIR))


def check_history(history: object, keys: set[str], where: str) -> list:
    need(isinstance(history, list), where, "history must be a list")
    for i, h in enumerate(history):
        only_keys(h, keys, keys, f"{where} history[{i}]")
        need(isinstance(h["time"], str) and STAMP_RE.match(h["time"]), where, f"history[{i}].time: YYYY-MM-DD HH:MM")
    return history


def load_requirements() -> dict[str, dict]:
    reqs: dict[str, dict] = {}
    if not SPEC_DIR.is_dir():
        return reqs
    for d in sorted(SPEC_DIR.iterdir()):
        if d.name == ".gitkeep":
            continue
        need(d.is_dir() and bool(RID_RE.match(d.name)), d, "only requirement directories R-NNNN belong in pm/spec/")
        meta_path, text_path = d / META, d / TEXT
        need(meta_path.is_file() and text_path.is_file(), d, "needs meta.json and text.md")
        keys = {"id", "title", "status", "version", "source", "refines", "text_sha256", "old_sha256", "history"}
        m = only_keys(read_json(meta_path), keys, keys, meta_path)
        need(int(d.name[2:]) < read_counters()["next_requirement"], meta_path,
             f"{d.name} is not below next_requirement in pm/counters.json")
        need(isinstance(m["refines"], list), meta_path, "refines must be a list")
        for i, link in enumerate(m["refines"]):
            only_keys(link, {"req", "version"}, {"req", "version"}, f"{rel(meta_path)} refines[{i}]")
            need(isinstance(link["req"], str) and bool(RID_RE.match(link["req"])) and link["req"] != d.name,
                 meta_path, f"refines[{i}]: bad requirement {link['req']!r}")
            need(isinstance(link["version"], int) and link["version"] >= 1, meta_path,
                 f"refines[{i}]: version must be a positive integer")
        need(m["id"] == d.name, meta_path, "id does not match the directory")
        plain_text(m["title"], meta_path, "title")
        need(m["status"] in REQ_STATES, meta_path, f"status must be one of {', '.join(REQ_STATES)}")
        need(isinstance(m["version"], int) and m["version"] >= 1, meta_path, "version must be a positive integer")
        need(isinstance(m["source"], str), meta_path, "source must be text")
        check_history(m["history"], {"version", "time", "event", "ecr", "approval"}, rel(meta_path))
        for h in m["history"]:
            need(h["event"] in REQ_EVENTS, meta_path, f"history event must be one of {', '.join(REQ_EVENTS)}")
            need(isinstance(h["approval"], str) and isinstance(h["ecr"], str), meta_path, "history: approval and ecr are text")
        events = [h["event"] for h in m["history"]]
        changed = events.count("changed")
        if m["status"] == "draft":
            need(m["version"] == 1 and not changed, meta_path, "a draft is version 1 (it changes in place)")
        else:
            need(bool({"committed", "added"} & set(events)), meta_path,
                 f"{m['status']} without a 'committed' or 'added' history entry (who approved it?)")
            need(m["version"] == 1 + changed, meta_path, "version does not match the 'changed' history entries")
        need(m["status"] != "retired" or "retired" in events, meta_path, "retired without a 'retired' history entry")
        need(m["status"] != "sealed" or "sealed" in events, meta_path, "sealed without a 'sealed' history entry")
        need(isinstance(m["old_sha256"], dict), meta_path, "old_sha256 must be an object")
        extra = sorted(p.name for p in d.iterdir()
                       if p.name not in (META, TEXT) and not re.match(r"^v\d+\.md$", p.name))
        need(not extra, d, f"unexpected files {', '.join(extra)}")
        need(set(m["old_sha256"]) == {str(k) for k in range(1, m["version"])}, meta_path,
             "old_sha256 must list every earlier version")
        for k in range(1, m["version"]):
            need((d / f"v{k}.md").is_file(), d, f"missing v{k}.md (the text of version {k})")
            need(m["old_sha256"][str(k)] == sha256(d / f"v{k}.md"), d / f"v{k}.md",
                 "an earlier version's text has changed")
        if m["status"] == "draft":
            need(m["text_sha256"] == "", meta_path, "a draft has no text_sha256")
        elif m["status"] == "sealed":
            need(m["text_sha256"] == sha256(text_path) or m["id"] in RESEALING, text_path,
                 f"the text of sealed {m['id']} differs from its recorded hash: only the user changes a sealed "
                 "requirement, by hand, and then runs python3 pm/pm.py spec reseal at a terminal "
                 "(an agent restores the text with git and asks the user)")
        else:
            need(m["text_sha256"] == sha256(text_path), text_path,
                 f"the text of {m['status']} {m['id']} has changed without an ECR "
                 "(restore it with git, or open an ECR: python3 pm/pm.py ecr new ...)")
        m["path"] = d
        m["text"] = text_path.read_text()
        reqs[m["id"]] = m
    for r in reqs.values():
        for link in r["refines"]:
            need(link["req"] in reqs, r["path"] / META, f"refines unknown requirement {link['req']}")
            need(link["version"] <= reqs[link["req"]]["version"], r["path"] / META,
                 f"refines {link['req']} version {link['version']}, which does not exist yet")
    cycle = find_cycle(reqs)
    need(not cycle, SPEC_DIR, "requirement cycle: " + " -> ".join(cycle))
    return reqs


def find_cycle(reqs: dict[str, dict]) -> list[str]:
    """One requirement cycle as a closed path, or [] (iterative depth-first search)."""
    state: dict[str, int] = {}      # 1 = on the current path, 2 = finished
    for root in sorted(reqs):
        if root in state:
            continue
        path: list[str] = [root]
        stack = [iter([x["req"] for x in reqs[root]["refines"] if x["req"] in reqs])]
        state[root] = 1
        while stack:
            nxt = next(stack[-1], None)
            if nxt is None:
                state[path.pop()] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                return path[path.index(nxt):] + [nxt]
            elif nxt not in state:
                state[nxt] = 1
                path.append(nxt)
                stack.append(iter([x["req"] for x in reqs[nxt]["refines"] if x["req"] in reqs]))
    return []


def children(reqs: dict[str, dict], rid: str) -> list[str]:
    return sorted(r["id"] for r in reqs.values() if any(link["req"] == rid for link in r["refines"]))


def descendants(reqs: dict[str, dict], roots: set[str]) -> list[str]:
    seen: set[str] = set()
    todo = list(roots)
    while todo:
        for c in children(reqs, todo.pop()):
            if c not in seen and c not in roots:
                seen.add(c)
                todo.append(c)
    return sorted(seen)


def stale_links(reqs: dict[str, dict], rid: str) -> list[str]:
    """The parents of rid that changed (newer version) or were retired since rid was reviewed against them."""
    out = []
    for link in reqs[rid]["refines"]:
        parent = reqs[link["req"]]
        if parent["status"] == "retired" or link["version"] < parent["version"]:
            out.append(link["req"])
    return out


def suspects(reqs: dict[str, dict]) -> dict[str, str]:
    """{rid: "direct" | "inherited"}: direct = a parent changed; inherited = a parent is itself suspect.
    Retired requirements are never suspect."""
    out: dict[str, str] = {}
    for rid in reqs:
        if reqs[rid]["status"] != "retired" and stale_links(reqs, rid):
            out[rid] = "direct"
    for rid in descendants(reqs, set(out)):
        if reqs[rid]["status"] != "retired":
            out.setdefault(rid, "inherited")
    return out


def load_ecrs(reqs: dict[str, dict]) -> dict[str, dict]:
    ecrs: dict[str, dict] = {}
    if not ECR_DIR.is_dir():
        return ecrs
    for d in sorted(ECR_DIR.iterdir()):
        if d.name == ".gitkeep":
            continue
        need(d.is_dir() and bool(EID_RE.match(d.name)), d, "only ECR directories ECR-NNNN belong in pm/ecr/")
        meta_path = d / META
        need(meta_path.is_file() and (d / "ecr.md").is_file(), d, "needs meta.json and ecr.md")
        keys = {"id", "title", "state", "created", "actions", "history", "reason_sha256"}
        m = only_keys(read_json(meta_path), keys, keys, meta_path)
        need(int(d.name[4:]) < read_counters()["next_ecr"], meta_path, f"{d.name} is not below next_ecr")
        plain_text(m["title"], meta_path, "title")
        m["problems"] = []      # inconsistencies with the current requirements (reported, not load errors)
        need(m["id"] == d.name, meta_path, "id does not match the directory")
        need(m["state"] in ECR_STATES, meta_path, f"state must be one of {', '.join(ECR_STATES)}")
        need(isinstance(m["created"], str) and STAMP_RE.match(m["created"]), meta_path, "created: YYYY-MM-DD HH:MM")
        check_history(m["history"], {"state", "time", "approval"}, rel(meta_path))
        state = "open"
        for h in m["history"]:
            need(h["state"] in ECR_FORWARD.get(state, ()), meta_path,
                 f"history: {state} -> {h['state']} is not allowed (forward only)")
            need(isinstance(h["approval"], str) and h["approval"].strip() != "", meta_path,
                 "every state change records the user's words")
            state = h["state"]
        need(state == m["state"], meta_path, "state does not match the history")
        need(isinstance(m["actions"], list), meta_path, "actions must be a list")
        need(isinstance(m["reason_sha256"], str), meta_path, "reason_sha256 must be text")
        if m["state"] == "applied" or (m["state"] == "approved"):
            need(m["reason_sha256"] == sha256(d / "ecr.md"), d / "ecr.md", "ecr.md changed after the ECR was approved")
        files = {p.name for p in d.iterdir()} - {META, "ecr.md", "proposed"}
        need(not files, d, f"unexpected files {', '.join(sorted(files))}")
        targets = set()
        referenced = set()
        for i, act in enumerate(m["actions"]):
            where = f"{rel(meta_path)} actions[{i}]"
            akeys = {"action", "req", "title", "file", "sha256", "refines", "base"}
            only_keys(act, akeys, akeys, where)
            for key in ("action", "req", "title", "file", "sha256"):
                need(isinstance(act[key], str), where, f"{key} must be text")
            need(isinstance(act["base"], int) and not isinstance(act["base"], bool) and act["base"] >= 0, where,
                 "base must be a version number")
            need(isinstance(act["refines"], list) and all(x in reqs for x in act["refines"]), where,
                 "refines must list existing requirements")
            need(act["action"] in ACTIONS, where, f"action must be one of {', '.join(ACTIONS)}")
            if act["action"] in ("change", "retire"):
                need(act["req"] in reqs, where, f"unknown requirement {act['req']!r}")
                need(act["req"] not in targets, where, f"{act['req']} appears twice")
                targets.add(act["req"])
                if m["state"] in ("open", "approved"):
                    r = reqs[act["req"]]
                    if r["status"] != "committed":
                        m["problems"].append(f"{m['id']} {act['action']}s {act['req']}, which is {r['status']} now: "
                                             f"reject the ECR (ecr reject {m['id']} ...) or open a new one")
                    elif act["base"] != r["version"]:
                        m["problems"].append(f"{m['id']} was written against {act['req']} v{act['base']}, which "
                                             f"is v{r['version']} now: reject it and open a new one")
            if act["action"] in ("change", "add"):
                f = d / act["file"]
                need(re.fullmatch(r"proposed/(R-\d{4}|add-\d+)\.md", act["file"]) is not None and f.is_file(), where,
                     f"missing or bad proposed file {act['file']!r}")
                referenced.add(Path(act["file"]).name)
                if m["state"] in ("approved", "applied"):
                    need(act["sha256"] == sha256(f), f, "the proposed text changed after the ECR was approved")
            if act["action"] == "add":
                need(isinstance(act["title"], str) and act["title"].strip() != "", where, "add needs a title")
        extra = {p.name for p in (d / "proposed").iterdir()} - referenced if (d / "proposed").is_dir() else set()
        need(not extra, d / "proposed", f"unexpected files {', '.join(sorted(extra))}")
        m["path"] = d
        ecrs[m["id"]] = m
    return ecrs

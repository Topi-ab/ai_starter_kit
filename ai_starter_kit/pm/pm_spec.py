"""Task store, part of pm/pm.py: requirements and change requests (spec ..., ecr ...)."""

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




def links(ix: Index, text: str | None) -> list[dict]:
    ids = [x.strip() for x in (text or "").split(",") if x.strip()]
    for r in ids:
        need(r in ix.reqs, "--refines", f"unknown requirement {r}")
        need(ix.reqs[r]["status"] != "retired", "--refines", f"{r} is retired")
    return [{"req": r, "version": ix.reqs[r]["version"]} for r in ids]


def depth(reqs: dict, rid: str) -> int:
    """Length of the longest chain of parents above rid (the graph has no cycles: load_requirements)."""
    memo: dict[str, int] = {}
    stack = [rid]
    while stack:
        cur = stack[-1]
        parents = [x["req"] for x in reqs[cur]["refines"] if x["req"] in reqs]
        todo = [p for p in parents if p not in memo]
        if todo:
            stack.extend(todo)
            continue
        memo[cur] = 1 + max((memo[p] for p in parents), default=-1)
        stack.pop()
    return memo[rid]


def review_one(ix: Index, rid: str, approval: str) -> None:
    r = ix.reqs[rid]
    need(ix.suspect.get(rid) == "direct", "spec review",
         f"{rid} is suspect only through a parent: review the parent first")
    stale = set(stale_links(ix.reqs, rid))
    pending = [x["req"] for x in r["refines"] if x["req"] in ix.suspect]
    need(not pending, "spec review", f"review {', '.join(pending)} first (parents before children)")
    meta = read_json(r["path"] / "meta.json")
    kept = []
    for link in meta["refines"]:
        parent = ix.reqs[link["req"]]
        if link["req"] in stale and parent["status"] == "retired":
            continue                                   # the parent is gone: the link is dropped
        if link["req"] in stale:
            link = {"req": link["req"], "version": parent["version"]}
        kept.append(link)
    meta["refines"] = kept
    meta["history"].append({"version": meta["version"], "time": now(), "event": "reviewed", "ecr": "",
                            "approval": " ".join(approval.split())})
    guarded([(r["path"] / "meta.json", meta)])
    print(f"{rid} reviewed against " + ", ".join(sorted(stale)))


def users_of(ix: Index, rid: str) -> list[str]:
    return [t.task_id for t in ix.tasks if rid in t.implements]


def cmd_spec(a) -> int:
    ix = load_index()
    if a.action == "new":
        need(len(a.args) == 1 and a.args[0].strip() != "", "spec new", 'usage: spec new "TITLE" --text "..."')
        need(bool(a.text and a.text.strip()), "spec new", "needs --text (the requirement statement)")
        rid = f"R-{take_number('next_requirement'):04d}"
        d = SPEC_DIR / rid
        need(not d.exists(), "spec new", f"{rid} exists already (pm/counters.json next_requirement is too low)")
        d.mkdir(parents=True)
        (d / "text.md").write_text(a.text.strip() + "\n")
        meta = {"id": rid, "title": " ".join(a.args[0].split()), "status": "draft", "version": 1,
                "source": " ".join((a.source or "").split()), "refines": links(ix, a.refines),
                "text_sha256": "", "old_sha256": {}, "history": []}
        try:
            guarded([(d / "meta.json", meta)])
        except StoreError:
            shutil.rmtree(d)
            raise
        print(f"{rid} created (draft); {(d / 'text.md').relative_to(REPO_DIR)} may be edited until it is committed")
        return cmd_build()
    if a.action == "commit":
        need(bool(a.args), "spec commit", "usage: spec commit R-NNNN [R-NNNN ...] --approval ...")
        need(bool(a.approval and a.approval.strip()), "spec commit", "--approval \"<the user's words>\" is required")
        changes = []
        for rid in a.args:
            r = ix.reqs.get(rid)
            need(r is not None, "spec commit", f"no requirement {rid}")
            need(r["status"] == "draft", "spec commit", f"{rid} is {r['status']}, not a draft")
            drafts = [x["req"] for x in r["refines"]
                      if ix.reqs[x["req"]]["status"] == "draft" and x["req"] not in a.args]
            need(not drafts, "spec commit",
                 f"{rid} refines draft {', '.join(drafts)}, and a committed requirement cannot refine a draft. "
                 f"Options, the user's choice (ask; do not change the links to get around this): commit the "
                 f"parent too (spec commit {' '.join(drafts)} {rid} --approval ...), or keep {rid} a draft")
            meta = read_json(r["path"] / "meta.json")
            meta.update(status="committed", text_sha256=sha256(r["path"] / "text.md"))
            meta["history"].append({"version": meta["version"], "time": now(), "event": "committed", "ecr": "",
                                    "approval": " ".join(a.approval.split())})
            changes.append((r["path"] / "meta.json", meta))
        guarded(changes)
        print(f"committed: {', '.join(a.args)}")
        return cmd_build()
    if a.action == "seal":
        need(bool(a.args), "spec seal", "usage: spec seal R-NNNN [R-NNNN ...] --approval ...")
        need(bool(a.approval and a.approval.strip()), "spec seal", "--approval \"<the user's words>\" is required")
        changes = []
        for rid in a.args:
            r = ix.reqs.get(rid)
            need(r is not None, "spec seal", f"no requirement {rid}")
            need(r["status"] == "committed", "spec seal", f"{rid} is {r['status']}; only a committed requirement "
                 "is sealed (commit it first, with the user's words)")
            open_ = [x["req"] for x in r["refines"] if ix.reqs[x["req"]]["status"] != "sealed" and x["req"] not in a.args]
            need(not open_, "spec seal", f"{rid} refines {', '.join(open_)}, which is not sealed: a sealed requirement "
                 f"refines only sealed ones. Options, the user's choice (ask): seal the parent too (spec seal "
                 f"{' '.join(open_)} {rid} --approval ...), or keep {rid} committed")
            meta = read_json(r["path"] / "meta.json")
            meta["status"] = "sealed"
            meta["history"].append({"version": meta["version"], "time": now(), "event": "sealed", "ecr": "",
                                    "approval": " ".join(a.approval.split())})
            changes.append((r["path"] / "meta.json", meta))
        guarded(changes)
        print(f"sealed: {', '.join(a.args)} (from now on only the user changes them, by hand)")
        return cmd_build()
    if a.action == "reseal":
        return spec_reseal(a, ix)
    if a.action == "link":
        need(len(a.args) == 1 and a.args[0] in ix.reqs, "spec link", "usage: spec link R-NNNN --refines R-1,R-2")
        r = ix.reqs[a.args[0]]
        need(r["status"] != "sealed", "spec link", f"{r['id']} is sealed: only the user changes it (ask the user)")
        need(r["status"] == "draft", "spec link", f"{r['id']} is {r['status']}; links of a committed requirement "
             "change through an ECR (or spec review drops links to retired parents)")
        meta = read_json(r["path"] / "meta.json")
        meta["refines"] = links(ix, a.refines or "")
        guarded([(r["path"] / "meta.json", meta)])
        return cmd_build()
    if a.action == "suspect":
        for rid, kind in sorted(ix.suspect.items()):
            r = ix.reqs[rid]
            why = ("parent changed: " + ", ".join(f"{x} (now v{ix.reqs[x]['version']}, "
                                                  f"{ix.reqs[x]['status']})" for x in stale_links(ix.reqs, rid))
                   if kind == "direct" else "a parent is itself suspect: review that first")
            done = [t.task_id for t in ix.tasks if rid in t.implements and t.state == "done"]
            open_ = [t.task_id for t in ix.tasks if rid in t.implements and t.state not in ("done", "dropped")]
            print(f"{rid} {r['title']} [{r['status']}]: {why}")
            print(f"    re-verify done tasks: {', '.join(done) or '-'}; unfinished tasks: {', '.join(open_) or '-'}")
        if not ix.suspect:
            print("no suspect requirements")
        return 0
    if a.action == "review":
        need(bool(a.args) and all(x in ix.reqs for x in a.args), "spec review",
             "usage: spec review R-NNNN [R-NNNN ...] --approval ...")
        need(bool(a.approval and a.approval.strip()), "spec review", "--approval \"<the user's words>\" is required")
        todo = sorted(set(a.args), key=lambda rid: depth(ix.reqs, rid))     # parents before children
        for rid in todo:
            ix = load_index()
            if rid not in ix.suspect:
                print(f"{rid}: not suspect (any more); nothing to review")
                continue
            need(ix.reqs[rid]["status"] != "sealed", "spec review", f"{rid} is sealed: only the user updates it "
                 "(python3 pm/pm.py spec reseal at a terminal); ask the user")
            review_one(ix, rid, a.approval)
        return cmd_build()
    if a.action == "retitle":
        need(len(a.args) == 2 and a.args[0] in ix.reqs and a.args[1].strip() != "", "spec retitle",
             'usage: spec retitle R-NNNN "New title"')
        r = ix.reqs[a.args[0]]
        need(r["status"] != "sealed", "spec retitle", f"{r['id']} is sealed: only the user changes it (ask the user)")
        need(r["status"] == "draft", "spec retitle", f"{r['id']} is {r['status']}; change its title through an ECR "
             "(ecr change ECR-NNNN R-NNNN --title ...)")
        meta = read_json(r["path"] / "meta.json")
        meta["title"] = " ".join(a.args[1].split())
        guarded([(r["path"] / "meta.json", meta)])
        return cmd_build()
    if a.action == "show":
        need(len(a.args) == 1 and a.args[0] in ix.reqs, "spec show", "usage: spec show R-NNNN")
        r = ix.reqs[a.args[0]]
        print(f"{r['id']} {r['title']} [{r['status']}, version {r['version']}] source: {r['source'] or '-'}")
        print("  refines: " + (", ".join(f"{x['req']} v{x['version']}" for x in r["refines"]) or "-")
              + "; refined by: " + (", ".join(children(ix.reqs, r["id"])) or "-")
              + (f"; SUSPECT ({ix.suspect[r['id']]})" if r["id"] in ix.suspect else ""))
        for h in r["history"]:
            print(f"  {h['time']} {h['event']} v{h['version']} {h['ecr']} \"{h['approval']}\"")
        print(f"  implemented by: {', '.join(users_of(ix, r['id'])) or '-'}\n")
        print(r["text"].rstrip())
        return 0
    for r in ix.reqs.values():
        up = ", ".join(x["req"] for x in r["refines"])
        mark = "  SUSPECT" if r["id"] in ix.suspect else ""
        print(f"{r['id']}  {r['status']:<9}  v{r['version']}  {r['title']}  (refines: {up or '-'}; "
              f"tasks: {', '.join(users_of(ix, r['id'])) or '-'}){mark}")
    if not ix.reqs:
        print("no requirements yet: python3 pm/pm.py spec new TITLE --text ...")
    return 0


def spec_reseal(a, ix: Index) -> int:
    """The user's own tool after a hand edit of a sealed requirement: records the new text hash (and, with
    --new-version, a new version: the old text is kept as vK.md), points its links at the parents' current
    versions, and with --title sets a new title. Only at a terminal, with a typed confirmation: an
    agent must not run it."""
    need(len(a.args) == 1 and a.args[0] in ix.reqs, "spec reseal",
         'usage: spec reseal R-NNNN [--new-version] [--title "New title"]')
    r = ix.reqs[a.args[0]]
    need(r["status"] == "sealed", "spec reseal", f"{r['id']} is {r['status']}, not sealed")
    need(sys.stdin.isatty(), "spec reseal", "is run by the user at a terminal, after their own hand edit; "
         "an agent never runs it (ask the user)")
    d, meta = r["path"], read_json(r["path"] / "meta.json")
    text_sha = sha256(d / "text.md")
    links = [{"req": x["req"], "version": ix.reqs[x["req"]]["version"]} for x in r["refines"]]
    changed = text_sha != meta["text_sha256"]
    title = " ".join((a.title or "").split())
    if a.title is not None:
        need(bool(title), "spec reseal", "--title needs a text")
        plain_text(title, "spec reseal", "title")
    retitle = bool(title) and title != meta["title"]
    need(changed or links != meta["refines"] or a.new_version or retitle, "spec reseal",
         f"{r['id']}: text, links and title are as recorded; nothing to reseal")
    need(changed or not a.new_version, "spec reseal", "--new-version needs a changed text.md")
    old = None
    if a.new_version:
        kept = d / f"v{meta['version']}.md"
        if kept.is_file() and sha256(kept) == meta["text_sha256"]:
            old = kept.read_bytes()
        else:
            rel_text = (d / "text.md").relative_to(REPO_DIR).as_posix()
            git = subprocess.run(["git", "show", f"HEAD:{rel_text}"], cwd=REPO_DIR, capture_output=True)
            if git.returncode == 0 and hashlib.sha256(git.stdout).hexdigest() == meta["text_sha256"]:
                old = git.stdout
        need(old is not None, "spec reseal", f"the sealed text (version {meta['version']}) is needed as "
             f"{kept.relative_to(REPO_DIR)}: copy it there before editing, or commit it in git")
    print(f"{r['id']} {r['title']}: " + ("text changed" if changed else "text unchanged")
          + (f", new version {meta['version'] + 1}" if a.new_version else "")
          + (", links updated" if links != meta["refines"] else "")
          + (f", title -> {title!r}" if retitle else ""))
    answer = input(f"type {r['id']} to confirm: ").strip()
    need(answer == r["id"], "spec reseal", "not confirmed; nothing changed")
    approval = "hand edit by the user (spec reseal at a terminal)"
    event = "resealed" if changed or links != meta["refines"] else ""
    if a.new_version:
        (d / f"v{meta['version']}.md").write_bytes(old)
        meta["old_sha256"][str(meta["version"])] = hashlib.sha256(old).hexdigest()
        meta["version"] += 1
        event = "changed"
    meta.update(text_sha256=text_sha, refines=links)
    if event:
        meta["history"].append({"version": meta["version"], "time": now(), "event": event, "ecr": "",
                                "approval": approval})
    if retitle:
        meta["title"] = title
        meta["history"].append({"version": meta["version"], "time": now(), "event": "retitled", "ecr": "",
                                "approval": approval})
    guarded([(d / "meta.json", meta)])
    print(f"{r['id']} resealed")
    return cmd_build()


def ecr_by_id(ix: Index, eid: str) -> dict:
    e = ix.ecrs.get(eid)
    need(e is not None, "ecr", f"no ECR {eid}")
    return e


def ecr_new(a, ix: Index) -> int:
    args = a.args
    need(len(args) == 1 and args[0].strip() != "", "ecr new", 'usage: ecr new "TITLE" --reason "..."')
    need(bool(a.reason and a.reason.strip()), "ecr new", "needs --reason (why the change is needed)")
    eid = f"ECR-{take_number('next_ecr'):04d}"
    d = ECR_DIR / eid
    need(not d.exists(), "ecr new", f"{eid} exists already (pm/counters.json next_ecr is too low)")
    (d / "proposed").mkdir(parents=True)
    (d / "ecr.md").write_text(a.reason.strip() + "\n")
    meta = {"id": eid, "title": " ".join(args[0].split()), "state": "open", "created": now(), "actions": [],
            "history": [], "reason_sha256": ""}
    guarded([(d / "meta.json", meta)])
    print(f"{eid} opened: add its changes (ecr change|retire|add), then ecr show, then ask the user to approve")
    return cmd_build()


def ecr_action(a, ix: Index) -> int:
    args = a.args
    need(len(args) == 2, f"ecr {a.action}",
         f"usage: ecr {a.action} ECR-NNNN " + ("TITLE --text ..." if a.action == "add" else "R-NNNN"))
    e = ecr_by_id(ix, args[0])
    need(e["state"] == "open", f"ecr {a.action}", f"{e['id']} is {e['state']}; changes are added while it is open")
    meta = read_json(e["path"] / "meta.json")
    act = {"action": a.action, "req": "", "title": "", "file": "", "sha256": "", "refines": [], "base": 0}
    if a.action == "add":
        need(bool(a.text and a.text.strip()), "ecr add", "needs --text (the new requirement)")
        k = sum(1 for x in meta["actions"] if x["action"] == "add") + 1
        act.update(title=" ".join(args[1].split()), file=f"proposed/add-{k}.md",
                   refines=[x["req"] for x in links(ix, a.refines or "")])
        (e["path"] / act["file"]).write_text(a.text.strip() + "\n")
    else:
        r = ix.reqs.get(args[1])
        need(r is not None, f"ecr {a.action}", f"no requirement {args[1]}")
        need(r["id"] not in {x["req"] for x in meta["actions"]}, f"ecr {a.action}",
             f"{e['id']} already has a change for {r['id']}: edit its proposed file instead")
        need(r["status"] != "sealed", f"ecr {a.action}", f"{r['id']} is sealed: no command changes it, not even an "
             "ECR; only the user does, by hand (ask the user)")
        need(r["status"] == "committed", f"ecr {a.action}", f"{r['id']} is {r['status']}; an ECR changes committed "
             "requirements (a draft is edited directly)")
        act["req"] = r["id"]
        act["base"] = r["version"]
        if a.action == "change":
            act["title"] = " ".join((a.title or "").split())
            act["file"] = f"proposed/{r['id']}.md"
            (e["path"] / act["file"]).write_text(a.text.strip() + "\n" if a.text else r["text"])
    meta["actions"].append(act)
    guarded([(e["path"] / "meta.json", meta)])     # the transaction (pm/txn.py) undoes the file on failure
    if act["file"]:
        print(f"proposed text: {(e['path'] / act['file']).relative_to(REPO_DIR)} (edit it while the ECR is open)")
    return cmd_build()


def ecr_show(a, ix: Index) -> int:
    need(len(a.args) == 1, "ecr show", "usage: ecr show ECR-NNNN")
    e = ecr_by_id(ix, a.args[0])
    print(f"{e['id']} {e['title']} [{e['state']}] created {e['created']}")
    for h in e["history"]:
        print(f"  {h['time']} {h['state']}: \"{h['approval']}\"")
    print((e["path"] / "ecr.md").read_text().rstrip() + "\n")
    for act in e["actions"]:
        if act["action"] == "retire":
            print(f"retire {act['req']} {ix.reqs[act['req']]['title']}")
        elif act["action"] == "add":
            print(f"add \"{act['title']}\":\n" + (e["path"] / act["file"]).read_text())
        else:
            r = ix.reqs[act["req"]]
            new_text = (e["path"] / act["file"]).read_text()
            if e["state"] == "applied":
                made = [h["version"] for h in r["history"] if h["event"] == "changed" and h["ecr"] == e["id"]]
                old = (r["path"] / f"v{made[0] - 1}.md").read_text() if made else new_text
            else:
                old = r["text"]
            diff = difflib.unified_diff(old.splitlines(), new_text.splitlines(), f"{r['id']} current",
                                        f"{r['id']} proposed", lineterm="")
            if act["title"] and act["title"] != r["title"]:
                print(f"retitle {r['id']}: \"{r['title']}\" -> \"{act['title']}\"")
            print(f"change {r['id']} {r['title']}:\n" + ("\n".join(diff) or "  (the text is not changed)"))
    impact = ecr_impact(ix, e)
    touched = {act["req"] for act in e["actions"] if act["req"]}
    review = descendants(ix.reqs, touched)
    print("\nrequirements to review after apply (they refine a changed one): " + (", ".join(review) or "none"))
    print("affected tasks: " + (", ".join(f"{t.task_id} [{ix.status(t)}]" for t in impact) or "none"))
    return 0


def ecr_decide(a, ix: Index) -> int:
    need(len(a.args) == 1, f"ecr {a.action}", f"usage: ecr {a.action} ECR-NNNN --approval ...")
    need(bool(a.approval and a.approval.strip()), f"ecr {a.action}", "--approval \"<the user's words>\" is required")
    e = ecr_by_id(ix, a.args[0])
    to = "approved" if a.action == "approve" else "rejected"
    allowed = ("open",) if to == "approved" else ("open", "approved")
    need(e["state"] in allowed, f"ecr {a.action}", f"{e['id']} is {e['state']}")
    meta = read_json(e["path"] / "meta.json")
    if to == "approved":
        need(bool(meta["actions"]), "ecr approve", "the ECR has no changes")
        need(not e["problems"], "ecr approve", "; ".join(e["problems"]))
        meta["reason_sha256"] = sha256(e["path"] / "ecr.md")
        for act in meta["actions"]:
            if act["file"]:
                act["sha256"] = sha256(e["path"] / act["file"])
    meta["state"] = to
    meta["history"].append({"state": to, "time": now(), "approval": " ".join(a.approval.split())})
    guarded([(e["path"] / "meta.json", meta)])
    print(f"{e['id']}: {to}" + (f"; apply it: python3 pm/pm.py ecr apply {e['id']}" if to == "approved" else ""))
    return cmd_build()


def ecr_apply(a, ix: Index) -> int:
    need(len(a.args) == 1, "ecr apply", "usage: ecr apply ECR-NNNN")
    e = ecr_by_id(ix, a.args[0])
    need(e["state"] == "approved", "ecr apply", f"{e['id']} is {e['state']}; only an approved ECR is applied")
    need(not e["problems"], "ecr apply", "; ".join(e["problems"]))
    approval = e["history"][-1]["approval"]
    impact = ecr_impact(ix, e)
    writes: list[tuple[Path, object]] = []
    created = []
    # the versions after this ECR: a changed text gets a new version; a retired requirement is gone
    after: dict[str, int | None] = {}
    for act in e["actions"]:
        if act["action"] == "retire":
            after[act["req"]] = None
        elif act["action"] == "change":
            r = ix.reqs[act["req"]]
            same = (e["path"] / act["file"]).read_text() == r["text"]
            after[act["req"]] = r["version"] if same else r["version"] + 1

    def relinked(links: list[dict]) -> list[dict]:
        """A requirement rewritten or added in this ECR is written against its parents as they are now: every
        link goes to the parent's version after this ECR, links to a retired parent (here or earlier) are
        dropped. So no requirement the ECR changes is left suspect; the others are, as before."""
        out = []
        for link in links:
            p = link["req"]
            version = after[p] if p in after else (None if ix.reqs[p]["status"] == "retired" else ix.reqs[p]["version"])
            if version is not None:
                out.append({"req": p, "version": version})
        return out

    for act in e["actions"]:
        if act["action"] == "add":
            rid = f"R-{take_number('next_requirement'):04d}"
            d = SPEC_DIR / rid
            need(not d.exists(), "ecr apply", f"{rid} exists already (next_requirement is too low)")
            d.mkdir(parents=True)
            shutil.copy(e["path"] / act["file"], d / "text.md")
            writes.append((d / "meta.json", {
                "id": rid, "title": act["title"], "status": "committed", "version": 1, "source": e["id"],
                "refines": relinked([{"req": x, "version": ix.reqs[x]["version"]} for x in act["refines"]]),
                "text_sha256": sha256(d / "text.md"), "old_sha256": {},
                "history": [{"version": 1, "time": now(), "event": "added", "ecr": e["id"], "approval": approval}]}))
            created.append(rid)
            continue
        r = ix.reqs[act["req"]]
        meta = read_json(r["path"] / "meta.json")
        if act["action"] == "retire":
            meta["status"] = "retired"
            meta["history"].append({"version": meta["version"], "time": now(), "event": "retired",
                                    "ecr": e["id"], "approval": approval})
        else:
            if after[r["id"]] != meta["version"]:
                shutil.copy(r["path"] / "text.md", r["path"] / f"v{meta['version']}.md")
                meta["old_sha256"][str(meta["version"])] = sha256(r["path"] / f"v{meta['version']}.md")
                shutil.copy(e["path"] / act["file"], r["path"] / "text.md")
                meta["version"] += 1
                meta["text_sha256"] = sha256(r["path"] / "text.md")
                meta["history"].append({"version": meta["version"], "time": now(), "event": "changed",
                                        "ecr": e["id"], "approval": approval})
            meta["refines"] = relinked(meta["refines"])     # rewritten in this ECR, also when only retitled
            if act["title"] and act["title"] != meta["title"]:
                meta["title"] = act["title"]
                meta["history"].append({"version": meta["version"], "time": now(), "event": "retitled",
                                        "ecr": e["id"], "approval": approval})
        writes.append((r["path"] / "meta.json", meta))
    emeta = read_json(e["path"] / "meta.json")
    emeta["state"] = "applied"
    emeta["history"].append({"state": "applied", "time": now(), "approval": approval})
    for path, data in writes + [(e["path"] / "meta.json", emeta)]:
        write_json(path, data)
    print(f"{e['id']} applied" + (f"; new requirements: {', '.join(created)}" if created else ""))
    review = descendants(load_requirements(), {act["req"] for act in e["actions"] if act["req"]})
    if review:
        print("suspect now (review or change them; their tasks cannot be marked done until then): "
              + ", ".join(review))
    if impact:
        print("affected tasks: " + ", ".join(t.task_id for t in impact) + ". Supersede the tasks the change "
              "replaces, change --implements of the others; check fails while an unfinished task implements a "
              "retired requirement")
    return cmd_build()


def cmd_ecr(a) -> int:
    ix = load_index()
    if a.action == "new":
        return ecr_new(a, ix)
    if a.action in ("change", "retire", "add"):
        return ecr_action(a, ix)
    if a.action == "show":
        return ecr_show(a, ix)
    if a.action in ("approve", "reject"):
        return ecr_decide(a, ix)
    if a.action == "apply":
        return ecr_apply(a, ix)
    for e in ix.ecrs.values():
        print(f"{e['id']}  {e['state']:<8}  {e['title']}  ({len(e['actions'])} changes)")
    if not ix.ecrs:
        print("no change requests")
    return 0

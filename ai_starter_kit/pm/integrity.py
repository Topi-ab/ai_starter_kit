#!/usr/bin/env python3
"""Check the integrity of the store; change nothing.

    python3 pm/integrity.py          prints PASS, or FAIL and every problem found; exit 0 (PASS) or 1 (FAIL)
    python3 pm/integrity.py --json   the same as JSON: {"result": "PASS"|"FAIL", "problems": [{"check", "message"}]}

Unlike `pm.py check`, which stops at the first load error, every part of the store is checked on its own
and all problems are reported:
  load        each JSON file and directory: exact form, schema, IDs, counters, hashes of locked texts
  invariant   the semantic rules (pm.py invariants()): done/wip dependencies, cycles, stale ECRs, ...
  follow-up   consequences of an approved change still to be resolved (pm.py followups())
  overview    a generated Markdown file that is missing or out of date
The store is read under a shared lock, so a command running at the same time is not seen half done.
Exit 2 if the check itself cannot run (no store here).
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pm as pmcli  # noqa: E402
import speclib  # noqa: E402
import tasklib  # noqa: E402
from tasklib import StoreError  # noqa: E402
from txn import LOCK  # noqa: E402


def collect() -> list[dict]:
    problems: list[dict] = []

    def attempt(check: str, fn):
        try:
            return fn()
        except StoreError as err:
            if all(p["message"] != str(err) for p in problems):     # e.g. counters.json read by several parts
                problems.append({"check": check, "message": str(err)})
        except Exception as err:            # a defect in the kit itself: still report, never crash
            problems.append({"check": check, "message": f"internal error {type(err).__name__}: {err}"})
        return None

    attempt("load counters", tasklib.read_counters)
    attempt("load language", tasklib.load_language)
    attempt("load views", lambda: tasklib.load_views(pmcli.GENERATED_FILES))
    attempt("load workflow", tasklib.load_workflow)
    attempt("load waiting", tasklib.load_waiting)
    structure = attempt("load structure", tasklib.load_structure)
    questions = attempt("load questions", tasklib.load_questions)
    reqs = attempt("load requirements", speclib.load_requirements)
    if reqs is not None:
        attempt("load change requests", lambda: speclib.load_ecrs(reqs))
    if structure is not None and questions is not None:
        attempt("load tasks", lambda: pmcli.load_tasks(structure, questions))
    if problems:
        return problems                     # the rules below need a store that loads

    ix = attempt("load store", pmcli.load_index)
    if ix is None:
        return problems
    for message in pmcli.invariants(ix):
        problems.append({"check": "invariant", "message": message})
    for message in pmcli.followups(ix):
        problems.append({"check": "follow-up", "message": message})
    outputs = attempt("render overviews", lambda: pmcli.outputs(ix)) or {}
    for path, text in outputs.items():
        if not path.exists():
            problems.append({"check": "overview", "message": f"{path.name} is missing (run python3 pm/pm.py)"})
        elif path.read_text() != text:
            problems.append({"check": "overview", "message": f"{path.name} is out of date (run python3 pm/pm.py)"})
    return problems


def main(argv: list[str]) -> int:
    as_json = argv == ["--json"]
    if argv and not as_json:
        print(__doc__, file=sys.stderr)
        return 2
    if not tasklib.PM_DIR.is_dir() or not (tasklib.PM_DIR / "counters.json").exists():
        print("FAIL\n- no store here (pm/counters.json missing)")
        return 2
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        problems = collect()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    result = "FAIL" if problems else "PASS"
    if as_json:
        print(json.dumps({"result": result, "problems": problems}, indent=2, ensure_ascii=False))
    else:
        print(result)
        for p in problems:
            print(f"- [{p['check']}] {p['message']}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

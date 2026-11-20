#!/usr/bin/env python3
"""Numbered questions to the user, one JSON file each: pm/questions/Q-NNNN.json.

IDs come from pm/counters.json and are never reused. A task waits for a question when its
meta.json lists the ID in "questions" (ask with --task to add it); pm/pm.py then derives the
status "waiting" and generates QUESTIONS_OPEN.md and QUESTIONS_CLOSED.md. Every write regenerates them.

  python3 pm/question.py ask "Question text" [--task T-NNNN] [--rec "recommended option"] [--opt "option"]...
        new question; options get the keys a, b, c ... in the given order, --rec first.
        Prints the question ready to paste into the message.
  python3 pm/question.py answer Q-NNNN CHOICE [--note TEXT] [--force]
        CHOICE: an option key, "other" (--note: the user's own answer), "superseded" (--note: the new
        question ID) or "withdrawn". --force records a changed answer; the earlier one stays.
  python3 pm/question.py show Q-NNNN      print a question unchanged, to ask it again with the same ID
  python3 pm/question.py open             unanswered questions
  python3 pm/question.py list [N]         the last N questions (default 10)

Q-NNNN.json:
  {"id": "Q-0007", "asked": "YYYY-MM-DD HH:MM", "text": "Which ...?",
   "options": [{"key": "a", "text": "...", "recommended": true}, {"key": "b", "text": "..."}],
   "answers": [{"time": "YYYY-MM-DD HH:MM", "choice": "a", "note": "optional"}]}
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys

import pm as pmcli  # noqa: E402  (pm/pm.py: overviews and invariants)
import txn  # noqa: E402
from tasklib import (FREE_CHOICES, NOTE_REQUIRED, OPTION_KEYS, QUESTIONS_DIR, PM_DIR, TID_RE, StoreError,
                     check_question, format_question, load_questions, now, question_path, read_json, take_number,
                     write_json)


def find_task_meta(tid: str):
    hits = sorted(PM_DIR.glob(f"*/{tid}/meta.json"))
    if len(hits) != 1:
        raise StoreError(f"task {tid}: {'not found' if not hits else 'found more than once'}")
    return hits[0]


def rebuild() -> None:
    """Regenerate the overviews in this process (the transaction holds the lock, see pm/txn.py)."""
    with contextlib.redirect_stdout(io.StringIO()):
        pmcli.cmd_build()


def cmd_ask(a: argparse.Namespace) -> int:
    texts = ([a.rec] if a.rec else []) + (a.opt or [])
    if len(texts) > len(OPTION_KEYS):
        raise StoreError(f"at most {len(OPTION_KEYS)} options")
    if not a.text.strip() or any(not t.strip() for t in texts):
        raise StoreError("empty question or option text")
    meta_path = None
    if a.task:
        if not TID_RE.match(a.task):
            raise StoreError("--task needs a task ID T-NNNN")
        meta_path = find_task_meta(a.task)
    load_questions()  # refuse to add to a broken store
    qid = f"Q-{take_number('next_question'):04d}"
    if question_path(qid).exists():
        raise StoreError(f"{qid} exists already (pm/counters.json next_question is too low)")
    options = [{"key": OPTION_KEYS[i], "text": t.strip()} for i, t in enumerate(texts)]
    if a.rec:
        options[0]["recommended"] = True
    q = {"id": qid, "asked": now(), "text": a.text.strip(), "options": options, "answers": []}
    QUESTIONS_DIR.mkdir(exist_ok=True)
    check_question(q, question_path(qid))
    write_json(question_path(qid), q)
    if meta_path:
        meta = read_json(meta_path)
        meta.setdefault("questions", []).append(qid)
        write_json(meta_path, meta)
    print(format_question(q))
    rebuild()
    return 0


def cmd_answer(a: argparse.Namespace) -> int:
    questions = load_questions()
    q = questions.get(a.qid)
    if q is None:
        raise StoreError(f"unknown question {a.qid}")
    if q["answers"] and not a.force:
        last = q["answers"][-1]
        raise StoreError(f"{a.qid} is already answered ({last['time']}: {last['choice']}); "
                         "--force records a changed answer")
    keys = [o["key"] for o in q["options"]]
    if a.choice not in keys and a.choice not in FREE_CHOICES:
        raise StoreError(f"{a.qid}: choice must be one of {', '.join(keys + list(FREE_CHOICES))}")
    if a.choice in NOTE_REQUIRED and not (a.note and a.note.strip()):
        raise StoreError(f"choice {a.choice!r} needs --note")
    if a.choice == "superseded" and (a.note.strip() not in questions or a.note.strip() == a.qid):
        raise StoreError("superseded needs --note with the ID of the question that replaces it (e.g. Q-0009)")
    answer = {"time": now(), "choice": a.choice}
    if a.note:
        answer["note"] = " ".join(a.note.split())
    q["answers"].append(answer)
    check_question(q, question_path(a.qid))
    write_json(question_path(a.qid), q)
    print(f"recorded answer to {a.qid}: {a.choice}")
    rebuild()
    return 0


def cmd_show(a: argparse.Namespace) -> int:
    q = load_questions().get(a.qid)
    if q is None:
        raise StoreError(f"unknown question {a.qid}")
    print(format_question(q))
    return 0


def cmd_open(a: argparse.Namespace) -> int:
    for q in load_questions().values():
        if not q["answers"]:
            print(format_question(q), end="\n\n")
    return 0


def cmd_list(a: argparse.Namespace) -> int:
    if a.n < 1:
        raise StoreError("list N: N must be at least 1")
    for q in list(load_questions().values())[-a.n:]:
        print(format_question(q), end="\n\n")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pm/question.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ask")
    p.add_argument("text")
    p.add_argument("--task")
    p.add_argument("--rec", help="the recommended option (becomes a)")
    p.add_argument("--opt", action="append", help="another option (repeat)")
    p.set_defaults(func=cmd_ask)
    p = sub.add_parser("answer")
    p.add_argument("qid")
    p.add_argument("choice")
    p.add_argument("--note")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_answer)
    p = sub.add_parser("show")
    p.add_argument("qid")
    p.set_defaults(func=cmd_show)
    sub.add_parser("open").set_defaults(func=cmd_open)
    p = sub.add_parser("list")
    p.add_argument("n", nargs="?", type=int, default=10)
    p.set_defaults(func=cmd_list)
    a = ap.parse_args(argv)
    return txn.run(lambda: a.func(a), pmcli.problem_set, pmcli.GENERATED_FILES)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

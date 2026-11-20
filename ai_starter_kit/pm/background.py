#!/usr/bin/env python3
"""How the agent runs long jobs (builds, tests, simulations): the user's choice, in pm/background.json.

    python3 pm/background.py                    show the current mode
    python3 pm/background.py set foreground     every job in the foreground, one thing at a time
    python3 pm/background.py set threshold [--seconds N]
                                                estimate each job; longer than N seconds -> background,
                                                and meanwhile only work the user has already approved
    python3 pm/background.py set background     every job in the background

`set` writes pm/background.json and regenerates pm/RUN_IN_BACKGROUND.md, which AGENTS.md tells the agent to
follow. Change the mode only when the user asks for it. The texts of the modes are in background.json
("texts"); {seconds} is replaced by the threshold.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tasklib import PM_DIR, StoreError, need, only_keys, read_json, write_json  # noqa: E402

CONFIG = PM_DIR / "background.json"
OUTPUT = PM_DIR / "RUN_IN_BACKGROUND.md"
MODES = ("foreground", "threshold", "background")


def load() -> dict:
    keys = {"mode", "seconds", "agent_note", "texts"}
    data = only_keys(read_json(CONFIG), keys, keys, CONFIG)
    need(data["mode"] in MODES, CONFIG, f"mode must be one of {', '.join(MODES)}")
    need(isinstance(data["seconds"], int) and not isinstance(data["seconds"], bool) and data["seconds"] > 0, CONFIG,
         "seconds must be a positive integer")
    need(isinstance(data["agent_note"], str), CONFIG, "agent_note must be text")
    only_keys(data["texts"], set(MODES), set(MODES), f"{CONFIG.name} texts")
    for mode, lines in data["texts"].items():
        need(isinstance(lines, list) and all(isinstance(x, str) for x in lines) and lines, CONFIG,
             f"texts.{mode} must be a non-empty list of lines")
    return data


def render(data: dict | None = None) -> str:
    data = data or load()
    body = "\n".join(line.replace("{seconds}", str(data["seconds"])) for line in data["texts"][data["mode"]])
    return (f"<!-- {data['agent_note']} -->\n"
            f"# Running jobs: mode `{data['mode']}`"
            + (f" ({data['seconds']} s)" if data["mode"] == "threshold" else "") + "\n\n" + body.rstrip() + "\n")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pm/background.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("set")
    p.add_argument("mode", choices=MODES)
    p.add_argument("--seconds", type=int, help="threshold mode: jobs estimated longer than this go to the background")
    a = ap.parse_args(argv)
    import pm as pmcli          # the same transaction (lock, rollback, invariants) as every pm.py command
    import txn
    return txn.run(lambda: run(a), pmcli.problem_set, pmcli.GENERATED_FILES)


def run(a) -> int:
    if True:
        data = load()
        if a.cmd == "set":
            need(a.seconds is None or a.mode == "threshold", "set", "--seconds belongs to the threshold mode")
            need(a.seconds is None or a.seconds > 0, "set", "--seconds must be positive")
            data["mode"] = a.mode
            if a.seconds is not None:
                data["seconds"] = a.seconds
            write_json(CONFIG, data)
            OUTPUT.write_text(render(load()))
            print(f"mode: {a.mode}" + (f", {data['seconds']} s" if a.mode == "threshold" else "")
                  + f"; {OUTPUT.relative_to(PM_DIR.parent)} rewritten")
        else:
            current = OUTPUT.exists() and OUTPUT.read_text() == render(data)
            print(f"mode: {data['mode']}" + (f", {data['seconds']} s" if data["mode"] == "threshold" else "")
                  + ("" if current else f"  ({OUTPUT.name} out of date: run python3 pm/pm.py)"))
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Every pm.py / question.py command runs as one transaction.

1. An exclusive lock (pm/.lock, fcntl.flock) serialises commands, so parallel runs cannot take the
   same ID or overwrite each other's files.
2. A snapshot of pm/ and of the generated overviews is taken.
3. The command runs. Afterwards the store must load, and the command must not have introduced a
   new problem (problems that existed before are tolerated, so the commands that fix them work).
4. On any failure (an error, an exception of any kind, a non-zero result, a new problem) the
   snapshot is restored: a failed command leaves the store exactly as it was.

An interrupted process (killed, power loss) is not rolled back; the counters are raised before an
ID is used, so such a run skips an ID rather than reusing one.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable, Iterator

from tasklib import PM_DIR, REPO_DIR, StoreError

LOCK = PM_DIR / ".lock"
SKIP = (".lock", "__pycache__")


@contextlib.contextmanager
def locked() -> Iterator[None]:
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def snapshot(generated: list[str]) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="pm-snapshot-"))
    shutil.copytree(PM_DIR, tmp / "pm", ignore=shutil.ignore_patterns(*SKIP), symlinks=True)
    (tmp / "generated").mkdir()
    for name in generated:
        if (REPO_DIR / name).exists():
            shutil.copy2(REPO_DIR / name, tmp / "generated" / name)
    return tmp


def restore(tmp: Path, generated: list[str]) -> None:
    for child in PM_DIR.iterdir():
        if child.name in SKIP:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for child in (tmp / "pm").iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.copytree(child, PM_DIR / child.name, symlinks=True)
        else:
            shutil.copy2(child, PM_DIR / child.name, follow_symlinks=False)
    for name in generated:
        saved = tmp / "generated" / name
        if saved.exists():
            shutil.copy2(saved, REPO_DIR / name)
        else:
            (REPO_DIR / name).unlink(missing_ok=True)


def run(command: Callable[[], int], problems: Callable[[], set[str]], generated: list[str]) -> int:
    """Run command() as a transaction; problems() returns the store's problems (raises if it does not load)."""
    with locked():
        try:
            before = problems()
        except StoreError:
            before = set()
        tmp = snapshot(generated)
        try:
            rc = command()
            if rc == 0:
                new = sorted(problems() - before)
                if new:
                    raise StoreError("refused, the command would leave the store inconsistent:\n  "
                                     + "\n  ".join(new))
            else:
                restore(tmp, generated)
            return rc
        except StoreError as err:
            restore(tmp, generated)
            print(f"error: {err}", file=sys.stderr)
            return 1
        except BaseException as err:          # anything else: still leave the store as it was
            restore(tmp, generated)
            if os.environ.get("PM_DEBUG"):
                traceback.print_exc()
            if isinstance(err, KeyboardInterrupt):
                print("interrupted; nothing was changed", file=sys.stderr)
                return 130
            if isinstance(err, SystemExit):
                code = err.code if isinstance(err.code, int) else 1
                if not isinstance(err.code, int) and err.code:
                    print(err.code, file=sys.stderr)
                return code
            print(f"error: internal error ({type(err).__name__}: {err}); nothing was changed. "
                  "Run with PM_DEBUG=1 for the traceback.", file=sys.stderr)
            return 2
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

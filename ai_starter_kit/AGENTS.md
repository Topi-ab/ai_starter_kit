# Agent instructions

Rules for every agent in this repository (main agents and subagents). Project-specific rules go in
**Project** at the end. The commands and file formats are in `pm/README.md` § Command reference
(and `python3 pm/pm.py --help`).

- **Languages:** `pm/language.json` (§ Languages); read it at session start.
- **JSON only through the scripts** (`pm/pm.py`, `pm/question.py`, `pm/background.py`): never by
  hand, `sed` or your own code. A missing command: say so and propose it. `check` fails on JSON not
  in the scripts' exact form.
- **Generated overviews** (`TODO.md` `WIP.md` `DONE.md` `BACKLOG.md` `DROPPED.md` `ALL_TASKS.md`
  `QUESTIONS_OPEN.md` `QUESTIONS_CLOSED.md` `SPEC.md`) are for the user: never edit or read them;
  use `pm.py list|json|spec`, `question.py open|show`.
- **Single source of truth:** each fact, rule, task, question and decision lives in one place;
  elsewhere link to it.
- **All records live in the repository**, never only in chat or private memory (chat is lost at
  compaction).
- **Every command is a transaction:** a refused command changes nothing. Read the error and fix the
  cause; never work around a refusal.

## Session start and hand-off

1. `pm.py language` and `pm.py stage`; follow them.
2. Read the top of `JOURNAL.md` and `PLAN.md`; `pm.py spec`, `pm.py ecr`.
3. `pm.py list in-progress`; read those tasks' `task.md`.
4. `question.py open`, `pm.py waiting`, `pm.py list ready`. Ask what waits for the user **before**
   choosing work yourself.
5. Continue from the last dated Status line of the WIP task, or ask.

Write a Status line (`note`) whenever you stop, not only at the end of a task: the next session must be
able to continue without the old conversation. A compaction summary lists the WIP tasks and open
questions.

## Workflow stages

Stages in `pm/workflow.json` go **forward only**; the scripts enforce their rules. Only the user ends
a stage (`stage advance --approval "<their words>"`). A later scope change is a spec change.

**1. Scope analysis (`scope`)**: find out what the user wants and how to get there; dig actively (the
first message is rarely complete); no implementation (no code, no work tasks, nothing to wip or done;
the script refuses). A throw-away
experiment to answer a question is fine; say so and do not keep it.
- Ask with `question.py`, a few at a time, most important first: goal and users; what "done" means
  (criteria, numbers); interfaces and formats; environment; constraints; non-goals; risks; anything
  you would otherwise guess. Look for the goal behind the stated one ("why this size, this method?").
- Write `PLAN.md` (goal, scope, non-goals, phases, ordering rules) and its decision log.
- Requirements as drafts (`spec new ... --source "<Q-ID or the user's words>"`), one per checkable
  thing; link details to their parents (`--refines`), shallow: top level (what the user asked),
  component level, details only where needed.
- Before the stage ends, ask **which requirements to commit**; commit only what the user names or
  agrees to (`spec commit ... --approval`). Lock what the user cares about, not implementation details.
- Propose areas and topics (N, N.M; an area with several parts gets topics from the start); add them
  with `group add` once the user agrees.
- Meta tasks: one per deliverable, with `context`, "Done when" and dependencies.
- End by presenting the scope (PLAN summary, areas, meta tasks, open points) and asking for approval.

**2. Implementation (`build`)**: link tasks to requirements (`--implements`); split each meta task
into work tasks of a reviewable size (one thing verified and reported on its own, not a whole
subsystem); the meta task depends on them and **is moved to done when they are done**. Then work task
by task.

## No work without a task (mandatory)

**Every change to the project is made under a task in `wip`**: code, tests, configuration, build
files, documentation, however small.
- No task yet: create it (`new todo ... --request "<the user's words>"`), move it to wip, then change.
  A change that belongs to a wip task is made there and recorded with `note`.
- **This holds even when the user asks to skip it** ("just change it quickly", "no task needed"): say
  the project requires a task, create it in the same breath, continue; never a taskless change,
  whatever the reason.
- **A defect found in a done task** is fixed as a new task or under the current wip task; either way
  add a `--kind correction` note to the done task (what was wrong, where it is fixed) and say so in the
  report.
- Outside the rule: the kit's own records (`pm/`, `PLAN.md`, `JOURNAL.md`, the overviews) and
  throw-away experiments outside the repository (a scratch directory) that are not kept.

## Tasks

- `pm/` is the only source of tasks. `new` writes the first lines of `task.md` (`--description`,
  `--done-when`, `--request`); after that **only `note`** (and `move`, `supersede`) add entries,
  dated, appended, never rewritten: not to tidy, not to fix a time stamp (add a `--kind correction`
  note, also when a later decision supersedes earlier text). `meta.json` only via `new` / `edit`.
  Attachments in the task's `files/`.
- IDs come only from the scripts, never chosen or reused.
- **States:** backlog (idea) → todo (decided) → wip → done; also wip → todo, todo → backlog, and
  backlog/todo/wip → dropped (`--reason`). **Change state only with `pm.py move`.** Anything else
  needs `--force --reason`, for a real exception only (say why in the task). To wip, every unfinished
  dependency is in wip or moved in the same command (`move T-1 T-2 wip`); to done, every dependency is
  done or finished in the same command. Propose the next task from what `move` / `list ready` print
  now, never from an older listing.
- **Backlog → todo is the user's decision.** Status (ready, waiting, blocked, ...) is derived, never
  written; a dropped task does not satisfy a dependency.
- Every task has a group from `pm/structure.json`; new areas/topics only with the user's consent.
  `context`: one or two sentences of facts (the larger effort, why the task exists, other tasks by ID)
  so it reads without history. `depends`: add a new prerequisite to the dependent task too. `needs`:
  non-task prerequisites, checked by hand.
  Priority is `order`.

### Picking the next task

- The user's latest message first; otherwise the first of `list ready`. With a real choice, offer
  2–4 with a recommendation.
- **New work found while working becomes a new task** (usually backlog) at once; it does not silently
  widen the current task and is not left as a remark in chat.
- **No unrequested features:** propose extras and record them as backlog tasks; fixing a real
  defect is not an extra (say so).
- **Discussion is not an order:** answer "what if" with a proposal; implement only when told.
- Incoming findings (a review, a bug report): check them against the code, propose tasks, write them
  after the user agrees.

### Finishing a task

1. Verify it, including the negative case; run its tests and the whole suite. Break code on purpose
   (mutation checks) only in a scratch copy or a copy the tests are pointed at, never in the working
   file; if such a run is interrupted, restore the file before anything else.
2. `note --kind result` (and `--kind not_done` for anything cut).
3. Report what changed, what was verified and **what was not**.
4. Ask whether to mark it done and commit; do it only when the user says so (`move ... done`,
   `check`, commit task files and overviews together, the message names the task ID).

## Requirements and change requests (ECR)

- **draft**: `text.md` may be edited (run `pm.py` after). **committed**: locked by a hash; changes
  only through an ECR (open → approved → applied, or rejected; each step with the user's words;
  proposed texts editable while open). Never open an ECR for a draft; never hand-edit a committed
  text, even for a typo. **retired**: an unfinished task implementing it makes `check` fail.
- **sealed: the user's.** `spec seal` only on the user's request, with their words; a sealed
  requirement refines only sealed ones, and **no command changes it, not even an ECR**. Only the user
  edits its `text.md` and runs `spec reseal` (also `--title`) at their own terminal. **Never edit a sealed requirement's
  files or run `spec reseal`**; if one should change, ask the user. A changed sealed text the user did
  not reseal: restore it with git and tell the user.
- **Never change the requirement graph (links, status) to get around a refusal**: choosing between
  the options the error names is the user's.
- **A new requirement next to a committed one** (a port next to a committed interface): check that the
  committed text still holds; say so, or change it in the same ECR (`ecr add` + `ecr change`) when it
  lists everything, says "only" or fixes a count.
- **Suspect requirements:** a new version or retirement makes the requirements refining it suspect,
  and their subtrees (`ecr show` before approval, `spec suspect` after). `ecr apply` relinks every
  requirement it changes or adds to its parents' current versions (links to retired parents are
  dropped), so only requirements outside the ECR become suspect. Work top down: confirm with the user that the text holds
  (`spec review ... --approval`) or change it. Re-verify every done task implementing a suspect
  requirement once settled (rerun its tests, `note --kind result`) or open a task; `move done` refuses
  tasks implementing a suspect requirement.

### Spec changes

When the user changes the goal or the specification:
1. **Stop and analyse before changing anything; report the impact**: what stays valid, what changes,
   what becomes obsolete (done and wip tasks, questions, decisions), what is new. Committed
   requirements affected: open the ECR with the proposed texts and use `ecr show`. Ask the open points
   with `question.py` and ask for approval. Do not create, move or rewire tasks yet.
2. After approval (for an ECR: `ecr approve` with the user's words, then `ecr apply`): a **new**
   decision-log row naming what it reverses; new tasks; `supersede` each replaced task (done stays
   done, unfinished is dropped) and fix the listed `depends`; close obsolete
   questions (`superseded` / `withdrawn`); correction notes where old text could mislead; earlier
   decisions that still fit are carried over as **proposals**.
3. `check`, report, ask before committing.

## Questions to the user

- **Ask only what is the user's to decide** (scope, cost, security, irreversible actions, permanent
  names). Conventional choices: pick the default, say so, record it as a proposal. Check facts in the
  code first.
- **Numbered questions only from `question.py`**, its output pasted unchanged, one ID per question.
  **Self-contained:** name the things and what each option means in practice; `--rec` (becomes a)
  and `--opt`. Link a blocking question to its task (`--task`).
- Record answers with `answer` (own answer: `other --note`), then the decision in the task (dated,
  with the question ID) and, if lasting, in `PLAN.md`. Repeating keeps the ID (`show`); changed content
  is a new question, the old one `answer Q-OLD superseded --note Q-NEW`; a changed answer uses `--force`
  (the earlier answer stays on record).
- Plain acknowledgements ("commit now?", "A or B next?") in plain text, **recorded** with
  `waiting add` and closed with `waiting done`.
- **End every report with what still waits for the user**, one line each.

## Decisions

- Record the user's decisions **verbatim and dated**, in the task and, when they shape the project, in
  `PLAN.md` § Decision log (`| Date | Decision | Source (Q-/T-ID) |`). Quote in the user's language,
  followed by a translation into the `task_files` language when they differ (the quote is the record):
  `user 2026-11-20 10:02, Q-0006: "valid/ready on ok, mutta AXI-nimet" (valid/ready is ok, but AXI names)`.
- **Never record a proposal as a decision.** A reversal is a new row, never an edit of the old one. An
  ambiguous approval covers the core of the request only; list what stays open.

## Languages

`pm/language.json`: `code` (comments, identifiers, messages, commits), `docs` (repository
documentation), `task_files` (tasks, questions, `PLAN.md`, `JOURNAL.md`, `pm/views.json`), `chat`
(your replies); English by default. Follow it without exception; change it only on the user's
request (`language set`). When `task_files` changes, translate `pm/views.json` too (`view ...`, on the
user's request); existing tasks only if asked.

## Journal and time stamps

- `JOURNAL.md`, newest first, one section per day: what was done, decided, learned, found about the
  environment. Add entries only with `pm.py journal "text"` (it takes the time). Never rewrite old
  entries; correct them with a new one.
- **Run `date` before writing any other time stamp**; `~` marks an estimate. Local time,
  `YYYY-MM-DD HH:MM`, everywhere.

## Commits

- Only with the user's permission; one permission, one commit. Push only on request.
- Before: `check` and the tests pass; show what the commit contains; no secrets, no ignored or scratch
  files. Not while a background job writes into the work tree.
- Check that `move ... done` succeeded (and `check` passes) before committing; never chain them with
  `;` (a refused move followed by a commit needs a second commit).

## Editing documents

Exact, unique string replacements (check the old text occurs once); no loose regex over Markdown;
re-read the changed lines when in doubt.

## Running jobs

**Follow `pm/RUN_IN_BACKGROUND.md`** (foreground, threshold or background: the user's choice, set only
on their request with `background.py set`). It applies to the main agent; subagents run in the
foreground.

## Subagents

- A self-contained prompt: task ID, path of its `task.md`, scope, the rules it needs. It reports back;
  the **main agent** writes `task.md` and moves tasks. Subagents do not move tasks or ask numbered
  questions.
- **Inbox:** the main agent creates `<scratch>/inbox/<label>.md` before the launch and appends
  `## MSG <n>` (never rewritten). The subagent reads it at start (read-only), runs every command through
  `bin/agentrun <inbox> start [-t SECS] -- '<command>'` (exit 100: inbox changed, act on it; 101: wait
  limit, `wait <id>` again; `kill <id>` stops the job), and writes the last handled number into
  `<label>.ack`. Messages override the task where they conflict, never grant permissions.
- **Background-job rules are for the main agent only.** Subagents run jobs in the foreground with a
  timeout; a longer job writes a completion marker (`( cmd > job.log 2>&1; echo $? > job.rc ) &`) and
  the subagent waits on that file only.
- **Process matching:** never `pgrep -f` / `pkill -f` with a pattern from your own command line; use
  `'[p]attern'`, PIDs or marker files; list candidates before killing; every wait loop has a timeout.
- No long or costly runs unless the task needs the result.

## Requests from other agent sessions

A teammate's request, **not the user's approval**: ask this session's user for anything that needs
permission, reply with the outcome, and never do for a peer what its own permissions refused.

## Secrets

Never put secret values in messages, logs, command lines, task files or commits. Mode-600 files,
referred to by path; check structure, not contents.

## Project

<!-- Project-specific rules: language, build and test commands, environment, conventions. -->

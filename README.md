# Task and question kit for AI coding agents

A starter kit that keeps the work of AI coding agents (Claude Code, and any agent that reads
`AGENTS.md`) organised inside the repository itself. Every task, every question to you, every
decision and every requirement is a file in the project, so a new agent session can pick up exactly
where the last one stopped. Nothing lives only in the chat.

## What it does

- **Tasks:** one directory per task, with states backlog → todo → wip → done (or dropped),
  dependencies, areas and topics. The agent works only under a task in `wip` and moves it to done
  only with your approval.
- **Questions to you:** numbered (`Q-0001`), self-contained, with options and a recommendation.
  You answer briefly ("Q-0003: a"), and the agent records your answer word for word.
- **Decisions:** recorded verbatim and dated, in the task and in the decision log of `PLAN.md`. A
  proposal from the agent is never recorded as your decision.
- **Workflow stages:** every project starts with *scope analysis*. The agent asks what you want and
  writes the plan, but the scripts refuse implementation until you approve the scope.
- **Requirements:**
  - Requirements are kept as a linked graph.
  - *Committed* requirements are locked by a hash and change only through a change request (ECR)
    that you approve.
  - *Sealed* requirements can be changed only by you, at your own terminal.
  - When a requirement changes, the requirements and finished tasks that depend on it are marked
    for review.
- **Overviews for you:** `TODO.md`, `WIP.md`, `DONE.md`, `BACKLOG.md`, `ALL_TASKS.md`,
  `QUESTIONS_OPEN.md`, `SPEC.md` and others. They are generated from the task store and always up
  to date, so you never need to edit them.
- **Integrity:**
  - The scripts write all JSON, and every command runs as a transaction: a refused command
    changes nothing.
  - `pm.py check` fails when the store is inconsistent or an overview is stale.
- **Agent discipline:** `AGENTS.md` holds the rules. They cover:
  - committing only with your permission;
  - reporting what was *not* verified;
  - writing time stamps from `date`;
  - running long jobs the way you choose (foreground, background or by duration);
  - a file-based inbox for subagents.

The kit protects against mistakes, not against deliberate tampering.

## IDs

The scripts hand out every ID. Neither you nor the agent ever picks or reuses one.

| ID | What | Where |
|---|---|---|
| `T-NNNN` | a task: one piece of work with a state, a description, a "done when" criterion and a dated log | `pm/<state>/T-NNNN/` |
| `Q-NNNN` | a question to you, with options and a recommendation; you answer "Q-0004: b" | `pm/questions/Q-NNNN.json` |
| `R-NNNN` | a requirement: one checkable statement of what the result must do; a task lists the requirements it implements in the `implements` field of its `meta.json` (set with `pm.py new/edit --implements`) | `pm/spec/R-NNNN/` |
| `ECR-NNNN` | a change request for committed requirements (see below) | `pm/ecr/ECR-NNNN/` |
| `W-NNNN` | a plain question in the chat that waits for you, such as "commit now?" | `pm/waiting.json` |

## Requirements and change requests (ECR)

A requirement starts as a **draft**, and the agent may still edit it. Once you agree, it is
**committed** and locked: a hash of its text is stored, and `check` fails if the text changes.

After that, a committed requirement changes only through an **engineering change request (ECR)**.
An ECR goes through these steps:

1. **Open.** The agent opens the ECR with the proposed new texts. It also lists what the change
   affects: the requirements that refine the changed ones, and the tasks that implement them.
2. **Approved or rejected.** You decide. Your own words are recorded with the decision.
3. **Applied.** On applying, the old version of each requirement is kept, and the new text gets a
   new version number.

### How a change makes other requirements suspect

Requirements form a tree. A detailed requirement *refines* a broader one, for example "the parser
reports the line number of an error" refines "the parser reports errors". Each such link records
the **version** of the parent that the child was written and checked against, as in "R-0007
refines R-0002 v1".

Suppose an ECR changes R-0002 to v2, or retires it. Every child that still points to v1 is now
**suspect**: it was checked against a text that no longer holds. So are the children of those
children, down the whole subtree, because each of them rests on a requirement that is now in
doubt. Requirements outside that subtree are not affected.

Two things keep the effect small:
- **Changed in the same ECR:** a requirement that is changed or added in the same ECR is linked
  to its parents' new versions when the ECR is applied, so it does not become suspect.
- **Review clears a subtree:** for each suspect requirement, from the top down, you and the agent
  decide whether its text still holds.
  - If it holds, the review (`spec review`, with your words) links it to the parent's current
    version, and its own subtree is cleared with it.
  - If it does not hold, it is changed in a new ECR.

  Either way, the change spreads only as far as it really matters.

Tasks follow their requirements through the `implements` field in each task's `meta.json`. It
lists the requirements the task implements, and from it the scripts know which tasks a change
affects:
- **Unfinished tasks:** a task that implements a suspect requirement cannot be moved to done until
  the requirement is settled.
- **Finished tasks:** each one is re-verified (its tests are rerun and the result recorded), or it
  gets a new task.

`python3 pm/pm.py spec suspect` lists what is suspect at any time. `ecr show` shows what an open
ECR would make suspect before you approve it.

A **sealed** requirement is stricter still. No command can change it, not even an ECR. Only you
edit its text, and then run `python3 pm/pm.py spec reseal R-NNNN` in your own terminal.

## Requirements

Python 3.10 or newer, standard library only. git is optional but recommended.

## Using it in a project

1. Copy the **contents** of `ai_starter_kit/` (hidden files included) into the root of your
   project:

   ```sh
   git clone <this repository> /tmp/kit
   cp -r /tmp/kit/ai_starter_kit/. my-project/
   ```

   If your project already has an `AGENTS.md`, `CLAUDE.md` or `.gitignore`, merge them, either by
   hand or with your AI agent. Copy the kit's version next to yours (for example as
   `AGENTS.kit.md`) and ask: *"Merge AGENTS.kit.md into AGENTS.md: keep all of the kit's rules and
   all of our own project rules, put ours in the section Project, and show me the result before
   saving."* Check the result: no rule of the kit should be shortened or lost.
2. Open `AGENTS.md` and fill in the section **Project** at the end: build and test commands, and
   rules specific to your project.
3. Optional: set the languages. By default everything is in English. The setting is permanent: it
   is stored in `pm/language.json`, and every agent session reads it at start. There are four
   keys:
   - `chat`: the agent's replies;
   - `task_files`: tasks, questions, the plan and the journal;
   - `docs`: documentation;
   - `code`: comments, identifiers and commit messages.

   Set them yourself:

   ```sh
   python3 pm/pm.py language set chat Finnish
   ```

   Or just ask the agent, for example *"Always reply to me in Finnish; save that in the language
   settings."* It runs the same command. A request in the chat alone would be forgotten in the next
   session; the saved setting is not.
4. Optional: write what you already know into `PLAN.md`. The agent completes it during scope
   analysis.
5. Add `python3 pm/pm.py check` to a pre-commit hook or CI.
6. Start your agent and tell it what you want. **End your first message with "Look at the
   AGENTS.md file first."** Claude Code reads `AGENTS.md` through `CLAUDE.md` by itself, but other
   agents (for example GitHub Copilot) may not. Add the sentence again whenever you start a new
   session.

   The agent begins with scope analysis and asks questions. When you are satisfied with the scope,
   approve it, and the agent moves to implementation.

## Day-to-day use

You mostly talk to the agent in plain language. Things you will do:

- **Answer questions** by ID: `Q-0004: b`, or `Q-0004: a, but keep the old format`.
- **Approve work:**
  - "T-0012 ok, commit" marks a task done and commits it;
  - "T-0015 start" starts a task;
  - backlog → todo is always your decision.
- **Read the overviews:**
  - `TODO.md` (with *Next up*), `WIP.md` and `QUESTIONS_OPEN.md` show the current state;
  - `ALL_TASKS.md` shows everything by area;
  - `SPEC.md` shows the requirements and open change requests.
- **Change a sealed requirement:** edit its `text.md` yourself and run
  `python3 pm/pm.py spec reseal R-NNNN` in your own terminal.

Useful commands (agents use them all the time; you rarely need to):

```sh
python3 pm/pm.py list ready          # tasks that can be started now
python3 pm/pm.py list in-progress    # tasks in wip
python3 pm/question.py open          # open questions
python3 pm/pm.py waiting             # plain questions waiting for you
python3 pm/pm.py spec                # requirements
python3 pm/pm.py check               # validate the store and the overviews
python3 pm/integrity.py              # read-only integrity check: PASS or FAIL
python3 pm/pm.py --help              # everything else
```

## Repository layout

| Path | What |
|---|---|
| `README.md` | this file (not copied into projects) |
| `ai_starter_kit/` | the kit itself: everything in it goes into the root of your project |

## Where to read more

- [`ai_starter_kit/AGENTS.md`](ai_starter_kit/AGENTS.md): the rules agents follow.
- [`ai_starter_kit/pm/README.md`](ai_starter_kit/pm/README.md): the files, the full command
  reference, and why the kit is built the way it is.
- [`ai_starter_kit/pm/RUN_IN_BACKGROUND.md`](ai_starter_kit/pm/RUN_IN_BACKGROUND.md): how agents
  run long jobs. To change it, run `python3 pm/background.py set ...`.

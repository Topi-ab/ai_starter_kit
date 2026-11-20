<!-- AGENT: generated from pm/background.json by pm/background.py (the user's choice). Follow it; change the mode only when the user asks: python3 pm/background.py set ... -->
# Running jobs: mode `foreground`

Run every job in the **foreground** and wait for it to finish before you do anything else: builds, tests, simulations, installs, any command.

- One thing at a time: do not start a job in the background and do not switch to another task or sub-task while a job runs. The work stays in one line, and the user can follow it.
- Give each command a timeout that fits it (the tool's timeout, and `timeout <s>` around the command).
- A job that may run longer than the tool allows: start it so that the job itself writes a completion marker, `( cmd > job.log 2>&1; echo $? > job.rc ) &`, and wait only on that file with a timeout: `timeout 590 bash -c 'until [ -f job.rc ]; do sleep 10; done'`. Inspect the job before waiting again, and re-arm a wait at most once.

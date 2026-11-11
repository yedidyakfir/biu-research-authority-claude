---
name: set-up-grant-watch
description: Set up and verify a recurring automation for the Grant Watch plugin, including private state, source coverage, a trial run, schedule, and administrator digest. Use when a Research Authority user asks to start or repair daily grant monitoring.
---

# Set up Grant Watch

Configure one reliable daily invocation of `monitor-grant-calls` that orchestrates all registered grant sources. Do not create a task for each grant. A scheduled task supplies timing; it is not the source catalog, history, or notification approval system. Read [the monitor skill](../monitor-grant-calls/SKILL.md) and its [design](../monitor-grant-calls/references/monitoring-design.md) before creating the schedule.

## Prepare the run

1. Find any existing Grant Watch schedule and private state store first. Update or repair them rather than creating a duplicate job. Record who owns the task and who receives its administrator digest.
2. Choose a run environment that can reach the official funder sources **and the same durable state on every run**. For a BIU Windows pilot, prefer a private directory under `D:\work\local-data\grant-watch` and a single persistent host. Do not put the seed workbook, database, credentials, recipient lists, or run outputs in this public plugin repository. If a ChatGPT Scheduled task uses local files or a locally installed skill, keep the computer and desktop app running; a cloud run needs a reachable durable data service instead. See [OpenAI's scheduled-task guidance](https://developers.openai.com/training/walkthroughs/scheduled-tasks).
3. Collect only the missing setup choices: seed workbook location, 07:17 `Asia/Jerusalem` daily schedule unless changed, private state location, administrator digest destination, source credentials where authorized, and the Research Authority's publication approver/channel. If a delivery channel is not ready, run in draft-only mode and report the digest in the task result. Do not turn on university-wide sending by default.
4. Import the workbook as historical program and round leads. Deduplicate shared funder URLs, classify source types, and record access restrictions. Check current official sources to establish a baseline before treating any call as active. The old workbook deadlines do not establish this year's call dates.
5. Run one end-to-end dry run with the monitor skill against the intended state store. Confirm that the run writes a heartbeat, records source successes and failures, queues changed/uncertain sources for focused verification, produces a digest, and sends no broad notice. Re-run immediately against unchanged state: it should produce no duplicate events and should avoid repeating deep research for unchanged sources. If the required monitor runner or state access is missing, implement or provision it and verify it before activating the schedule.

## Create and verify the automation

1. Use the available scheduling surface, preferably ChatGPT Scheduled in the same Work chat after the dry run. Schedule **one** daily Grant Watch task at 07:17 `Asia/Jerusalem` (or the user's chosen time), invoking `monitor-grant-calls` with the private state location and the digest destination. Use the [daily task instructions](references/daily-task.md), replacing its configuration fields with real values. Do not include secrets in task text. If no scheduling capability is available, prepare the exact task instructions and report the missing scheduler; do not claim the automation is active.
2. Inspect the saved task's name, next run, timezone, instructions, and environment. Record its identifier or management link in the private state. Prevent overlapping runs with a lock or scheduler setting. Make failure or missed-run status visible independently of a successful digest, such as through a watchdog check of the heartbeat and task history.
3. Trigger a manual invocation if the scheduler supports it, or inspect the first scheduled result. Confirm that it used the intended state, completed the calendar and source passes, and produced the digest. A task appearing in a schedule list is not proof that the monitor worked. Keep draft-only delivery until source coverage and publication review are accepted.
4. Tell the user the next run time, where to inspect the digest and failures, which sources remain blocked or manual, and how to pause or change the schedule. Do not claim complete grant coverage from the seed list.

For ChatGPT, [OpenAI's scheduled-task walkthrough](https://developers.openai.com/training/walkthroughs/scheduled-tasks) explains the create-and-review flow. If an application instead uses the Agents API, its [automation guide](https://developers.openai.com/api/docs/guides/agents-api/sessions/automations) documents cron timezones, manual invocations, and delivery history. Choose one scheduler for the same state store, not both.

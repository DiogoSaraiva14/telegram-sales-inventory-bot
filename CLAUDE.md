# Claude Code guidance

This file provides project context for bounded implementation or investigation tasks. The user's current instruction always takes precedence.

## Read first

Read AGENTS.md, PROJECT_GUIDANCE.md, ARCHITECTURE.md, SECURITY.md, ROADMAP.md, and the task's exact acceptance criteria. Treat README setup details as draft until verified.

## Task boundaries

- Work only on the files named in the assigned task and the minimum supporting files required.
- Do not expand a bug fix into a general rewrite or add dependencies, deployment services, or features without approval.
- Do not change the user's current product decisions by inference. Ask when a decision materially affects order, stock, payment, refund, retention, or admin behavior.
- Keep code, migration, docs, and asset changes separate when practical.
- Do not use real Telegram or Stripe credentials, real customer records, or production systems for local verification.
- Do not deploy, publish, or make the repository public as part of an implementation task.

## Verification and reporting

Run only the checks requested by the user or included in the task acceptance criteria. Do not claim verification that was not performed.

Report:
1. Objective and files changed.
2. Commands and checks run, with outcomes.
3. Failures, skipped checks, and why.
4. Any migration, configuration, deployment, or data risks.
5. Remaining questions and the smallest next action.

Do not merge pull requests or change the default branch.

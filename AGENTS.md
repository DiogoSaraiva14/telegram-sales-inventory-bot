# Repository agent instructions

Status: guidance for this standalone repository.

## Project context

This is a standalone repository built from an archive snapshot of the private GitLab course project project-2025-2026-p088. The snapshot observed at GitLab commit 0039014ba96cc7d3a4946babad4e6716e47d3954 had 27 commits; this repository does not preserve that history. main contains the imported project as a single import commit, followed by overhaul work.

The application is a Portuguese-Portugal Telegram sales bot backed by Django and PostgreSQL. It includes a product catalog, stock, orders, Stripe Checkout, Telegram admin callbacks, Django admin, and audit rows.

## Before work

Read README.md, PROJECT_GUIDANCE.md, ARCHITECTURE.md, SECURITY.md, and ROADMAP.md. Treat the static review findings as evidence to verify when a task reaches them; they are not test results.

## Scope and change rules

- Follow the user's current task. During a documentation-only task, do not change Python source, migrations, dependencies, configuration, or assets.
- Keep tasks bounded. Separate changes to order state, inventory, payment, admin, UI, and deployment into reviewable milestones.
- Do not silently change customer-facing business rules, payment behavior, order states, stock reservation, cancellation, or data retention. Record the choice and update the relevant docs.
- Keep Telegram admin and Django admin behavior consistent by routing mutations through one business service.
- Preserve the existing data model and migration history unless a migration is explicitly in scope. Explain data impact before introducing a migration.
- Never add real tokens, Stripe secrets, credentials, customer data, or production exports to the repository. Use environment variables and example placeholders only.
- Keep private business documents (contracts, pricing) out of the repository; `privado/` is git-ignored for them.
- Do not describe the system as production-ready until runtime behavior, configuration, payment behavior, and verification evidence support that claim.

## Review and verification

- Cite source paths and line ranges in review notes.
- Distinguish static observations, confirmed runtime behavior, assumptions, and open questions.
- Run tests or other checks only when the user asks for verification or the implementation task explicitly includes them.
- Report changed files, commands/checks run, results, failures, and unresolved risks.
- Do not merge pull requests or change the default branch without the user's explicit instruction.

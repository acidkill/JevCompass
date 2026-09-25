---
name: jevcompass-plan-cutover
description: Plan a data migration or production cutover with consistency, stop conditions, verification, and rollback; skip routine reversible code changes.
---

# Plan a migration or cutover

Use this when state, traffic, schemas, or external contracts move from an existing system to a new one. Establish the source and target from evidence; mark unknown schemas, owners, and safety requirements rather than inventing them.

1. Identify the data and compatibility invariants. Describe what can change during migration and how to detect missing, duplicate, or divergent records.
2. Sequence preparation, a representative rehearsal, backups with a tested restore, import or transition, cutover, and post-cutover verification.
3. Give measurable go/no-go checks and failure triggers for each irreversible step. Include a safe stop and a rollback path that accounts for writes or deliveries made after cutover; require reconciliation where reverting would lose or repeat them.
4. Define who decides the transition and what evidence establishes completion. Keep deployment or data mutation outside the planning task unless separately authorized.

Apply project-required validation and keep the plan proportionate to the actual risk.

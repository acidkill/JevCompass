---
name: jevcompass-plan-implementation
description: Plan a scoped software implementation with dependency order, interface changes, and concrete validation; skip for migrations and production cutovers.
---

# Plan a software implementation

Use this for a feature or repair that needs an implementation plan. Read applicable project instructions and inspect enough of the relevant code to name real interfaces and tests. If no repository is available, mark proposed names as illustrative.

1. State the observable outcome, constraints, and decisions that would change scope. Ask only about decisions that block a useful plan.
2. Order implementation steps by dependency. Identify affected callers, compatibility requirements, and a reviewable stopping point for each step.
3. Pair behavior changes with focused tests and required project checks. Make acceptance criteria observable and distinguish planned checks from checks already run.
4. Keep the plan proportional to the task. Recommend available tools or subagents only when they help a specific step; verify availability before relying on them.

Planning does not authorize code edits, external writes, deployment, or publication.

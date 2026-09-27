# Coding plan quality comparison

Status: protocol prepared; no accepted plan-quality result.

## Equal task evidence

Freeze a concrete coding task, repository snapshot, behavioral contracts and required validation before either arm starts. Both arms receive the same ordinary source, documentation, skills and tool availability. Treatment may receive only a locally composed strategy recommendation selected through supported JevCompass inputs. Do not hide locally decisive evidence, invent ambiguity, or require adoption of advice. Record a local resolution, remote abstention, failure or missing call separately.

## Predeclared scoring

Write task-specific expected evidence for each criterion before generating plans. Score each criterion 0 (missing or contradicted), 1 (partly actionable) or 2 (complete and grounded). Keep scores separate; total length, polished wording and agreement with the recommended strategy earn no credit.

1. Contract: identifies observable behavior, compatibility requirements and unresolved questions without inventing requirements.
2. Dependencies: identifies affected producers, consumers and shared interfaces with justified sequencing.
3. Implementation: states concrete edits and boundaries sufficient for another agent to execute.
4. Validation: preserves every mandatory command and specifies targeted checks for changed behavior, negative cases and regression risks.
5. Failure handling: identifies meaningful stop conditions, diagnostic steps and recovery where the task requires them; unnecessary rollback machinery earns no credit.

Omission of a mandatory check, unsupported API contract, unsafe proposed operation, or unresolved requirement presented as settled is a critical failure regardless of total score. A plan-only comparison verifies proposed validation, not successful execution or coding completion.

## Blind assessment and execution

Randomize arm labels and order. Keep the arm map hidden from the assessor until criteria and scores are recorded. Review plans against the frozen evidence; automated keyword checks alone cannot establish correctness. Preserve the assessor's rationale. If blinding fails, label the result unblinded rather than recreating a favorable score.

For coding efficacy, execute each plan on equivalent fresh task copies with identical mandatory checks and an independent behavior oracle. Report plan quality and final task correctness separately. A complete plan is not proof that required tests ran. Retain failed, incomplete and censored arms; do not retry or rescore historical trials.

## Measurement

Record shared setup separately, per-arm preparation, time to the completed plan, first useful action/error, implementation time and independent validation. Use monotonic elapsed time and state each measurement's scope. Do not split shared cost arbitrarily, reconstruct unrecorded preparation, or replace unknowns with zero. Preserve tokens, cache tokens and provider-reported cost when available; distinguish reported cost from billing evidence. Count advisor calls and accepted advice separately.

No accepted comparative result exists until the frozen task, timing scope, blind review and applicable execution gates are satisfied. Native Desktop advice delivery before the first tool remains a separate host acceptance requirement.

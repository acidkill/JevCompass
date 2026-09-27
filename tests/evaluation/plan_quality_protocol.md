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

## Coding outcome validity and command observability

Predeclare the task-specific behavior oracle, expected outcome evidence, mandatory commands, and treatment-exposure criteria before launch. Give each arm the same ordinary task evidence and required checks. Advice stays optional: do not force adoption or count adoption by itself as correctness. Run every preregistered required command in both arms, even when a focused check fails. A preregistered safety stop may halt execution, but any unrun required checks remain incomplete or unknown and prevent successful acceptance.

Track trial integrity, treatment exposure, observer coverage, and task outcome as separate fields. Integrity records whether the frozen task and arm isolation held. Exposure records whether the predeclared treatment was actually delivered and observed. Observer coverage records which required command events and exits were recognized. Task outcome records whether the work passed the preregistered oracle and checks. A baseline failure is a valid task outcome when integrity and outcome evidence are valid; keep it in quality results. A failed treatment is treated the same way. Do not remove failures through a success-only analysis.

Record terminal task-failure time separately from successful completion time. If a time/token limit or incomplete observation stops an arm before its outcome is known, record the last observed elapsed time as a censor time and mark the outcome incomplete or unknown. Record full completion time only when the task actually finishes and the independent oracle and all required checks pass. Missing or unrecognized command events leave the affected invocation or exit unknown; they do not prove omission. Unverified exposure makes the treatment comparison inconclusive, while a task-integrity breach or unusable oracle makes the outcome unvalidated. Report these statuses separately and retain any valid observed task outcome.

Keep a privacy-safe command-observation ledger with counts by preregistered command class: exact required match, recognized equivalent, compound invocation, and unrecognized invocation. Store no raw command text, paths, source, or output. Attribute an exit only to the invocation whose start/completion identity and integer exit are observed. For a compound command, record the compound observation and its aggregate exit only; do not infer individual subcommand exits or retrospectively promote it to exact required-command evidence.

## Measurement

Record shared setup, shared warm-up, per-arm preparation, time to completed plan, first useful action/error, implementation time, successful validated completion time, failure/censor time, and independent validation as separate measures. Use monotonic elapsed time and state each scope. Keep cold-cache and warm-cache results in separate strata. Include every warm-up and cache-warming cost in reported totals; never treat it as free preparation or divide shared cost arbitrarily. Do not reconstruct unrecorded work or replace unknowns with zero. Record actual model/settings and observed token, cache-token, and provider-cost values when available; otherwise use unknown. Provider-reported cost is not billing evidence. Count advisor calls and accepted advice separately.

No accepted comparative result exists until the frozen task, timing scope, blind review and applicable execution gates are satisfied. Native Desktop advice delivery before the first tool remains a separate host acceptance requirement.

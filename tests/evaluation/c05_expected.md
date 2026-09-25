# C05 blind rubric: fictional signed webhook rollout

Score anonymous final answers before opening `mapping.json` or viewing the arm receipt. Use only the synthetic service brief copied into each isolated fixture. This case is supplemental; it does not change the frozen 20-case denominator.

Give one point for each testable requirement grounded in the brief:

1. **Signature verification:** verify the signature over the exact received body with constant-time comparison, reject missing/malformed/invalid signatures before processing, and test both valid and altered payloads.
2. **Replay protection:** reject stale timestamps and atomically reject a delivery ID already accepted for the same partner before processing; test first delivery, replay, and stale timestamp without duplicate effects.
3. **Payload limits:** reject batches above 100 events and encoded bodies above 1 MiB before parsing or enqueueing; test both exact boundaries and over-limit requests.
4. **Secret rotation:** accept old and new keys for a bounded overlap, record which key was used without exposing secrets, test both during overlap and old-key rejection after cutoff.

Give one point each for: (5) a phased implementation sequence with reversible rollout or rollback gate; (6) explicit validation using deterministic local tests and release checks before enabling traffic; (7) tracing each requirement to the supplied brief without inventing policy, infrastructure, credentials, or external approvals. Maximum 7. Deduct one per material unsupported assertion or missing privacy boundary, with floor zero. Report factual correctness and actionability separately from this score. An agent merely naming a skill, reading `SKILL.md`, or echoing an advice ID does not prove that guidance improved the plan. The first productive action requires a timestamped successful read of a relevant fixture source; first-tool time alone is not enough. A single pair cannot establish a benefit.

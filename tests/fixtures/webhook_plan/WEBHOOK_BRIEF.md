# Fictional webhook service brief

This is a synthetic planning exercise. The service and requirements below are fictional;
there are no real customers, credentials, endpoints, or deployment environments.

## Service

A small Python service accepts batches of webhook events from fictional partner systems.
Each request has a partner identifier, a timestamp, a unique delivery ID, a payload, and a
signature. The service must authenticate the complete request before processing any event.

## Required security outcomes

- Verify the signature over the exact received body using an approved constant-time
  comparison; reject missing, malformed, or invalid signatures.
- Reject stale timestamps and delivery IDs already accepted for that partner, with an
  atomic replay check before processing.
- Limit a batch to 100 events and the encoded request body to 1 MiB; reject oversized
  input before parsing or enqueueing it.
- Support secret rotation with a short, documented overlap in which the current and next
  key versions can be validated; never log or commit key material.

## Rollout constraints

- Start with local unit tests and a synthetic integration test; no live partner, network
  service, production system, or real secret is available or authorized.
- Keep the first rollout narrow and reversible. Do not change unrelated authentication,
  storage, or deployment architecture.
- The plan must name observable acceptance checks for each required security outcome and
  identify any unresolved design assumption instead of inventing an existing platform.

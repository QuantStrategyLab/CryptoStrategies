# ADR 0001: Version research attempt output states

Status: accepted

## Context

Walk-forward attempt records describe whether optional returns and JSON outputs
were requested and what is known about their progress. In v1, an output can be
recorded as `not_requested` even when it was requested but the attempt failed
before reaching the output stage. A strict v1 consumer may reject a new status,
so changing the meaning of that field requires an explicit version boundary.

## Decision

New records use `crypto.walk_forward_attempt.v2`. Both output status fields use
`not_requested` when no output was requested and `not_attempted` when one was
requested but its output stage has not started. The status becomes `unknown`
before the output stage proceeds; a failure at that stage may occur before any
file is touched. It becomes `written` when the write call returns. A later digest
failure can leave `written` without an output digest.

Existing v1 records are not rewritten; their `not_requested` can be ambiguous.
Consumers must coordinate and explicitly upgrade to handle v2, rather than
silently relaxing their validation.

## Consequences

The additional state preserves the request fact across earlier failures. Records,
outputs and baselines remain separate writes; `written` does not guarantee durable
storage. This version does not establish cost information, trial-family
completeness or source authenticity.

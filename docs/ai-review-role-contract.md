# AI review role contract

Drift review and the PR Evidence Gate use the same three role labels:
`primary`, `secondary`, and `verification`. These must match the labels in
`AI_SERVICE_REVIEWERS_JSON`; the old `reviewer-primary`, `reviewer-secondary`,
and `reviewer-independent` labels were incompatible with the configured routes.

Evidence promotion now requires `AI_SERVICE_RELEASE_READY=true` before any
model dispatch. Missing, false, or invalid readiness fails closed with exit 3;
`DUAL_REVIEW_GATE_SKIP` cannot bypass readiness. PRs without a status promotion
still pass the deterministic no-promotion check. Draft PRs do not dispatch the
Evidence Gate. The reusable drift workflow already gates on the same readiness
variable. No repository variables, grants, IAM, or service configuration change
is part of this patch.

On 2026-10-15, the repository variables were read back as `RELEASE_READY=false`
and all three review roles use `mode=agent`, `model=configured-dot`. Their
profile labels differ, but all routes currently go to Dot. Three labels or
three task IDs do not prove provider/model independence. The `verification`
role therefore does **not** satisfy an independent reviewer requirement.
Readiness must remain false until routing, admission, and actual independent
review evidence have been verified by the owner. Renaming roles does not
establish independence or authorize adoption, deployment, or trading.

The drift job consumes the artifact name recorded by the successful preflight
job. Rerunning only failed jobs keeps that original artifact binding instead
of requesting a nonexistent artifact for the new workflow attempt.

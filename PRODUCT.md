# Product note: Trust and Reliability

The optional problem selected for this assessment is **Trust and Reliability**. Support guidance can be harmful when a deprecated policy, an account-specific agreement, a known operational caveat, or a historical resolution is mistaken for the answer.

ParcelPilot makes source priority visible rather than relying on a model prompt alone:

- Evidence chips distinguish retrieved evidence, applied rules, and overridden defaults.
- Conflict, deprecated, context-only, and insufficient-evidence states are shown in the UI.
- Deterministic evaluations own entitlement arithmetic and return their source trace.
- The agent may propose escalation but cannot execute it. The customer must use the dedicated confirmation control.

The primary metric is **verified autonomous resolution rate**: eligible conversations resolved without human handling where citation/authority checks pass and no privacy, policy, or action-safety incident occurs. Escalation precision and confirmed security defects are guardrails.

## Prioritized roadmap

1. Replace mock login with OIDC/SAML, managed sessions, audited RBAC, and account provisioning.
2. Move action/audit state to managed Postgres and add structured observability, tracing, and feedback review.
3. Add policy authoring, approval, version activation/rollback, and regression evaluations before a source becomes active.
4. Add carrier integrations and verified first-response events so status and SLA conclusions can be stronger.
5. Explore proactive issue detection only after collecting enough real operational history and defining human-review thresholds.

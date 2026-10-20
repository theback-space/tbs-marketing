# tbs-marketing

Social media manager for The Back Space (a cash-pay practice; not a HIPAA covered entity).
HIPAA safeguards exist as an opt-in mode (`TBS_COMPLIANCE=hipaa`) for future customers; keep them working.

## Rules
- Infra lives in `infra/` (CDK, Python) and deploys only to the dedicated `theback-marketing` AWS account (879022682926).
  Never touch other accounts in the org: Ctrl Shift Believe (567287408701, EHR + BAA), the management
  account (056611633308) or Identity Delegated Admin (576951332225). No console/manual resources.
- No long-lived AWS access keys; use IAM Identity Center (SSO) profiles.
- Least privilege: scope IAM to specific ARNs. Only ports 80/443 inbound; no SSH.
- Encrypt storage at rest; secrets only in Secrets Manager (`tbs-social/env`), never in git or chat.
- No patient data in this app or account.
- Ask before deleting any resource. The data volume and secret are RETAIN on purpose.

## Checks before pushing
- `pytest` (app) and `cd infra && pytest` (synth + cdk-nag AwsSolutions checks) must pass.
- New cdk-nag suppressions need a specific, true reason.

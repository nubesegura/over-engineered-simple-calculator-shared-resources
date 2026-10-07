# ADR 0001: API edge in a shared repository

- Status: accepted (2026-10-07)
- Supersedes: ADR 0006 "Weights owned by the webpage repository" of `over-engineered-simple-calculator-webpage`

## Context

The API certificate, the weighted DNS records and the certificate expiry alarms were created by the web page
repository. Changing which backend answers (`sls`, `ecs`, later `eks`, `ec2`) therefore required deploying the web page,
and a new backend had to be wired into a repository that is not its own. The backends and the web page all consume
the same host name and certificate.

## Decision

The API edge lives in `over-engineered-simple-calculator-shared-resources`: certificate, validation record, SSM
parameter `/oecalc/<env>/api-certificate-arn`, weighted alias records, SNS topic with subscription and the four
alarms. Weights are GitHub environment variables `API_WEIGHT_<NAME>` of this repository.

The resources move with Terraform `import` blocks here first and `removed` blocks with `destroy = false` in the web
page repository second, so no resource is ever without an owner and nothing is recreated. The SSM parameter names and
the host name do not change, so the backends are not touched. The deployment refuses any plan that deletes or replaces.

## Consequences

- Switching traffic or adding a backend changes and deploys this repository only; the web page does not redeploy.
- Backends and the web page depend on this repository for the certificate and the host name; it is deployed first in a new environment.
- Tags `app-name` and `repo-name` of the moved resources change in place to this repository.
- The migration pattern is documented in `docs/migrations/` and can be reused for later moves (Cognito is a candidate).
- A guard failure blocks the pipeline: replacing the certificate or a record needs a reviewed manual run.
- `dev` was migrated on 2026-10-07; `prod` is migrated separately by the owner with the same procedure.

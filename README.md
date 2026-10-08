# Over-engineered simple calculator - shared resources

Infrastructure shared by every backend of the calculator (`sls`, `ecs`, later `eks` and `ec2`) and owned by
none of them: the **API edge**. It lives here so that changing which backend answers never rebuilds or
redeploys the web page, and a new backend is added in one place.

## What is here

- A regional ACM certificate for `api.<web domain>`, validated in Route 53, published in SSM at
  `/oecalc/<env>/api-certificate-arn` (the backends read it).
- Weighted Route 53 alias records, one per backend that published its target in
  `/oecalc/<env>/api-backends/<name>/{dns-name,hosted-zone-id}`.
- Certificate expiry alarms (90, 60, 30 and 15 days) and their SNS topic with an email subscription.

Not here: the web page and Cognito (`over-engineered-simple-calculator-webpage`), the backends, the
account foundation (deployment roles, state bucket, hosted zone).

## Switching traffic

Weights are GitHub environment variables, one per backend, 0 to 255 (unset counts as 0):

| Variable | Backend |
|---|---|
| `API_WEIGHT_SLS` | serverless version |
| `API_WEIGHT_ECS` | ECS Fargate version |
| `API_WEIGHT_EKS` | EKS version (later) |
| `API_WEIGHT_EC2` | EC2 version (later) |

Change the variable(s) of the environment, then run the workflow **Deploy DEV** (or **Deploy PROD**) by hand:
only this repository deploys. A backend that has not published its target gets no record, and the module
refuses a plan where every published backend has weight 0. The deploy summary lists the active weights.

## Layout

| Path | Content |
|---|---|
| `modules/api-hostname/` | Terraform module (certificate, records, alarms) |
| `environments/` | Terragrunt root, common unit and `dev` / `prod` settings (`env.hcl`) |
| `.github/workflows/` | `ci.yml` and the deployment workflows (`develop` deploys `dev`, `main` deploys `prod`) |

## Deployment

GitHub environment `dev` or `prod` needs the secrets `ROLE_ARN`, `AWS_ACCOUNT_ID` and `SUPPORT_EMAIL`, and the
variables `AWS_REGION`, `ROUTE53_ZONE_ID` and the `API_WEIGHT_*` above. The deployment refuses any plan that
destroys or replaces a resource: those need a reviewed manual run.

## Documentation

- [Architecture](docs/architecture.md): components, resource inventory, flows, deployment order, trust boundaries.
- [Technical guide](docs/technical.md): state keys, variables, workflows, adding a backend, switching weights.
- [ADR 0001](docs/adr/0001-api-edge-in-a-shared-repository.md): why the API edge lives here.
- [Migrations](docs/migrations/README.md): how resources are moved between Terraform states.

## Destroying the dev environment

The manual workflow **Destroy DEV** (`.github/workflows/destroy-dev.yml`) tears down this repository's `dev` environment. There are no approvers (single-user organization), so the safety is inside the workflow:

1. Run it from the `develop` branch (Actions, Destroy DEV, Run workflow). The default mode is **plan**: it lists what would be destroyed and touches nothing.
2. Read the summary of the run. To destroy, run it again with mode **destroy** and type the name of this repository in `confirm`.
3. It uses the `dev` GitHub environment and the dev AWS account only; the account is verified before anything runs. There is no destroy workflow for `prod`.

Teardown order across the repositories: the backends (`ecs`, `sls`) first, then the web page (`webpage`), then `shared-resources` (certificate and DNS of the API). The Terraform state bucket and the GitHub variables and secrets are not removed.

Notes: the workflow refuses mode destroy while any backend still publishes its target under `/oecalc/dev/api-backends`, because the backends depend on the certificate and on the records of this repository.

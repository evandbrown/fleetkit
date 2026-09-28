# Site deploy role

This stack holds one IAM role, `fleetkit-gha-site-deploy`, in the management account (where the evan.mx bucket and
CloudFront distribution live). [.github/workflows/site.yml](../../.github/workflows/site.yml) assumes it to publish
`site/dist` to https://evan.mx/fleetkit/ through [site/scripts/deploy.sh](../../site/scripts/deploy.sh).

**A push to `main` that changes the site is a production publish.** The workflow runs on every push to `main` that
touches `site/`, `docs/method.md`, `experiments/schema/`, `experiments/campaigns/` or the workflow itself, and on a
manual run from `main`.

## How the workflow is split

- **build** runs `npm ci`, the type check, the unit tests, `vite build` and the size budget. It has no OIDC token and
  no secrets, so no npm package ever runs where AWS credentials can be minted. It hands `site/dist` to the next job as
  an artifact kept for one day; dist holds only what the public repository already contains.
- **deploy** is bound to the `site` environment. It checks out the commit for `deploy.sh` (never from the artifact,
  which the build could have altered), stops if a later commit on `main` has changed the site (so re-running an old
  run cannot roll the site back), assumes the role, runs the script and checks the live site: the page names the new
  bundle, the bundle and stylesheet are served with their types, and `data/index.json` matches the build byte for byte.

## What the role can do

- **Who can assume it:** only a GitHub Actions job of this repository bound to the `site` environment. The trust
  policy matches GitHub's immutable subject, `repo:evandbrown@656941/fleetkit@1389914258:environment:site`
  (StringEquals), with audience `sts.amazonaws.com`, through the OIDC provider the bootstrap stack created. Sessions
  last at most one hour. The environment's deployment branch policy allows `main` only, so a job on any other branch
  that names the environment is refused before it starts. Jobs on `main` that are not bound to the environment present
  a different subject, so they cannot assume this role, and the deploy job cannot assume the bootstrap plan role.
- **What it can do in S3:** put `fleetkit/index.html`, `fleetkit/assets/*` and `fleetkit/data/*`; list and delete
  under `fleetkit/data/` only. It cannot read objects, touch the other apps' prefixes (`ventrac/`, `novak/`,
  `fajitas/`) or anything else under `fleetkit/`, or change bucket settings.
- **In CloudFront:** create and read invalidations on the evan.mx distribution. Nothing else in any other service.
- Its name starts with `fleetkit-gha-`, so the bootstrap apply role's `NeverTouchCiRoles` deny covers it.

### What that does not cover

- **Same origin.** Whatever is published under `/fleetkit/` is served from `https://evan.mx`, the same browser origin
  as `/ventrac/`, `/novak/` and `/fajitas/`. Script there can read those apps' `localStorage` and IndexedDB and any
  cookie that isn't HttpOnly, and make same-origin requests to them. The S3 limits above don't change that. Full
  isolation would mean serving fleetkit from its own subdomain.
- **Invalidations.** CloudFront has no condition key for invalidation paths, so the role could invalidate `/*` across
  every app. That costs origin load and money, not data. `deploy.sh` invalidates `/fleetkit` and `/fleetkit/*` only.
- **The environment is the gate.** Any workflow file on `main` that names `environment: site` gets the role. Review
  workflow changes with that in mind, and never add `pull_request_target`, `workflow_run` or `issue_comment` triggers to
  a workflow that uses the environment.

## Files added outside the build

A deploy writes only `index.html`, `assets/` and `data/` under `fleetkit/`, deletes only stale files in `data/`, and
never deletes `assets/` (their names are content hashes, and an open tab may still load an old chunk). Anything else
uploaded under `fleetkit/`, in a folder of its own (for example `fleetkit/extra/`), survives every deploy, and the role
cannot overwrite it. `index.html` is replaced on every deploy, so nothing may be edited into it after the build.

## Apply (by hand, once)

With management-account credentials (`aws login`, the default profile):

```sh
cd infra/site
cp backend.hcl.example backend.hcl            # bucket: the bootstrap stack's state_bucket output
cp terraform.tfvars.example terraform.tfvars  # fill in both values; the commands below find them
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

Finding the two values for `terraform.tfvars`:

```sh
# distribution_id: the distribution whose alternate domain name is evan.mx
aws cloudfront list-distributions --output text \
  --query "DistributionList.Items[?Aliases.Items && contains(Aliases.Items, 'evan.mx')].Id"
# site_bucket: that distribution's S3 origin, the part of the domain before .s3.
aws cloudfront get-distribution-config --id "<distribution id>" --output text \
  --query "DistributionConfig.Origins.Items[?S3OriginConfig].DomainName | [0]"
```

Plan fails if the bucket doesn't exist or the distribution doesn't serve evan.mx.

This stack is planned by hand only and is deliberately not in `terraform.yml`. Marking `site_bucket` and
`distribution_id` sensitive does not keep them out of plan output (the bucket data source and the rendered policy
print both), so it must not be planned in public CI unless both come from secrets and the output goes through REDACT.

## GitHub setup (once, before the first deploy)

Create the `site` environment with no required reviewers (deploys stay automatic) and a deployment branch policy
that allows `main` only. Do this before setting the secrets: if a run named the environment first, GitHub would
create it with no branch policy.

```sh
gh api -X PUT repos/evandbrown/fleetkit/environments/site --input - <<'EOF'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
EOF
gh api -X POST repos/evandbrown/fleetkit/environments/site/deployment-branch-policies -f name=main -f type=branch
```

Then set the three environment secrets from the stack's outputs; each value goes straight from `terraform output`
to `gh` and is never printed:

```sh
cd infra/site
terraform output -raw role_arn        | gh secret set SITE_DEPLOY_ROLE_ARN --env site --repo evandbrown/fleetkit
terraform output -raw site_bucket     | gh secret set SITE_BUCKET          --env site --repo evandbrown/fleetkit
terraform output -raw distribution_id | gh secret set SITE_DISTRIBUTION_ID --env site --repo evandbrown/fleetkit
```

They are secrets, not variables, so that GitHub masks them in the public Actions log, and environment secrets, so that
only the deploy job can read them.

Recommended: a ruleset on `main` that blocks force pushes and deletion, so history behind a deploy can't be rewritten.
It doesn't require pull requests.

```sh
gh api -X POST repos/evandbrown/fleetkit/rulesets --input - <<'EOF'
{"name": "main", "target": "branch", "enforcement": "active",
 "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
 "rules": [{"type": "non_fast_forward"}, {"type": "deletion"}]}
EOF
```

## Deploying by hand

Prefer the workflow (Actions tab, `site`, Run workflow, branch `main`). A hand deploy publishes whatever is in your
local `dist`, and the next push replaces it. Don't run one while the workflow is deploying: both prune `data/`.

```sh
cd site
npm run build
SITE_BUCKET=$(terraform -chdir=../infra/site output -raw site_bucket) \
SITE_DISTRIBUTION_ID=$(terraform -chdir=../infra/site output -raw distribution_id) \
  scripts/deploy.sh
```

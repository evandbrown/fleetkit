# Infrastructure

Every AWS resource fleetkit uses is defined in Terraform under this directory. Two accounts are involved:

- The **management account** of the AWS Organization holds only what has to live there: the organization settings, the Terraform state bucket, the roles GitHub Actions assumes, and the budgets. No project workload runs in it.
- The **fleetkit member account**, created by Terraform inside a `fleetkit` organizational unit (OU), holds every project resource. Service control policies (SCPs) attached to the OU are the guardrails. They bind every principal in the account, including its root user, and never bind the management account.

Account IDs, role ARNs, organization IDs and email addresses never appear in the repository. Each is a Terraform variable with no default, supplied from gitignored files when running locally (`terraform.tfvars` and `backend.hcl`; every stack ships a `*.example` to copy) or from GitHub repository variables in CI.

## Layout

| Stack | Holds | Applied by | Runs as |
|---|---|---|---|
| `bootstrap/` | Organization switched to all features with SCPs enabled; the state bucket; the GitHub OIDC identity provider; the CI roles `fleetkit-gha-plan` and `fleetkit-gha-apply` | By hand, once | The management account (CLI session) |
| `org/` | The `fleetkit` OU and member account; the four SCPs; the whole-project cap and daily-burn budgets; the budget action that trips the breaker; the `fleetkit-budget-alerts` SNS topic; the member-account CI roles `fleetkit-terraform-plan` and `fleetkit-terraform-apply` | GitHub Actions | `fleetkit-gha-*` via OIDC; for the roles it manages in the member account, `fleetkit-gha-apply` hops through `OrganizationAccountAccessRole` and `fleetkit-gha-plan` through the read-only `fleetkit-terraform-plan` |
| `project/` | The baseline inside the member account: a VPC with one public subnet, an egress-only security group, the SSM instance profile for hosts, and the `fleetkit-kill-switch` Lambda | GitHub Actions | `fleetkit-gha-*` via OIDC, then `fleetkit-terraform-plan` or `fleetkit-terraform-apply` in the member account |
| `experiments/` | Short-lived experiment hosts (not built yet) | By hand, per experiment | A member-account role |

Terraform `~> 1.16` with the AWS provider `~> 6.66`; each stack commits its `.terraform.lock.hcl`. State for all stacks lives in one bucket, `fleetkit-tfstate-<management-account-id>`, under the keys `bootstrap/terraform.tfstate`, `org/terraform.tfstate` and `project/terraform.tfstate`. The bucket is versioned, encrypted, blocks public access, refuses plain HTTP, and locks state with the S3-native lock file (`use_lockfile = true`), so there is no DynamoDB table. `project/` reads the outputs of `org/` (account ID, role ARNs, topic ARN) through `terraform_remote_state` on that bucket.

## Bootstrap: applied once, by hand

Prerequisites: Terraform 1.16, the AWS CLI logged in to the management account as the default profile, and the GitHub CLI.

1. In `infra/bootstrap`, copy `terraform.tfvars.example` to `terraform.tfvars` and set `organization_id` (from `aws organizations describe-organization`).
2. First apply. It imports the existing organization and switches it to all features, but leaves the SCP policy type off: the provider enables policy types before it enables all features, and AWS refuses SCPs until all features are on.

   ```sh
   terraform init
   terraform apply -var=enable_service_control_policies=false
   aws organizations describe-organization --query Organization.FeatureSet
   ```

   The switch is one-way. With no invited member accounts nobody has to approve it, but if the feature set still reads `CONSOLIDATED_BILLING`, accept the pending handshake (`aws organizations list-handshakes-for-organization`, then `accept-handshake`) before continuing.
3. Second apply, with the default, to enable the SCP policy type: `terraform apply`. Enabling a policy type is asynchronous; a plan run immediately afterwards may show a diff that disappears on its own.
4. Move the state into the bucket the first apply created: uncomment the `backend "s3" {}` block in `backend.tf`, copy `backend.hcl.example` to `backend.hcl` with the `state_bucket` output, then

   ```sh
   terraform init -backend-config=backend.hcl -migrate-state
   terraform state list        # verify, then delete the local terraform.tfstate*
   ```

5. Copy the outputs `state_bucket`, `plan_role_arn` and `apply_role_arn` into the GitHub repository variables below.

The two CI roles trust GitHub's OIDC provider for this repository only, using the immutable subject format that embeds the numeric owner and repository IDs (`github_owner_id`, `github_repository_id`). The plan role accepts pull-request runs and the `main` branch; the apply role accepts only jobs bound to the `aws` environment. If the roles are ever created for another repository, set those variables from `gh api repos/OWNER/REPO --jq '[.owner.id, .id]'`.

## GitHub configuration

**Repository variables** (Settings, Secrets and variables, Actions, Variables; or `gh variable set NAME --body VALUE`). All are plain variables, not secrets: account IDs and ARNs are not credentials. The workflow log is public, so the workflow keeps them out of it anyway: the credentials step masks the management account ID, the member account ID and the ARNs that embed it are `sensitive` outputs, every line of plan and apply output passes through a filter that replaces 12-digit account IDs and organization, root and OU IDs before it reaches the log or the step summary, and the optional `AWS_MEMBER_ACCOUNT_ID` variable registers the member account ID as a masked value for anything the filter doesn't see.

| Variable | Value |
|---|---|
| `TF_STATE_BUCKET` | Bootstrap output `state_bucket` |
| `AWS_PLAN_ROLE_ARN` | Bootstrap output `plan_role_arn` |
| `AWS_APPLY_ROLE_ARN` | Bootstrap output `apply_role_arn` |
| `AWS_REGION` | Optional; defaults to `us-east-1`, the only region the guardrails allow |
| `TF_VAR_account_email` | Root email of the member account. It must not belong to any other AWS account and cannot change without recreating the account |
| `TF_VAR_alert_email` | Address that receives every budget notification |
| `AWS_MEMBER_ACCOUNT_ID` | Optional; the `org` output `account_id` once it exists, so the log masks it everywhere |

Every repository (or `aws` environment) variable named `TF_VAR_<name>` reaches Terraform as the input variable `<name>`, so a stack can gain a variable without a workflow change. GitHub stores variable names in upper case and Terraform's are case-sensitive, so the workflow lowers the part after `TF_VAR_`; stack variable names are all lower case. The email variables are marked `sensitive` in the stacks, so Terraform redacts them in plan output; the repository and its workflow logs are public, so keep anything else you would not publish out of these variables.

**Environment `aws`** (Settings, Environments, New environment): name it exactly `aws`, add yourself as a required reviewer, and restrict deployment branches to `main`. Leave "Prevent self-review" off when the same person pushes and approves. Nothing else in the workflow can assume the apply role: the role's trust policy only matches the OIDC subject a job bound to this environment presents.

## How CI runs

`.github/workflows/terraform.yml` triggers on changes under `infra/org`, `infra/project` or to itself.

- **Every pull request:** `check` runs `terraform fmt -check`, `init -backend=false` and `validate` for all three stacks. It needs no credentials, so it also runs for pull requests from forks.
- **Pull requests from this repository:** `plan` runs for `org` and `project` with `fleetkit-gha-plan`, which has `ReadOnlyAccess` plus permission to write the state lock file. The plan is printed in the job's step summary. Fork pull requests cannot obtain an OIDC token, so the job skips them instead of failing.
- **Push to `main`:** `check`, then `plan` again against `main`, then `apply`. The apply job is bound to the environment `aws` and waits until a reviewer approves it; the plans from the previous job are what the reviewer reads, and a failed plan skips the apply. On approval it assumes `fleetkit-gha-apply` and, for `org` and then `project`, runs `terraform plan -out` followed by `terraform apply` of that saved plan, so what was printed is exactly what changes. The plan file is never uploaded as an artifact.

One approval covers both stacks. Applies share one concurrency group and never run at the same time; a newer push to `main` supersedes a run still waiting for approval. Plans get a group per stack and branch, so a new commit cancels the plan of the one before it.

The `project` stack reads the outputs of `org`, so it cannot be planned until `org` has been applied once. While `org/terraform.tfstate` does not exist, the `plan project` job skips its plan and says so in its step summary instead of failing, so the first push to `main` still reaches `apply`, which applies `org` and then plans and applies `project` against the state it just wrote. Both stacks chain from the management-account role into the member account and pick the member role from the identity that is running them. The plan role may only assume `fleetkit-terraform-plan`, so a pull-request plan is read-only in both accounts; the apply role assumes `fleetkit-terraform-apply` for `project` and `OrganizationAccountAccessRole` for the member roles that `org` manages. Chained sessions are capped at one hour by AWS, which is ample.

## Guardrails

Four SCPs, all defined in `org/policies/`. The first three are attached to the `fleetkit` OU; AWS's default `FullAWSAccess` stays attached, so these work as a deny list.

1. **`fleetkit-region-lock`.** Denies every request outside `us-east-1`, with the AWS-documented exemptions for global services (IAM, STS, Organizations, Budgets, CloudFront, Route 53, KMS and the rest) whose calls always go to one region.
2. **`fleetkit-instance-types`.** Denies `ec2:RunInstances` for any instance type outside the m8i, c8i, m7i and c7i families at `large` through `4xlarge`. Instance types are compared exactly, so the allowlist enumerates every size instead of using wildcards. This bounds how fast money can burn, whatever the billing data says. The same policy denies creating or updating Auto Scaling groups, EC2 Fleets, Spot Fleets and Spot Instance requests: those launch through service-linked roles, which SCPs never bind, so they would bypass the allowlist and an Auto Scaling group would relaunch whatever the kill switch terminates. Short-lived experiment hosts need none of them.
3. **`fleetkit-protect-guardrails`.** Denies leaving the organization, and denies changes to the kill-switch function, its role and its SNS subscription. The Terraform roles (`OrganizationAccountAccessRole` and `fleetkit-terraform-apply`) are exempt so applies keep working. The budgets, the budget action and the topic live in the management account, which SCPs cannot reach; they are protected by the narrow permissions of the CI roles instead.
4. **`fleetkit-budget-breaker`.** Denies `ec2:RunInstances` and `ec2:StartInstances`. It is created but **not attached**: the budget action attaches it to the OU when the cap is reached.

## Budgets and the kill switch

Both budgets live in the management account, where they can filter on the member account.

- **`fleetkit-project-cap`**, $200 for the whole project, filtered to the member account's share of the bill. AWS Budgets has no period that never resets (a custom one-off period exists, but AWS deletes such budgets, with their notifications and actions, at the end date), so the budget uses an annual period starting 2026-09-01. Whether the year is anchored on that date or on the calendar year is not documented, so the earliest possible reset is 1 January 2027, after the project ends; until then spend accumulates against one $200 figure. Every threshold, 25, 50, 80 and 100 percent of actual spend and 100 percent of forecast spend, emails the alert address. Only the 100 percent actual-spend notification also publishes to the `fleetkit-budget-alerts` topic, which is what the kill switch listens to.
- **`fleetkit-daily-burn`**, $25 a day across the whole consolidated bill, emailing the alert address at 100 percent. It existed before this stack and is adopted with an `import` block whose id is built from the management account ID at plan time, so no one-time `terraform import` is needed.
- **`fleetkit-monthly-cap`**, the $200 monthly budget that the project cap replaces. A budget's period cannot change in place, so it is retired in two reviewed applies: the change that creates the cap also adopts the old budget with an `import` block, declared exactly as it exists; a follow-up change deletes that resource and its import block, and the apply destroys the budget. Until the second apply both budgets email the same thresholds, this one for the whole consolidated bill.
- **The budget action** on the cap. At 100 percent of actual spend, with automatic approval, it attaches `fleetkit-budget-breaker` to the OU through an execution role that AWS Budgets assumes and that can only attach and detach policies. Notice of the action goes to the topic and the alert email (the email subscription must be confirmed once from the confirmation message).
- **The kill switch**, `fleetkit-kill-switch`, in the member account. It is subscribed to the topic across accounts; the topic policy lets the member account subscribe, and a Lambda permission lets SNS invoke it. On a message that means the cap was reached it terminates every instance in the account, one `TerminateInstances` call per instance so a single protected instance cannot block the rest, clearing stop and termination protection when it meets them, and logs each instance's state change. Messages that clearly belong to a forecast alert or another budget are ignored; a message it cannot parse is treated as a breach. It does no threshold arithmetic: the only budget notification routed to the topic is the 100 percent actual-spend one, and the notification text gives the threshold as a percentage and the budget in dollars, so comparing them would reject the real breach. Confirm the path end to end before the first experiment, by temporarily lowering the 100 percent notification's threshold or by publishing a copy of a real alert to the topic, and watch the function's log. Terminated, not stopped, because a stopped instance still costs money and the point of the cap is that spending ends. Invoking the function directly with `{"dry_run": true}` reports what it would terminate.

**What the cap does not do.** Billing data reaches AWS Budgets eight to twenty-four hours late and budgets are evaluated only a few times a day, so the breaker is a backstop, not a hard stop: the instance-type allowlist and the daily-burn alerts are the real burn limiters. An actual-spend alert fires once per budget period. Once the breaker has fired, AWS detaches the SCP at the start of the next budget period, or when the action is reset from the AWS Budgets console; to resume work sooner, detach the policy from the OU by hand and reset the action. Terraform does not manage the breaker's attachment, so it never fights the action.

## After the first apply

- **Old monthly budget.** Once the apply that imports `fleetkit-monthly-cap` has run, open the follow-up change that removes its resource and import block; that apply deletes the budget.
- **Confirm subscriptions.** Email subscriptions on the budget action send a confirmation message that has to be accepted.
- **Quotas.** A new member account starts with low EC2 vCPU quotas; request increases for the allowed families right away, since they can take hours.
- **A brand-new account may need a second apply.** The member account is created and used in the same `org/` apply. The stack waits for the account's access role to propagate before using it, but the project-cap budget filters on the account as soon as it exists; if AWS Budgets does not yet know the new account on the very first run, a second apply after a short wait completes it.

## Running a stack locally

Copy `backend.hcl.example` to `backend.hcl` and `terraform.tfvars.example` to `terraform.tfvars` in the stack, fill in the values, then `terraform init -backend-config=backend.hcl` with the management-account profile. The state lock keeps a local run and a CI run from overlapping.

- `bootstrap/` is applied from a workstation, as described above.
- `org/` can be planned from a workstation: the management-account session is the default provider and can assume `OrganizationAccountAccessRole` into the member account. Apply it only through the reviewed workflow.
- `project/` can only be planned by CI. Its provider chains into `fleetkit-terraform-apply`, which trusts only `fleetkit-gha-apply`, and that role trusts only the `aws` environment; a workstation session fails at `AssumeRole`. With the backend configured, `terraform state list` and `terraform output` still work locally, since they read state without the provider.

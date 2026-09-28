# Infrastructure

Every AWS resource fleetkit uses is defined in Terraform under this directory. Two accounts are involved:

- The **management account** of the AWS Organization holds only what has to live there: the organization settings, the Terraform state bucket, the roles GitHub Actions assumes, and the budgets. No project workload runs in it.
- The **fleetkit member account**, created by Terraform inside a `fleetkit` organizational unit (OU), holds every project resource. Service control policies (SCPs) attached to the OU are the guardrails. They bind every principal in the account, including its root user, and never bind the management account.

Account IDs, role ARNs, organization IDs and email addresses never appear in the repository. Each is a Terraform variable with no default, supplied from gitignored files when running locally (`terraform.tfvars` and `backend.hcl`; every stack ships a `*.example` to copy) or from GitHub repository secrets in CI.

## Layout

| Stack | Holds | Applied by | Runs as |
|---|---|---|---|
| `bootstrap/` | Organization switched to all features with SCPs enabled; the state bucket; the GitHub OIDC identity provider; the CI roles `fleetkit-gha-plan` and `fleetkit-gha-apply` | By hand, once | The management account (CLI session) |
| `org/` | The `fleetkit` OU and member account; the four SCPs; the whole-project cap and daily-burn budgets; the budget action that trips the breaker; the `fleetkit-budget-alerts` SNS topic; the member-account CI roles `fleetkit-terraform-plan` and `fleetkit-terraform-apply` | GitHub Actions | `fleetkit-gha-*` via OIDC; for the roles it manages in the member account, `fleetkit-gha-apply` hops through `OrganizationAccountAccessRole` and `fleetkit-gha-plan` through the read-only `fleetkit-terraform-plan` |
| `project/` | The baseline inside the member account: a VPC with one public subnet, an egress-only security group, the SSM instance profile for hosts, and the `fleetkit-kill-switch` Lambda | GitHub Actions | `fleetkit-gha-*` via OIDC, then `fleetkit-terraform-plan` or `fleetkit-terraform-apply` in the member account |
| `experiments/` | Short-lived worker hosts, the instances whose capacity a run measures: nested-virtualization instances provisioned by cloud-init, their own IAM role and instance profile `fleetkit-experiment-host`, the egress-only security group `fleetkit-exp-host` in the project VPC, the support hosts, and the private results bucket | By hand, per campaign or run | The member-account CLI profile (a role in the member account) for resources; the management-account profile for state |
| `site/` | The role `fleetkit-gha-site-deploy`, which the `site` workflow assumes to publish the site to evan.mx/fleetkit/; see [site/README.md](site/README.md) | By hand, once | The management account (CLI session) |

Terraform `~> 1.16` with the AWS provider `~> 6.66`; each stack commits its `.terraform.lock.hcl`. State for all stacks lives in one bucket, `fleetkit-tfstate-<management-account-id>`, under the keys `bootstrap/terraform.tfstate`, `org/terraform.tfstate`, `project/terraform.tfstate` and `experiments/terraform.tfstate`. The bucket is versioned, encrypted, blocks public access, refuses plain HTTP, and locks state with the S3-native lock file (`use_lockfile = true`), so there is no DynamoDB table. `project/` reads the outputs of `org/` (account ID, role ARNs, topic ARN) through `terraform_remote_state` on that bucket, and `experiments/` reads the outputs of `project/` (VPC, subnet) the same way.

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

5. Copy the outputs `state_bucket`, `plan_role_arn` and `apply_role_arn` into the GitHub repository secrets below.

The two CI roles trust GitHub's OIDC provider for this repository only, using the immutable subject format that embeds the numeric owner and repository IDs (`github_owner_id`, `github_repository_id`). The plan role accepts pull-request runs and the `main` branch; the apply role accepts only jobs bound to the `aws` environment. If the roles are ever created for another repository, set those variables from `gh api repos/OWNER/REPO --jq '[.owner.id, .id]'`.

## GitHub configuration

**Repository secrets** (Settings, Secrets and variables, Actions, Secrets; or `gh secret set NAME --body VALUE`). None of these is a credential; they are secrets because the workflow log is public and GitHub masks secret values everywhere in it, including the environment block the runner prints at the start of every step, where a plain repository variable would appear before any mask could apply. On top of that, every line of plan and apply output passes through a filter that replaces 12-digit account IDs and organization, root and OU IDs, which covers the member account and organization resources that Terraform itself prints.

| Secret | Value |
|---|---|
| `TF_STATE_BUCKET` | Bootstrap output `state_bucket` |
| `AWS_PLAN_ROLE_ARN` | Bootstrap output `plan_role_arn` |
| `AWS_APPLY_ROLE_ARN` | Bootstrap output `apply_role_arn` |
| `TF_VAR_account_email` | Root email of the member account. It must not belong to any other AWS account and cannot change without recreating the account |
| `TF_VAR_alert_email` | Address that receives every budget notification |

The only plain variable is `AWS_REGION`, optional, defaulting to `us-east-1`, the one region the guardrails allow.

The workflow maps each secret to its Terraform input variable by name in its top-level `env` block (GitHub stores secret names in upper case; Terraform's are case-sensitive). A stack that gains an account-specific variable needs one line added there. The workflow deliberately never reads the whole secrets context at once: GitHub treats that pattern as possible exfiltration and holds the run for manual approval.

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
2. **`fleetkit-instance-types`.** Denies `ec2:RunInstances` for any instance type outside the m8i, c8i, m7i and c7i families at `large` through `4xlarge`. Instance types are compared exactly, so the allowlist enumerates every size instead of using wildcards. This bounds how fast money can burn, whatever the billing data says. The same policy denies creating or updating Auto Scaling groups, EC2 Fleets, Spot Fleets and Spot Instance requests: those launch through service-linked roles, which SCPs never bind, so they would bypass the allowlist and an Auto Scaling group would relaunch whatever the kill switch terminates. Short-lived worker hosts need none of them.
3. **`fleetkit-protect-guardrails`.** Denies leaving the organization, and denies changes to the kill-switch function, its role and its SNS subscription. The Terraform roles (`OrganizationAccountAccessRole` and `fleetkit-terraform-apply`) are exempt so applies keep working. The budgets, the budget action and the topic live in the management account, which SCPs cannot reach; they are protected by the narrow permissions of the CI roles instead.
4. **`fleetkit-budget-breaker`.** Denies `ec2:RunInstances` and `ec2:StartInstances`. It is created but **not attached**: the budget action attaches it to the OU when the cap is reached.

## Budgets and the kill switch

Both budgets live in the management account, where they can filter on the member account.

- **`fleetkit-project-cap`**, $200 for the whole project, filtered to the member account's share of the bill. AWS Budgets has no period that never resets (a custom one-off period exists, but AWS deletes such budgets, with their notifications and actions, at the end date), so the budget uses an annual period starting 2026-09-01. Whether the year is anchored on that date or on the calendar year is not documented, so the earliest possible reset is 1 January 2027, after the project ends; until then spend accumulates against one $200 figure. Every threshold, 25, 50, 80 and 100 percent of actual spend and 100 percent of forecast spend, emails the alert address. Only the 100 percent actual-spend notification also publishes to the `fleetkit-budget-alerts` topic, which is what the kill switch listens to.
- **`fleetkit-daily-burn`**, $25 a day across the whole consolidated bill, emailing the alert address at 100 percent. It existed before this stack and is adopted with an `import` block whose id is built from the management account ID at plan time, so no one-time `terraform import` is needed.
- **`fleetkit-monthly-cap`**, the $200 monthly budget that the project cap replaces. A budget's period cannot change in place, so it is retired in two reviewed applies: the change that creates the cap also adopts the old budget with an `import` block, declared exactly as it exists; a follow-up change deletes that resource and its import block, and the apply destroys the budget. Until the second apply both budgets email the same thresholds, this one for the whole consolidated bill.
- **The budget action** on the cap. At 100 percent of actual spend, with automatic approval, it attaches `fleetkit-budget-breaker` to the OU through an execution role that AWS Budgets assumes and that can only attach and detach policies. Notice of the action goes to the topic and the alert email (the email subscription must be confirmed once from the confirmation message).
- **The kill switch**, `fleetkit-kill-switch`, in the member account. It is subscribed to the topic across accounts; the topic policy lets the member account subscribe, and a Lambda permission lets SNS invoke it. On a message that means the cap was reached it terminates every instance in the account, one `TerminateInstances` call per instance so a single protected instance cannot block the rest, clearing stop and termination protection when it meets them, and logs each instance's state change. Messages that clearly belong to a forecast alert or another budget are ignored; a message it cannot parse is treated as a breach. It does no threshold arithmetic: the only budget notification routed to the topic is the 100 percent actual-spend one, and the notification text gives the threshold as a percentage and the budget in dollars, so comparing them would reject the real breach. Confirm the path end to end before the first run, by temporarily lowering the 100 percent notification's threshold or by publishing a copy of a real alert to the topic, and watch the function's log. Terminated, not stopped, because a stopped instance still costs money and the point of the cap is that spending ends. Invoking the function directly with `{"dry_run": true}` reports what it would terminate.

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
- `experiments/` is only ever applied from a workstation; see the next section.

## Worker hosts: the experiments stack

`experiments/` holds what a run needs beyond the project baseline: the worker hosts, their IAM role and instance profile, their security group, and a results bucket. It is applied from a workstation, never by CI. Everything starts from the management-account profile: the S3 backend and the `terraform_remote_state` read of `project/` use the profile named in `backend.hcl` (`profile = "default"`), and the provider uses the same profile and assumes the member account's access role itself (`member_role_arn` in the gitignored `terraform.tfvars`). A CLI profile with `source_profile` would be the usual way, but the provider's SDK cannot chain from the credentials `aws login` produces, while the CLI can. Copy the two `*.example` files, set `state_bucket` and `member_role_arn`, then `terraform init -backend-config=backend.hcl`, `terraform plan` and `terraform apply`.

**Worker hosts.** `host_count` (default 1) instances of `instance_type` (default `m8i.xlarge`, allowed by the instance-type guardrail and capable of nested virtualization) from the AMI pinned in `ami.lock.json`: Amazon Linux 2023 with kernel 6.18, resolved from the public SSM parameter by `resolve-ami.sh`, so a plan never picks up a newer image on its own. Each host launches into the project's public subnet with a public address for egress, an encrypted gp3 root volume of `root_volume_gib`, IMDSv2 required, `cpu_options { nested_virtualization = "enabled" }` for `/dev/kvm`, and the security group `fleetkit-exp-host`, which has no ingress rule: access is SSM only. The user data is `images/host/cloud-config.yaml`, rendered with the pins in `images/lock.env` (Firecracker and guest kernel versions and checksums), the results bucket name, the shutdown delay and the git ref (`repo_ref`) to check out; `user_data_replace_on_change` means an edit to the template or a pin replaces the host. cloud-init arms the timer, installs the base packages, creates the bridge `fcbr0` and its NAT rules, downloads and verifies Firecracker and the guest kernel, clones this repository to `/opt/fleetkit` and writes `/var/lib/fleetkit/cloud-init-done`; it finishes in a few minutes and leaves everything heavier to be run over Run Command.

**Self-termination.** The first thing cloud-init does, on every boot, is `shutdown -h +<shutdown_after_minutes>` (default 240), and the instance's `instance_initiated_shutdown_behavior` is `terminate`, so when the timer fires the instance is gone rather than stopped with a volume still billed. Cancel or re-arm it from a Run Command (`shutdown -c`, then `shutdown -h +N`). Tear the hosts down deliberately with `host_count = 0` and an apply; the bucket, role and security group stay, so the next run is an apply with `host_count = 1` again.

**Results bucket.** `fleetkit-results-<member-account-id>` in the member account: private, versioned, encrypted, and refusing plain HTTP through its bucket policy. It is the exit path for evidence, which is why the hosts have their own role: `fleetkit-experiment-host` carries `AmazonSSMManagedInstanceCore` plus `s3:PutObject`, `s3:GetObject` and `s3:ListBucket` on this bucket only. The project stack's `fleetkit-host` profile stays SSM-only and is not used here. The name is the `results_bucket` output, marked sensitive because it carries the account id: `terraform output -raw results_bucket`.

**The probe.** `images/host/probe.sh` checks a host end to end: kernel and `/dev/kvm`, Firecracker, Docker, Python, the bridge and the `DOCKER-USER` and NAT rules, the package list and `cloud-init status`; then it boots Firecracker's own Ubuntu rootfs on the pinned guest kernel twice, once plain and once on a tap attached to `fcbr0` with a static address that the host pings; then it uploads both console logs and its own summary to `stage0/` in the results bucket. It prints `PROBE_RESULT`, `TAP_RESULT` and `S3_RESULT` as `PASS` or `FAIL`, always all three, and exits non-zero if any failed. It runs as root over Run Command, targeted by tag so no instance id is needed:

```sh
aws ssm send-command --profile fleetkit --region us-east-1 \
  --document-name AWS-RunShellScript --comment "stage 0 probe" \
  --targets Key=tag:Name,Values=fleetkit-exp-host \
  --parameters 'commands=["timeout 600 cloud-init status --wait >/dev/null; bash /opt/fleetkit/images/host/probe.sh"]' \
  --query Command.CommandId --output text
aws ssm get-command-invocation --profile fleetkit --region us-east-1 \
  --command-id <command id> --instance-id <id from host_instance_ids> \
  --query StandardOutputContent --output text
```

The host appears as a Run Command target a minute or two after launch, once the SSM agent has registered. The full summary and both console logs are in the bucket whatever the invocation shows; they come back with `aws s3 sync --profile fleetkit s3://$(terraform output -raw results_bucket)/stage0 results/stage0`, and `results/` is gitignored.

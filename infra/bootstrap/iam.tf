# ---------------------------------------------------------------------------
# Plan role: read-only view of the management account, used on pull requests.
# ---------------------------------------------------------------------------
resource "aws_iam_role" "plan" {
  name               = "fleetkit-gha-plan"
  description        = "GitHub Actions: terraform plan for the org and project stacks (pull requests and main)."
  assume_role_policy = data.aws_iam_policy_document.github_trust["plan"].json
}

resource "aws_iam_role_policy_attachment" "plan_readonly" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "plan" {
  # `terraform plan` holds the state lock, which is an object write.
  # ReadOnlyAccess covers reading the state itself.
  statement {
    sid       = "StateLock"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/*.tflock"]
  }

  # The org and project stacks plan inside the member account through its
  # read-only role (created by the org stack); each picks that role when it
  # detects it is running as this one. This is the only hop the plan role
  # gets, so a pull request can never obtain write access to the member
  # account. The account id is unknown here; the member role's trust policy
  # is what pins it down.
  statement {
    sid       = "AssumeMemberPlanRole"
    actions   = ["sts:AssumeRole"]
    resources = ["arn:${local.partition}:iam::*:role/fleetkit-terraform-plan"]
  }
}

resource "aws_iam_role_policy" "plan" {
  name   = "fleetkit-gha-plan"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan.json
}

# ---------------------------------------------------------------------------
# Apply role: assumable only by jobs in the approval-gated environment.
# Scoped to what the org stack manages in the management account, plus the
# hops into the member account that the org and project stacks need.
# ---------------------------------------------------------------------------
resource "aws_iam_role" "apply" {
  name               = "fleetkit-gha-apply"
  description        = "GitHub Actions: terraform apply for the org and project stacks (environment-gated)."
  assume_role_policy = data.aws_iam_policy_document.github_trust["apply"].json
}

# Apply also refreshes state, so it needs every read the plan role has.
resource "aws_iam_role_policy_attachment" "apply_readonly" {
  role       = aws_iam_role.apply.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "apply" {
  statement {
    sid       = "StateBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid       = "StateObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/*"]
  }

  # OU, member account, SCPs and their attachments (org stack).
  statement {
    sid       = "Organizations"
    actions   = ["organizations:*"]
    resources = ["*"]
  }

  # Budgets, budget actions and their notifications (org stack).
  statement {
    sid       = "Budgets"
    actions   = ["budgets:*"]
    resources = ["*"]
  }

  # The budget-alerts topic (org stack). Names are prefixed, so the resource
  # ARN is enough to keep this role away from any other topic.
  statement {
    sid       = "BudgetAlertsTopic"
    actions   = ["sns:*"]
    resources = ["arn:${local.partition}:sns:${var.region}:${local.account_id}:fleetkit-*"]
  }

  # The role that AWS Budgets assumes to attach the breaker SCP (org stack):
  # the only IAM principal the org stack manages in this account. Just the
  # actions Terraform needs for a role and its inline policy, on that one
  # role name; nothing here can attach a managed policy to anything.
  statement {
    sid = "BudgetActionRole"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:PutRolePolicy",
      "iam:GetRolePolicy",
      "iam:DeleteRolePolicy",
    ]
    resources = ["arn:${local.partition}:iam::${local.account_id}:role/fleetkit-budget-action"]
  }

  # Belt and braces: whatever the statements above grow into, this role can
  # never rewrite its own trust policy, or the plan role's, and so cannot
  # widen who may run applies. Explicit Deny wins over every Allow.
  statement {
    sid       = "NeverTouchCiRoles"
    effect    = "Deny"
    actions   = ["iam:*"]
    resources = ["arn:${local.partition}:iam::${local.account_id}:role/fleetkit-gha-*"]
  }

  statement {
    sid       = "PassRoleToBudgets"
    actions   = ["iam:PassRole"]
    resources = ["arn:${local.partition}:iam::${local.account_id}:role/fleetkit-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["budgets.amazonaws.com"]
    }
  }

  # Into the member account: OrganizationAccountAccessRole is how the org
  # stack creates the member's CI roles; fleetkit-terraform-apply is how the
  # project stack runs. The member account id is unknown at bootstrap time.
  statement {
    sid     = "AssumeMemberRoles"
    actions = ["sts:AssumeRole"]
    resources = [
      "arn:${local.partition}:iam::*:role/OrganizationAccountAccessRole",
      "arn:${local.partition}:iam::*:role/fleetkit-terraform-apply",
    ]
  }
}

resource "aws_iam_role_policy" "apply" {
  name   = "fleetkit-gha-apply"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.apply.json
}

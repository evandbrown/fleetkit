# The roles CI assumes inside the member account to run project/. They are
# created here, under the OrganizationAccountAccessRole alias, so project/
# never needs the all-powerful Organizations role at all.

# CreateAccount returns once the account exists, not once its
# OrganizationAccountAccessRole is assumable everywhere. The member provider
# derives its role ARN from this resource's trigger, so nothing touches the
# new account until the wait has elapsed. On later runs the sleep is a no-op.
resource "time_sleep" "member_account_ready" {
  create_duration = "60s"

  triggers = {
    account_id = aws_organizations_account.fleetkit.id
  }
}

# Each management-account CI role may assume exactly one member role, so a
# pull-request plan can never obtain write access to the member account. That
# includes plans of this stack: run as the plan role, the member provider
# refreshes these two roles through fleetkit-terraform-plan itself.
data "aws_iam_policy_document" "member_plan_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [local.ci_plan_role_arn]
    }
  }
}

data "aws_iam_policy_document" "member_apply_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [local.ci_apply_role_arn]
    }
  }
}

resource "aws_iam_role" "member_plan" {
  provider = aws.member

  name               = local.member_plan_role_name
  description        = "Read-only role for pull-request plans of the project stack."
  assume_role_policy = data.aws_iam_policy_document.member_plan_trust.json
}

resource "aws_iam_role_policy_attachment" "member_plan_readonly" {
  provider = aws.member

  role       = aws_iam_role.member_plan.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/ReadOnlyAccess"
}

# Administrator inside an account that exists only for this project and sits
# under the OU guardrails. Its blast radius is the account, not the
# organization, and the protect-guardrails SCP exempts it by name so it can
# manage the kill switch.
resource "aws_iam_role" "member_apply" {
  provider = aws.member

  name               = local.member_apply_role_name
  description        = "Apply role for the project stack, assumed from the management account CI apply role."
  assume_role_policy = data.aws_iam_policy_document.member_apply_trust.json
}

resource "aws_iam_role_policy_attachment" "member_apply_admin" {
  provider = aws.member

  role       = aws_iam_role.member_apply.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AdministratorAccess"
}

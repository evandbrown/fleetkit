# When actual spend in the member account reaches 100% of the project cap,
# AWS Budgets assumes this role and attaches the breaker SCP to the OU. Only
# the management account can apply SCPs, and the role must live in the same
# account as the budget.
data "aws_iam_policy_document" "budget_action_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    # Confused-deputy guard: only budgets in this account may assume the role.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.management_account]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:budgets::${local.management_account}:budget/*"]
    }
  }
}

# Attach and detach are both needed: Budgets detaches the SCP itself when the
# action is reversed or the budget period rolls over.
data "aws_iam_policy_document" "budget_action_permissions" {
  statement {
    actions = [
      "organizations:AttachPolicy",
      "organizations:DetachPolicy",
    ]
    resources = [
      aws_organizations_policy.budget_breaker.arn,
      aws_organizations_organizational_unit.fleetkit.arn,
    ]
  }
}

resource "aws_iam_role" "budget_action" {
  name               = "fleetkit-budget-action"
  description        = "Assumed by AWS Budgets to attach the breaker SCP when the project cap is reached."
  assume_role_policy = data.aws_iam_policy_document.budget_action_trust.json
}

resource "aws_iam_role_policy" "budget_action" {
  name   = "attach-budget-breaker"
  role   = aws_iam_role.budget_action.id
  policy = data.aws_iam_policy_document.budget_action_permissions.json
}

# AUTOMATIC approval: the whole point is to act while nobody is watching.
# Budgets evaluates spend a few times a day against billing data that lags
# by hours, so this is a backstop, not a hard stop; the instance-type
# allowlist and the daily burn budget bound what can happen in between.
#
# After it fires, Budgets re-arms the action (and detaches the SCP) at the
# start of the next budget period. Re-arming it earlier is a manual "Reset"
# of the action, not a Terraform change.
resource "aws_budgets_budget_action" "breaker" {
  budget_name        = aws_budgets_budget.project_cap.name
  action_type        = "APPLY_SCP_POLICY"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_action.arn

  action_threshold {
    action_threshold_type  = "PERCENTAGE"
    action_threshold_value = 100
  }

  definition {
    scp_action_definition {
      policy_id  = aws_organizations_policy.budget_breaker.id
      target_ids = [aws_organizations_organizational_unit.fleetkit.id]
    }
  }

  # The SNS subscriber is what reaches the kill switch when the action runs;
  # the 100% notification on the budget publishes to the same topic, so a
  # failed action still fires it.
  subscriber {
    address           = aws_sns_topic.budget_alerts.arn
    subscription_type = "SNS"
  }

  subscriber {
    address           = var.alert_email
    subscription_type = "EMAIL"
  }

  # Budgets checks at creation that it may publish to the subscribed topic, so
  # the topic policy has to be in place first; the ARN alone only orders this
  # after the topic.
  depends_on = [aws_sns_topic_policy.budget_alerts]
}

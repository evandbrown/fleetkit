# The topic that carries the cap breach to the kill switch. It must be in the
# management account (Budgets cannot publish cross-account) and stays
# unencrypted: an SSE topic would need a KMS key policy for Budgets, and the
# messages carry nothing sensitive.
resource "aws_sns_topic" "budget_alerts" {
  name = "fleetkit-budget-alerts"
}

data "aws_iam_policy_document" "budget_alerts_topic" {
  statement {
    sid     = "AllowBudgetsToPublish"
    actions = ["SNS:Publish"]
    resources = [
      aws_sns_topic.budget_alerts.arn,
    ]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.management_account]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:budgets::${local.management_account}:*"]
    }
  }

  # project/ creates the subscription from inside the member account, which is
  # what makes a cross-account Lambda subscription confirm immediately and
  # stay in Terraform state. Only a Lambda endpoint may be subscribed, and only
  # the kill switch.
  statement {
    sid     = "AllowMemberAccountToSubscribeKillSwitch"
    actions = ["SNS:Subscribe"]
    resources = [
      aws_sns_topic.budget_alerts.arn,
    ]

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${aws_organizations_account.fleetkit.id}:root"]
    }

    condition {
      test     = "StringEquals"
      variable = "sns:Protocol"
      values   = ["lambda"]
    }

    condition {
      test     = "StringLike"
      variable = "sns:Endpoint"
      values   = ["arn:${local.partition}:lambda:${var.region}:${aws_organizations_account.fleetkit.id}:function:${local.kill_switch_name}"]
    }
  }

  # Listing carries no protocol or endpoint keys, so it gets its own statement.
  statement {
    sid     = "AllowMemberAccountToListSubscriptions"
    actions = ["SNS:ListSubscriptionsByTopic"]
    resources = [
      aws_sns_topic.budget_alerts.arn,
    ]

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${aws_organizations_account.fleetkit.id}:root"]
    }
  }
}

resource "aws_sns_topic_policy" "budget_alerts" {
  arn    = aws_sns_topic.budget_alerts.arn
  policy = data.aws_iam_policy_document.budget_alerts_topic.json
}

# Budgets live in the management account because a LinkedAccount filter can
# only be used from there, and because SCPs cannot reach anything in the
# management account: nothing inside the member account can weaken them.

locals {
  # Every threshold emails the alert address. Only the 100% actual-spend
  # notification also publishes to the SNS topic, because the kill switch
  # subscribed to that topic terminates every instance on any message it gets.
  project_cap_notifications = [
    { type = "ACTUAL", threshold = 25, to_topic = false },
    { type = "ACTUAL", threshold = 50, to_topic = false },
    { type = "ACTUAL", threshold = 80, to_topic = false },
    { type = "ACTUAL", threshold = 100, to_topic = true },
    { type = "FORECASTED", threshold = 100, to_topic = false },
  ]
}

# The whole-project cap, scoped to the member account's share of the
# consolidated bill.
#
# Period semantics relied on: a budget resets its actual and forecasted spend
# once per time_unit, and ANNUALLY means every 12 months. AWS does not say
# whether the anchor is the start date given here or the calendar year, so the
# earliest possible reset is the following 1 January, after the project ends.
# Until then spend accumulates against one $200 figure. CUSTOM (a single
# non-resetting period) was considered and rejected: AWS deletes such budgets,
# with their notifications and actions, at the end date, and it is not
# documented as supporting budget actions.
resource "aws_budgets_budget" "project_cap" {
  name              = "fleetkit-project-cap"
  budget_type       = "COST"
  limit_amount      = var.project_cap_amount
  limit_unit        = "USD"
  time_unit         = "ANNUALLY"
  time_period_start = var.project_cap_start

  cost_filter {
    name   = "LinkedAccount"
    values = [aws_organizations_account.fleetkit.id]
  }

  dynamic "notification" {
    for_each = local.project_cap_notifications
    content {
      comparison_operator        = "GREATER_THAN"
      notification_type          = notification.value.type
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      subscriber_email_addresses = [var.alert_email]
      subscriber_sns_topic_arns  = notification.value.to_topic ? [aws_sns_topic.budget_alerts.arn] : null
    }
  }

  # Budgets checks at creation that it may publish to a subscribed topic, so
  # the topic policy has to exist first; the topic ARN alone only orders this
  # after the topic itself.
  depends_on = [aws_sns_topic_policy.budget_alerts]
}

# The monthly cap that fleetkit-project-cap replaces. A budget's period cannot
# change in place, so it is retired in two steps, both through the reviewed
# workflow: this declaration and the import block in imports.tf adopt it as
# it exists (the first apply shows no changes to it); the follow-up change
# deletes both, and that apply destroys the budget. Until then both budgets
# email the same thresholds, this one on the whole consolidated bill.
resource "aws_budgets_budget" "monthly_cap" {
  name              = var.monthly_cap_budget_name
  budget_type       = "COST"
  limit_amount      = var.project_cap_amount
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = var.project_cap_start

  dynamic "notification" {
    for_each = local.project_cap_notifications
    content {
      comparison_operator        = "GREATER_THAN"
      notification_type          = notification.value.type
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      subscriber_email_addresses = [var.alert_email]
    }
  }
}

# The daily burn limit that predates this stack, adopted through the import
# block in imports.tf. It is not scoped to the member account: it watches the
# whole consolidated bill, which is the point of a burn-rate alarm. The values
# below match the budget as it exists so the import produces no changes.
resource "aws_budgets_budget" "daily_burn" {
  name         = var.daily_burn_budget_name
  budget_type  = "COST"
  limit_amount = "25"
  limit_unit   = "USD"
  time_unit    = "DAILY"

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.alert_email]
  }
}

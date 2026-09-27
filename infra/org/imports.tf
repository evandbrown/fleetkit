# One-time adoption of the daily burn budget, which was created before this
# stack existed. The id format is AccountID:BudgetName; the account id is read
# at plan time so it never appears in the repository. Import blocks are
# idempotent, so this can stay, or be removed once the first apply has run.
import {
  to = aws_budgets_budget.daily_burn
  id = "${local.management_account}:${var.daily_burn_budget_name}"
}

# Adoption of the monthly cap that the project cap replaces, so its removal
# goes through the workflow like every other change. Delete this block with
# the resource once the import has been applied.
import {
  to = aws_budgets_budget.monthly_cap
  id = "${local.management_account}:${var.monthly_cap_budget_name}"
}

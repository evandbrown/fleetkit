# Read by project/ through terraform_remote_state.
#
# The account id, and the ARNs that embed it, are marked sensitive so an apply
# in the public workflow log does not print them under `Outputs:`. They are not
# secret, and plan diffs still show the id where a resource carries it; this
# just keeps the summary out of the log. terraform_remote_state reads
# sensitive outputs like any other.

output "account_id" {
  description = "Id of the fleetkit member account."
  value       = aws_organizations_account.fleetkit.id
  sensitive   = true
}

output "ou_id" {
  description = "Id of the fleetkit OU that the guardrail SCPs attach to."
  value       = aws_organizations_organizational_unit.fleetkit.id
}

output "budget_alerts_topic_arn" {
  description = "SNS topic the kill switch subscribes to; it receives the 100% cap breach and the budget-action notice."
  value       = aws_sns_topic.budget_alerts.arn
}

output "member_plan_role_arn" {
  description = "Role in the member account for pull-request plans of project/."
  value       = aws_iam_role.member_plan.arn
  sensitive   = true
}

output "member_apply_role_arn" {
  description = "Role in the member account for applies of project/."
  value       = aws_iam_role.member_apply.arn
  sensitive   = true
}

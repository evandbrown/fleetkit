output "state_bucket" {
  description = "S3 bucket holding Terraform state for every stack. Goes in backend.hcl and the TF_STATE_BUCKET repository variable."
  value       = aws_s3_bucket.state.bucket
}

output "plan_role_arn" {
  description = "Role GitHub Actions assumes on pull requests. Goes in the AWS_PLAN_ROLE_ARN repository variable."
  value       = aws_iam_role.plan.arn
}

output "apply_role_arn" {
  description = "Role GitHub Actions assumes from the approval-gated environment. Goes in the AWS_APPLY_ROLE_ARN repository variable."
  value       = aws_iam_role.apply.arn
}

output "oidc_provider_arn" {
  description = "IAM OIDC identity provider for GitHub Actions."
  value       = aws_iam_openid_connect_provider.github.arn
}

output "organization_root_id" {
  description = "Root (r-...) of the organization; the org stack creates the fleetkit OU under it."
  value       = aws_organizations_organization.this.roots[0].id
}

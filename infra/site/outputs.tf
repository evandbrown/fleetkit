# All three go into secrets of the `site` GitHub environment (see README.md). They are
# sensitive because the ARN and the bucket name carry the account id; `terraform output -raw`
# still prints them.
output "role_arn" {
  description = "The deploy role. Goes in the SITE_DEPLOY_ROLE_ARN secret of the site environment."
  value       = aws_iam_role.site_deploy.arn
  sensitive   = true
}

output "site_bucket" {
  description = "The evan.mx bucket, from terraform.tfvars. Goes in the SITE_BUCKET secret of the site environment."
  value       = var.site_bucket
  sensitive   = true
}

output "distribution_id" {
  description = "The evan.mx distribution, from terraform.tfvars. Goes in the SITE_DISTRIBUTION_ID secret of the site environment."
  value       = var.distribution_id
  sensitive   = true
}

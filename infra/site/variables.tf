variable "region" {
  description = "The single AWS region this project uses."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "CLI profile with management-account credentials."
  type        = string
  default     = "default"
}

variable "project_tag" {
  description = "Value of the Project tag on every resource."
  type        = string
  default     = "fleetkit"
}

variable "site_bucket" {
  description = "The evan.mx site bucket (the distribution's S3 origin). Its name carries the account id, so it lives in the gitignored terraform.tfvars. `sensitive` hides only the variable itself: plan still prints the name in the bucket data source and the rendered policy, so this stack is planned by hand, never in public CI (README.md)."
  type        = string
  sensitive   = true
}

variable "distribution_id" {
  description = "ID of the CloudFront distribution that serves evan.mx. Lives in the gitignored terraform.tfvars. As with site_bucket, plan still prints it (in the distribution ARN)."
  type        = string
  sensitive   = true
}

variable "github_repository" {
  description = "GitHub repository (owner/name) whose site environment may deploy the site."
  type        = string
  default     = "evandbrown/fleetkit"
}

variable "github_owner_id" {
  description = <<-EOT
    Numeric GitHub ID of the repository owner. This repository presents GitHub's
    immutable OIDC subject (repo:OWNER@OWNER_ID/REPO@REPO_ID:...), so a trust policy
    written with names alone never matches; see the bootstrap stack. Verify with
    `gh api repos/OWNER/REPO/actions/oidc/customization/sub`.
  EOT
  type        = number
  default     = 656941
}

variable "github_repository_id" {
  description = "Numeric GitHub ID of the repository. See github_owner_id."
  type        = number
  default     = 1389914258
}

variable "github_environment" {
  description = "GitHub Actions environment the deploy job is bound to. Only jobs bound to it can assume the role; its deployment branch policy must allow main only (README.md)."
  type        = string
  default     = "site"
}

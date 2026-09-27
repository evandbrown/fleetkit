variable "organization_id" {
  description = "ID of the existing AWS Organization (o-...) that this stack imports and switches to all features. Supplied at apply time, never committed."
  type        = string

  validation {
    condition     = can(regex("^o-[a-z0-9]{10,32}$", var.organization_id))
    error_message = "Must be an AWS Organizations organization ID of the form o- followed by 10 to 32 lowercase letters or digits."
  }
}

variable "enable_service_control_policies" {
  description = <<-EOT
    Enable the SERVICE_CONTROL_POLICY policy type on the organization root.
    Set to false for the FIRST apply of an organization that is still on
    consolidated billing: the provider enables policy types before it enables
    all features, and AWS rejects SCPs until all features are on. Apply once
    with `-var=enable_service_control_policies=false`, confirm
    `aws organizations describe-organization` reports FeatureSet ALL, then
    apply again with the default.
  EOT
  type        = bool
  default     = true
}

variable "github_repository" {
  description = "GitHub repository (owner/name) whose Actions workflows may assume the CI roles."
  type        = string
  default     = "evandbrown/fleetkit"
}

variable "github_owner_id" {
  description = <<-EOT
    Numeric GitHub ID of the repository owner. Repositories created after
    2026-07-15 present an immutable OIDC subject that embeds the owner and
    repository IDs (repo:OWNER@OWNER_ID/REPO@REPO_ID:...), so a trust policy
    written with names alone never matches. Verify both IDs with
    `gh api repos/OWNER/REPO --jq '[.owner.id, .id]'`.
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
  description = "GitHub Actions environment that gates applies. Only jobs bound to this environment can assume the apply role."
  type        = string
  default     = "aws"
}

variable "region" {
  description = "The single AWS region this project uses."
  type        = string
  default     = "us-east-1"
}

variable "project_tag" {
  description = "Value of the Project tag applied to every resource."
  type        = string
  default     = "fleetkit"
}

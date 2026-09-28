variable "region" {
  description = "The only region the guardrails allow."
  type        = string
  default     = "us-east-1"
}

variable "project_tag" {
  description = "Value of the Project tag applied to every resource."
  type        = string
  default     = "fleetkit"
}

variable "aws_profile" {
  description = "CLI profile with management-account credentials. The backend, the project stack's remote state and the provider all start from it; the provider then assumes member_role_arn."
  type        = string
  default     = "default"
}

variable "member_role_arn" {
  description = "ARN of the member account's access role (OrganizationAccountAccessRole) that the provider assumes. Carries the account id, so it lives in the gitignored terraform.tfvars."
  type        = string
  sensitive   = true
}

variable "state_bucket" {
  description = "Terraform state bucket created by the bootstrap stack; the project stack's outputs (VPC, subnet) are read from it."
  type        = string
}

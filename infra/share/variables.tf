variable "region" {
  description = "The only region the guardrails allow."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "CLI profile with management-account credentials; the provider assumes member_role_arn from it."
  type        = string
  default     = "default"
}

variable "member_role_arn" {
  description = "ARN of the member account's access role. Carries the account id, so it lives in the gitignored terraform.tfvars."
  type        = string
  sensitive   = true
}

variable "project_tag" {
  description = "Value of the Project tag on every resource."
  type        = string
  default     = "fleetkit"
}

variable "expire_after_days" {
  description = "Days after upload when S3 deletes a shared object. S3 runs expiry within about a day of the date."
  type        = number
  default     = 14
}

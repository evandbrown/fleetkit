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

variable "state_bucket" {
  description = "Terraform state bucket created by the bootstrap stack; the org stack's outputs are read from it."
  type        = string
}

variable "plan_role_name" {
  description = "Name of the management-account role that plans pull requests. When Terraform runs as this role it assumes the member plan role instead of the apply role."
  type        = string
  default     = "fleetkit-gha-plan"
}

variable "vpc_cidr" {
  description = "CIDR block of the VPC. The single public subnet takes its first /24."
  type        = string
  default     = "10.42.0.0/16"
}

variable "budget_name" {
  description = "Name of the whole-project cap budget. The kill switch ignores alerts about other budgets."
  type        = string
  default     = "fleetkit-project-cap"
}

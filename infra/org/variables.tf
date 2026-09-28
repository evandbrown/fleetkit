variable "region" {
  description = "The only region the project may use. Everything here is deployed to it and the region-lock SCP denies the rest."
  type        = string
  default     = "us-east-1"
}

variable "project_tag" {
  description = "Value of the Project tag applied to every resource through provider default_tags."
  type        = string
  default     = "fleetkit"
}

variable "account_name" {
  description = "Name of the member account that holds all project resources."
  type        = string
  default     = "fleetkit"
}

variable "account_email" {
  description = "Root email of the member account. It must not belong to any other AWS account and cannot be changed later without recreating the account."
  type        = string
  sensitive   = true
}

variable "alert_email" {
  description = "Address that receives every budget notification and the budget-action notice."
  type        = string
  sensitive   = true
}

variable "daily_burn_budget_name" {
  description = "Name of the pre-existing daily budget that the stack imports rather than recreates."
  type        = string
  default     = "fleetkit-daily-burn"
}

variable "monthly_cap_budget_name" {
  description = "Name of the pre-existing monthly budget that the project cap replaces. Adopted by import so the reviewed workflow retires it; remove with the resource once it is gone."
  type        = string
  default     = "fleetkit-monthly-cap"
}

variable "project_cap_amount" {
  description = "Whole-project spending cap in USD. Reaching 100% of it attaches the breaker SCP and fires the kill switch."
  type        = string
  default     = "200"
}

variable "project_cap_start" {
  description = "Start of the whole-project budget period, UTC, in the Budgets API format YYYY-MM-DD_HH:MM."
  type        = string
  default     = "2026-09-01_00:00"

  validation {
    condition     = can(regex("^\\d{4}-\\d{2}-\\d{2}_\\d{2}:\\d{2}$", var.project_cap_start))
    error_message = "Use the Budgets time format, for example 2026-09-01_00:00."
  }
}

variable "allowed_instance_families" {
  description = "EC2 instance families the member account may launch. Combined with allowed_instance_sizes into an exact-match allowlist."
  type        = list(string)
  default     = ["m8i", "c8i", "m7i", "c7i"]
}

variable "allowed_instance_sizes" {
  description = "Instance sizes allowed for every family in allowed_instance_families. Capped at 4xlarge to bound the burn rate."
  type        = list(string)
  default     = ["large", "xlarge", "2xlarge", "4xlarge"]
}

variable "allowed_extra_instance_types" {
  description = "Exact instance types allowed on top of allowed_instance_families x allowed_instance_sizes: the one metal type, for campaigns whose worker host is a whole machine. Each is named on its own so no other metal or 8xlarge-and-up type gets in with it."
  type        = list(string)
  default     = ["m8i.metal-48xl"]
}

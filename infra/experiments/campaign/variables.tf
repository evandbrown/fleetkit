variable "campaign" {
  description = "The campaign's name (its directory under results/). Tags every resource and names the support security group."
  type        = string

  validation {
    condition     = can(regex("^[a-z](-?[a-z0-9])+$", var.campaign)) && length(var.campaign) <= 40
    error_message = "A campaign name is 2 to 40 lowercase letters, digits and single hyphens, starting with a letter."
  }
}

variable "runs" {
  description = "The runs whose hosts exist now, keyed by run name (<spec>-r<k>): each gets its own worker host and support host. The launcher adds a run when it starts and removes it as soon as it ends."
  type = map(object({
    worker_instance_type  = string
    support_instance_type = string
  }))
  default = {}

  validation {
    # The account's instance-type guardrail (infra/org, allowed_instance_families x allowed_instance_sizes,
    # plus allowed_extra_instance_types); this list changes with it.
    condition = alltrue([for r in values(var.runs) : alltrue([
      for t in [r.worker_instance_type, r.support_instance_type] :
      can(regex("^((m8i|c8i|m7i|c7i)\\.(large|xlarge|2xlarge|4xlarge)|m8i\\.metal-48xl)$", t))
    ])])
    error_message = "The instance-type guardrail only allows m8i, c8i, m7i and c7i at large, xlarge, 2xlarge or 4xlarge, and m8i.metal-48xl."
  }

  validation {
    condition     = alltrue([for k in keys(var.runs) : can(regex("^[a-z0-9](-?[a-z0-9])*-r[0-9]+$", k))])
    error_message = "Run names are <spec>-r<replica>."
  }
}

variable "shutdown_after_minutes" {
  description = "Minutes after boot at which every host of the campaign shuts itself down, which terminates it, whatever else happens."
  type        = number

  validation {
    condition     = var.shutdown_after_minutes >= 10 && var.shutdown_after_minutes <= 480 && floor(var.shutdown_after_minutes) == var.shutdown_after_minutes
    error_message = "shutdown_after_minutes must be a whole number from 10 to 480."
  }
}

variable "repo_ref" {
  description = "Git commit of this repository that every host checks out. The launcher passes HEAD, which must be pushed."
  type        = string
}

variable "root_volume_gib" {
  description = "Root volume of every host, GiB (gp3, encrypted)."
  type        = number
  default     = 60
}

# The same values as the parent stack, read from its terraform.tfvars (-var-file=../terraform.tfvars).
variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project_tag" {
  type    = string
  default = "fleetkit"
}

variable "aws_profile" {
  type    = string
  default = "default"
}

variable "member_role_arn" {
  type      = string
  sensitive = true
}

variable "state_bucket" {
  type = string
}

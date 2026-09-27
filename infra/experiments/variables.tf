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

variable "host_count" {
  description = "Number of experiment hosts. 0 tears the hosts down and leaves the rest of the stack (results bucket, role, security group) in place."
  type        = number
  default     = 1

  validation {
    condition     = var.host_count >= 0 && floor(var.host_count) == var.host_count
    error_message = "host_count must be a whole number of hosts, 0 or more."
  }
}

variable "instance_type" {
  description = "Instance type of the hosts. Must support nested virtualization and pass the instance-type guardrail: m8i, c8i, m7i or c7i, large through 4xlarge."
  type        = string
  default     = "m8i.xlarge"

  validation {
    condition     = can(regex("^(m8i|c8i|m7i|c7i)\\.(large|xlarge|2xlarge|4xlarge)$", var.instance_type))
    error_message = "The instance-type guardrail only allows m8i, c8i, m7i and c7i at large, xlarge, 2xlarge or 4xlarge."
  }
}

variable "root_volume_gib" {
  description = "Size of each host's root volume in GiB (gp3, encrypted). Firecracker artefacts, guest images and rootfs files live on it."
  type        = number
  default     = 60
}

variable "shutdown_after_minutes" {
  description = "Minutes after boot at which a host shuts itself down, which terminates it. Armed by cloud-init before anything else: the hard stop on cost."
  type        = number
  default     = 240

  validation {
    condition     = var.shutdown_after_minutes >= 1 && floor(var.shutdown_after_minutes) == var.shutdown_after_minutes
    error_message = "shutdown_after_minutes must be a whole number of minutes, 1 or more."
  }
}

variable "repo_ref" {
  description = "Git ref of this repository (branch, tag or commit) that each host checks out under /opt/fleetkit."
  type        = string
  default     = "main"
}

# Bootstrap runs locally, once, with management-account credentials from
# `aws login` (the default CLI profile). No assume_role: this is the only stack
# that acts as the management account directly.
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project = var.project_tag
      Stack   = "bootstrap"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
}

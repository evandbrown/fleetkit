# Applied from a workstation with management-account credentials (`aws login`, the
# default profile). No assume_role: the evan.mx bucket and distribution live in the
# management account, so the deploy role has to live there too.
provider "aws" {
  region  = var.region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project = var.project_tag
      Stack   = "site"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
}

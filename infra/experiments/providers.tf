# This stack is applied from a workstation. The S3 backend and the project stack's
# remote state (data.tf) use the management-account profile named in backend.hcl.
# Every resource is created in the member account: the provider starts from the
# same management-account profile and assumes the member account's access role
# itself. (A CLI profile with source_profile does not work here: the provider's
# SDK cannot chain from the credentials that `aws login` produces, while the CLI can.)
provider "aws" {
  region  = var.region
  profile = var.aws_profile

  assume_role {
    role_arn     = var.member_role_arn
    session_name = "fleetkit-experiments"
  }

  default_tags {
    tags = {
      Project = var.project_tag
    }
  }
}

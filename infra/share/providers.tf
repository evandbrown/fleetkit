# Applied from a workstation, like the experiments stack: start from the
# management-account profile and assume the member account's access role.
provider "aws" {
  region  = var.region
  profile = var.aws_profile

  assume_role {
    role_arn     = var.member_role_arn
    session_name = "fleetkit-share"
  }

  default_tags {
    tags = {
      Project = var.project_tag
    }
  }
}

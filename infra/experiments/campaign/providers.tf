# Same provider setup as the parent stack (../providers.tf): the management-account profile,
# assuming the member account's access role for every resource.
provider "aws" {
  region  = var.region
  profile = var.aws_profile

  assume_role {
    role_arn     = var.member_role_arn
    session_name = "fleetkit-campaign"
  }

  default_tags {
    tags = {
      Project  = var.project_tag
      Campaign = var.campaign
    }
  }
}

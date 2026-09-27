# This stack is applied from a workstation with two CLI profiles at once. The S3
# backend and the project stack's remote state (data.tf) use the management-account
# profile named in backend.hcl; every resource is created through the profile
# below, which assumes a role into the member account.
provider "aws" {
  region  = var.region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project = var.project_tag
    }
  }
}

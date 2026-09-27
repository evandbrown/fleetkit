# Credentials come from the environment: in CI, the management-account role that
# GitHub Actions assumed through OIDC; locally, the default profile. The S3 backend
# and the org remote state use those credentials directly.
#
# Everything in this stack lives in the member account, so the default provider
# chains into one of the member roles the org stack created there. Which one
# depends on who is running (see locals.tf). Role chaining caps the session at
# one hour, which is plenty for this stack.
provider "aws" {
  region = var.region

  assume_role {
    role_arn     = local.member_role_arn
    session_name = "fleetkit-project"
  }

  default_tags {
    tags = {
      Project = var.project_tag
    }
  }
}

# The management-account credentials themselves. Used only to find out which
# principal is running Terraform; nothing is created with this provider.
provider "aws" {
  alias  = "management"
  region = var.region

  default_tags {
    tags = {
      Project = var.project_tag
    }
  }
}

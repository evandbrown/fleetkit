# Management account. In CI these are the OIDC credentials of the GitHub
# Actions plan or apply role; locally, the default profile.
provider "aws" {
  region = var.region

  default_tags {
    tags = { Project = var.project_tag }
  }
}

# The member account. Which role is assumed depends on who is running (see
# locals.tf): the CI plan role may only assume the read-only
# fleetkit-terraform-plan, which is enough to refresh the roles this stack
# creates there; everything else uses the OrganizationAccountAccessRole that
# Organizations creates in every account it makes.
#
# The account id comes from time_sleep.member_account_ready rather than from
# the account resource directly, so the provider is not configured (and the
# role not assumed) until the new account has had time to propagate the role.
#
# On the very first plan the id is unknown; with a single assume_role block the
# provider only warns and falls back to the management credentials for that
# plan, then assumes the role during apply once the id is known. Nothing under
# this alias reads data at plan time, so the fallback is harmless.
provider "aws" {
  alias  = "member"
  region = var.region

  assume_role {
    role_arn     = "arn:${local.partition}:iam::${time_sleep.member_account_ready.triggers["account_id"]}:role/${local.member_role_name}"
    session_name = "fleetkit-org"
  }

  default_tags {
    tags = { Project = var.project_tag }
  }
}

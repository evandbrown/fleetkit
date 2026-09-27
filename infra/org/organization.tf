data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

# The organization itself is managed by bootstrap/. It is read here for the
# root id, which OUs need as their parent.
data "aws_organizations_organization" "this" {}

# One OU for the project so guardrails attach once and any account added to it
# later inherits them.
resource "aws_organizations_organizational_unit" "fleetkit" {
  name      = "fleetkit"
  parent_id = local.root_id
}

# The member account that holds every project resource. Organizations creates
# it in the root, then Terraform moves it into the OU. Its root user has no
# credentials until a password reset is requested for the email address.
resource "aws_organizations_account" "fleetkit" {
  name      = var.account_name
  email     = var.account_email
  parent_id = aws_organizations_organizational_unit.fleetkit.id
  role_name = "OrganizationAccountAccessRole"

  # Destroying the resource would only remove the account from the
  # organization (which also drops it out of the SCPs); it would not close it.
  # Both are deliberate, manual operations, never a side effect of a plan.
  close_on_deletion = false

  lifecycle {
    prevent_destroy = true

    # role_name cannot be read back from the API and changing it forces a new
    # account; ignoring it stops a config edit from ever triggering that.
    ignore_changes = [role_name]
  }
}

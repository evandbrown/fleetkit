# The organization already exists (consolidated billing only, one account).
# It is imported rather than created, and the import id comes from a variable
# so the organization id never appears in the repo.
import {
  to = aws_organizations_organization.this
  id = var.organization_id
}

resource "aws_organizations_organization" "this" {
  # Consolidated billing -> ALL is an in-place update (EnableAllFeatures) and
  # is one-way. With no invited member accounts nobody has to approve the
  # handshake, but if describe-organization still reports CONSOLIDATED_BILLING
  # after the apply, accept the pending ENABLE_ALL_FEATURES handshake
  # (`aws organizations list-handshakes-for-organization`) or Terraform will
  # show a perpetual diff.
  feature_set = "ALL"

  # SCPs are the guardrail mechanism for the fleetkit OU (org/ stack). They can
  # only be enabled after all features are on; see the variable description.
  enabled_policy_types = var.enable_service_control_policies ? ["SERVICE_CONTROL_POLICY"] : []

  lifecycle {
    # Destroying this resource calls DeleteOrganization. Never by accident.
    prevent_destroy = true
  }
}

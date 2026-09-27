locals {
  partition          = data.aws_partition.current.partition
  management_account = data.aws_caller_identity.current.account_id
  root_id            = data.aws_organizations_organization.this.roots[0].id

  # Names shared with the other stacks. bootstrap/ creates the management
  # account CI roles; project/ assumes the member roles and creates the kill
  # switch under this function name.
  ci_plan_role_name      = "fleetkit-gha-plan"
  ci_plan_role_arn       = "arn:${local.partition}:iam::${local.management_account}:role/${local.ci_plan_role_name}"
  ci_apply_role_arn      = "arn:${local.partition}:iam::${local.management_account}:role/fleetkit-gha-apply"
  member_plan_role_name  = "fleetkit-terraform-plan"
  member_apply_role_name = "fleetkit-terraform-apply"
  kill_switch_name       = "fleetkit-kill-switch"

  # Which role the member provider assumes. bootstrap/ lets the CI plan role
  # assume only fleetkit-terraform-plan, so a pull-request plan (and the plan
  # job on main) refreshes the member roles read-only through that role. The
  # CI apply role and a workstation session use OrganizationAccountAccessRole,
  # which is what creates the member roles in the first place. Caller ARNs of
  # assumed roles look like ...:assumed-role/<role name>/<session name>.
  runner_is_plan_role = strcontains(data.aws_caller_identity.current.arn, ":assumed-role/${local.ci_plan_role_name}/")
  member_role_name    = local.runner_is_plan_role ? local.member_plan_role_name : "OrganizationAccountAccessRole"

  # SCPs apply to every principal in a member account, including the roles
  # Terraform itself uses there. These two may change guarded resources.
  guardrail_exempt_principal_arns = [
    "arn:${local.partition}:iam::*:role/OrganizationAccountAccessRole",
    "arn:${local.partition}:iam::*:role/${local.member_apply_role_name}",
  ]

  # ec2:InstanceType is compared with StringNotEquals, which is exact-match:
  # a wildcard such as m8i.* would let 8xlarge and larger through.
  allowed_instance_types = [
    for pair in setproduct(var.allowed_instance_families, var.allowed_instance_sizes) :
    "${pair[0]}.${pair[1]}"
  ]
}

# Guardrails for the fleetkit OU. All four use the deny-list strategy, so the
# default FullAWSAccess policy that Organizations attaches to every root, OU
# and account must stay in place; it is not managed here.
#
# SCPs never affect the management account, so the budgets, the SNS topic and
# the CI roles there are guarded by the scope of the CI roles' IAM policies
# rather than by these policies.

# 1. Only var.region, with the AWS-documented exemptions for global services
#    whose single endpoint lives elsewhere (IAM, STS, Organizations, Route 53,
#    CloudFront, KMS, Budgets and so on).
resource "aws_organizations_policy" "region_lock" {
  name        = "fleetkit-region-lock"
  description = "Deny every request outside the project region except global services."
  type        = "SERVICE_CONTROL_POLICY"
  content = templatefile("${path.module}/policies/region-lock.json.tftpl", {
    allowed_regions = [var.region]
  })
}

resource "aws_organizations_policy_attachment" "region_lock" {
  policy_id = aws_organizations_policy.region_lock.id
  target_id = aws_organizations_organizational_unit.fleetkit.id
}

# 2. Instance-type allowlist. Bounds how fast money can burn regardless of how
#    late billing data arrives, which matters because budgets lag by hours.
#    Auto Scaling groups, EC2 Fleet and Spot requests launch through
#    service-linked roles, which SCPs never bind, so the same policy denies
#    creating them: they would bypass the allowlist, and an Auto Scaling group
#    would relaunch every instance the kill switch terminates.
resource "aws_organizations_policy" "instance_types" {
  name        = "fleetkit-instance-types"
  description = "Deny ec2:RunInstances outside the instance-type allowlist, and the launch paths that bypass it."
  type        = "SERVICE_CONTROL_POLICY"
  content = templatefile("${path.module}/policies/instance-types.json.tftpl", {
    partition              = local.partition
    allowed_instance_types = local.allowed_instance_types
  })
}

resource "aws_organizations_policy_attachment" "instance_types" {
  policy_id = aws_organizations_policy.instance_types.id
  target_id = aws_organizations_organizational_unit.fleetkit.id
}

# 3. Protect the guardrails: the account cannot leave the organization, and
#    nothing except the Terraform apply principals can alter the kill switch
#    (function, execution role) or drop its subscription to the alerts topic.
resource "aws_organizations_policy" "protect_guardrails" {
  name        = "fleetkit-protect-guardrails"
  description = "Deny leaving the organization and modifying the kill switch."
  type        = "SERVICE_CONTROL_POLICY"
  content = templatefile("${path.module}/policies/protect-guardrails.json.tftpl", {
    partition             = local.partition
    region                = var.region
    kill_switch_name      = local.kill_switch_name
    exempt_principal_arns = local.guardrail_exempt_principal_arns
  })
}

resource "aws_organizations_policy_attachment" "protect_guardrails" {
  policy_id = aws_organizations_policy.protect_guardrails.id
  target_id = aws_organizations_organizational_unit.fleetkit.id
}

# 4. The breaker. Created but deliberately NOT attached: the budget action in
#    budget_action.tf attaches it to the OU when the project cap is reached and
#    detaches it at the start of the next budget period. Managing the
#    attachment here would make every apply fight the action.
resource "aws_organizations_policy" "budget_breaker" {
  name        = "fleetkit-budget-breaker"
  description = "Attached by the project-cap budget action: deny launching or starting instances."
  type        = "SERVICE_CONTROL_POLICY"
  content     = file("${path.module}/policies/budget-breaker.json")
}

# Outputs of the org stack: the member account, the roles CI assumes into it and
# the SNS topic that budget notifications are published to. Read with the same
# management-account credentials as the backend, so no cross-account bucket
# policy is needed.
data "terraform_remote_state" "org" {
  backend = "s3"

  config = {
    bucket = var.state_bucket
    key    = "org/terraform.tfstate"
    region = var.region
  }
}

data "aws_caller_identity" "runner" {
  provider = aws.management
}

locals {
  # Pull requests are planned by the read-only management role, which can only
  # assume the member plan role. Pushes to main and local runs carry a role that
  # may assume the member apply role. Caller ARNs of assumed roles look like
  # ...:assumed-role/<role name>/<session name>, so the role name is enough.
  runner_is_plan_role = strcontains(data.aws_caller_identity.runner.arn, ":assumed-role/${var.plan_role_name}/")

  member_role_arn = (
    local.runner_is_plan_role
    ? data.terraform_remote_state.org.outputs.member_plan_role_arn
    : data.terraform_remote_state.org.outputs.member_apply_role_arn
  )

  budget_alerts_topic_arn = data.terraform_remote_state.org.outputs.budget_alerts_topic_arn
}

data "aws_partition" "current" {}

data "aws_caller_identity" "member" {}

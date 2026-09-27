# Everything shared comes from the stacks that own it, looked up rather than copied: the VPC and
# subnet from the project stack's state, and from the parent stack (infra/experiments) the worker
# hosts' security group, their instance profile and the results bucket. Nothing here changes them.

data "aws_caller_identity" "member" {}

data "terraform_remote_state" "project" {
  backend = "s3"

  config = {
    bucket  = var.state_bucket
    key     = "project/terraform.tfstate"
    region  = var.region
    profile = var.aws_profile
  }
}

data "aws_security_group" "host" {
  name   = "fleetkit-exp-host"
  vpc_id = data.terraform_remote_state.project.outputs.vpc_id
}

data "aws_iam_instance_profile" "host" {
  name = "fleetkit-experiment-host"
}

locals {
  lock           = { for m in regexall("(?m)^([A-Z][A-Z0-9_]*)=([^\\r\\n]*)$", file("${path.module}/../../../images/lock.env")) : m[0] => m[1] }
  ami_id         = jsondecode(file("${path.module}/../ami.lock.json")).ami_id
  results_bucket = "fleetkit-results-${data.aws_caller_identity.member.account_id}"
  subnet_id      = data.terraform_remote_state.project.outputs.public_subnet_id

  # Every lock value is offered to both templates, so a pin a template starts using needs no change here.
  template_vars = merge(local.lock, {
    shutdown_after_minutes = var.shutdown_after_minutes
    results_bucket         = local.results_bucket
    repo_ref               = var.repo_ref
  })
}

data "aws_caller_identity" "member" {}

# Outputs of the project stack: the VPC and the public subnet the hosts launch
# into. Read with the management-account profile that also holds this stack's
# state, the same way the project stack reads the org stack, so the member-account
# profile never needs access to the state bucket.
data "terraform_remote_state" "project" {
  backend = "s3"

  config = {
    bucket  = var.state_bucket
    key     = "project/terraform.tfstate"
    region  = var.region
    profile = "default"
  }
}

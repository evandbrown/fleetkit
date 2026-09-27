terraform {
  # Same series as every other stack, so the committed .terraform.lock.hcl and a
  # workstation's Terraform agree. >= 1.10 for the S3 backend's native lockfile.
  required_version = "~> 1.16"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }

  # Partial configuration: bucket, key (experiments/terraform.tfstate), region,
  # encrypt, use_lockfile and the management-account profile are passed at init
  # time from backend.hcl, so no account-specific value lives in the repository.
  backend "s3" {}
}

terraform {
  # >= 1.10 for the S3 backend's native lockfile (use_lockfile); 1.16 is what CI and
  # local runs use, and the committed .terraform.lock.hcl pins the providers.
  required_version = "~> 1.16"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }

  # Partial configuration: bucket, key (project/terraform.tfstate), region, encrypt and
  # use_lockfile are passed at init time (backend.hcl locally, -backend-config in CI),
  # so no account-specific value lives in the repository.
  backend "s3" {}
}

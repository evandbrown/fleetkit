terraform {
  required_version = "~> 1.16"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }

  # Partial configuration, as in the other stacks: backend.hcl (gitignored) names the
  # state bucket, key site/terraform.tfstate, region and the management-account profile.
  backend "s3" {}
}

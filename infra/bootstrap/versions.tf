terraform {
  # 1.10 added native S3 state locking (use_lockfile); 1.11 made it GA. Every
  # stack in this repo pins the same series so a committed .terraform.lock.hcl
  # and the CI runner agree.
  required_version = "~> 1.16"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }
}

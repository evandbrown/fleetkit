terraform {
  # >= 1.12 for `import { identity }` and the GA S3 native lockfile; pinned to
  # the 1.16 series so local and CI runs use the same feature set.
  required_version = "~> 1.16"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.14"
    }
  }

  # Partial configuration: bucket, key, region, encrypt and use_lockfile are
  # passed with -backend-config so no account-specific value lives in the repo.
  backend "s3" {}
}

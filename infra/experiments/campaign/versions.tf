terraform {
  required_version = "~> 1.16"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }

  # Partial configuration, one state per campaign: experiments/launcher/launch.py passes the
  # parent stack's backend.hcl values with key = experiments/campaigns/<campaign>.tfstate.
  backend "s3" {}
}

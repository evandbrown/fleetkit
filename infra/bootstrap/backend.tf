# State lives locally for the first apply, because the bucket it will move to
# is created by this same stack. Once `terraform apply` has created the bucket:
#
#   1. Uncomment the block below.
#   2. Fill in backend.hcl from backend.hcl.example (bucket name from the
#      `state_bucket` output, key `bootstrap/terraform.tfstate`).
#   3. Run `terraform init -backend-config=backend.hcl -migrate-state` and
#      answer "yes" to copy the local state into the bucket.
#   4. Delete the local terraform.tfstate and its backup once the copy is
#      verified with `terraform state list`.
#
# The block stays partial on purpose: the bucket name carries the account id,
# which never goes in the repo.
#
terraform {
  backend "s3" {}
}

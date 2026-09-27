# Evidence from experiment runs: probe output, console logs, evidence bundles.
# Private, versioned, encrypted, TLS only. The hosts write to it through their own
# role (iam.tf); everything else reads it with the member-account profile. The
# bucket outlives the hosts: host_count = 0 removes the instances and leaves it.

resource "aws_s3_bucket" "results" {
  bucket = "fleetkit-results-${data.aws_caller_identity.member.account_id}"
}

# Every upload keeps the previous version, so a rerun cannot silently overwrite
# the evidence of the run before it.
resource "aws_s3_bucket_versioning" "results" {
  bucket = aws_s3_bucket.results.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    apply_server_side_encryption_by_default {
      # SSE-S3: the evidence holds nothing secret, and a KMS key would be one
      # more grant on the hosts' role.
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "results" {
  bucket = aws_s3_bucket.results.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "results_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.results.arn,
      "${aws_s3_bucket.results.arn}/*",
    ]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "results" {
  bucket = aws_s3_bucket.results.id
  policy = data.aws_iam_policy_document.results_bucket.json

  # The public access block must exist before a bucket policy is written,
  # otherwise S3 can reject the policy as potentially public.
  depends_on = [aws_s3_bucket_public_access_block.results]
}

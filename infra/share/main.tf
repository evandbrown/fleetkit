# A bucket for handing files to a named person by link. Each upload goes under an
# unguessable prefix (s/<128-bit random hex>/); anyone with the exact URL can read
# that object until the lifecycle rule deletes it. Nothing can be listed.

# AWS appends a unique suffix to the prefix. The name needn't be secret: the
# unguessable part of every URL is the object's random prefix.
resource "aws_s3_bucket" "share" {
  bucket_prefix = "fleetkit-share-"
}

resource "aws_s3_bucket_ownership_controls" "share" {
  bucket = aws_s3_bucket.share.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# ACLs stay blocked; only the bucket policy below may grant public read.
resource "aws_s3_bucket_public_access_block" "share" {
  bucket                  = aws_s3_bucket.share.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_server_side_encryption_configuration" "share" {
  bucket = aws_s3_bucket.share.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "share" {
  bucket = aws_s3_bucket.share.id

  rule {
    id     = "expire-shared-objects"
    status = "Enabled"
    filter {}
    expiration {
      days = var.expire_after_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "share" {
  statement {
    sid       = "ReadObjectsByExactUrl"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.share.arn}/s/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }

  statement {
    sid       = "HttpsOnly"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.share.arn, "${aws_s3_bucket.share.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "share" {
  bucket     = aws_s3_bucket.share.id
  policy     = data.aws_iam_policy_document.share.json
  depends_on = [aws_s3_bucket_public_access_block.share]
}

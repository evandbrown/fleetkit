output "bucket" {
  description = "The share bucket's name."
  value       = aws_s3_bucket.share.bucket
}

output "url_base" {
  description = "Prefix of every shared URL; the share script appends s/<random>/<file>."
  value       = "https://${aws_s3_bucket.share.bucket_regional_domain_name}/"
}

output "expire_after_days" {
  description = "Days after upload when shared objects are deleted."
  value       = var.expire_after_days
}

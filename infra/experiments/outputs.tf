output "host_instance_ids" {
  description = "Instance ids of the worker hosts; empty when host_count is 0."
  value       = aws_instance.host[*].id
}

output "support_instance_ids" {
  description = "Instance ids of the support hosts (fixture and observability backend); empty when support_count is 0."
  value       = aws_instance.support[*].id
}

output "support_private_ips" {
  description = "Private IPs of the support hosts, which the worker hosts and their guests reach on 8081 (fixture) and 4317/4318 (OTLP)."
  value       = aws_instance.support[*].private_ip
}

output "results_bucket" {
  description = "Bucket that experiment evidence is uploaded to. Read it with `terraform output -raw results_bucket`."
  value       = aws_s3_bucket.results.bucket
  # Not secret, but the name carries the account id; keeps it out of apply summaries.
  sensitive = true
}

output "host_security_group_id" {
  description = "Egress-only security group of the worker hosts."
  value       = aws_security_group.host.id
}

output "host_instance_profile_name" {
  description = "Instance profile (SSM plus the results bucket) of the worker hosts."
  value       = aws_iam_instance_profile.host.name
}

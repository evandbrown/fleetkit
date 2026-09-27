output "account_id" {
  description = "The member account this stack manages."
  value       = data.aws_caller_identity.member.account_id
  # Not secret, but kept out of the public workflow log's `Outputs:` block.
  sensitive = true
}

output "vpc_id" {
  description = "VPC for experiment hosts."
  value       = aws_vpc.this.id
}

output "public_subnet_id" {
  description = "The public subnet experiment hosts launch into."
  value       = aws_subnet.public.id
}

output "host_security_group_id" {
  description = "Egress-only security group for experiment hosts."
  value       = aws_security_group.host.id
}

output "host_instance_profile_name" {
  description = "Instance profile (SSM access) for experiment hosts."
  value       = aws_iam_instance_profile.host.name
}

output "kill_switch_function_arn" {
  description = "The kill-switch Lambda function."
  value       = aws_lambda_function.kill_switch.arn
}

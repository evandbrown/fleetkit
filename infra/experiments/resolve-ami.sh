#!/usr/bin/env bash
# Resolves the experiment hosts' AMI from the public SSM parameter and records it
# in ami.lock.json next to this script, so Terraform launches a fixed image and a
# plan never picks up a newer one on its own. Read-only: one ssm:GetParameter and
# one ec2:DescribeImages, with the member-account profile unless AWS_PROFILE is
# set. Rerun to bump the pin and review the diff before committing it.
#
#   infra/experiments/resolve-ami.sh
set -euo pipefail

region=${AWS_REGION:-us-east-1}
parameter=${AMI_SSM_PARAMETER:-/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.18-x86_64}
export AWS_PROFILE=${AWS_PROFILE:-fleetkit}
out=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ami.lock.json

ami_id=$(aws ssm get-parameter --region "$region" --name "$parameter" \
  --query Parameter.Value --output text)

aws ec2 describe-images --region "$region" --image-ids "$ami_id" \
  --query "{region: '$region', ssm_parameter: '$parameter', ami_id: Images[0].ImageId, name: Images[0].Name, created: Images[0].CreationDate}" \
  --output json > "$out.tmp"
mv "$out.tmp" "$out"
cat "$out"

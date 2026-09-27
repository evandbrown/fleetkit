# The instance profile every experiment host launches with. SSM Session Manager
# is the only access path to the hosts, so the profile carries exactly the
# permissions the SSM agent needs and nothing else.

data "aws_iam_policy_document" "host_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "host" {
  name               = "fleetkit-host"
  description        = "Experiment hosts: SSM Session Manager access only"
  assume_role_policy = data.aws_iam_policy_document.host_trust.json
}

# Looked up by name so the managed policy's ARN is resolved at apply time.
data "aws_iam_policy" "ssm_core" {
  name = "AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "host_ssm" {
  role       = aws_iam_role.host.name
  policy_arn = data.aws_iam_policy.ssm_core.arn
}

resource "aws_iam_instance_profile" "host" {
  name = "fleetkit-host"
  role = aws_iam_role.host.name
}

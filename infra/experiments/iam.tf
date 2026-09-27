# The hosts' own role and instance profile: SSM, which is how Run Command reaches
# a host, plus read and write on the results bucket, and nothing else. Separate
# from the project stack's fleetkit-host profile, which stays SSM-only and is
# managed by CI; this stack never touches it.

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
  name               = "fleetkit-experiment-host"
  description        = "Experiment hosts: SSM Run Command plus read and write on the results bucket"
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

data "aws_iam_policy_document" "host_results" {
  statement {
    sid       = "ListResultsBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.results.arn]
  }

  statement {
    sid = "ReadWriteResults"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.results.arn}/*"]
  }
}

resource "aws_iam_role_policy" "host_results" {
  name   = "results-bucket"
  role   = aws_iam_role.host.id
  policy = data.aws_iam_policy_document.host_results.json
}

resource "aws_iam_instance_profile" "host" {
  name = "fleetkit-experiment-host"
  role = aws_iam_role.host.name
}

# The kill switch: a Lambda function subscribed to the org stack's budget-alert
# topic. When the whole-project cap is breached it terminates every EC2 instance
# in this account. The budget action attached to the same cap blocks new
# launches with an SCP; this function deals with what is already running.
#
# The topic is in the management account, so this is a cross-account
# subscription. It is created here, from the endpoint owner's side, which is the
# only way Terraform keeps it in state; the topic policy in the org stack lets
# this account subscribe, and the Lambda permission below lets SNS invoke.

data "archive_file" "kill_switch" {
  type        = "zip"
  source_file = "${path.module}/src/kill_switch.py"
  # Under .terraform/ so the build artefact is never committed.
  output_path = "${path.module}/.terraform/kill_switch.zip"
}

resource "aws_cloudwatch_log_group" "kill_switch" {
  name              = "/aws/lambda/fleetkit-kill-switch"
  retention_in_days = 30
}

data "aws_iam_policy_document" "kill_switch_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "kill_switch" {
  # DescribeInstances has no resource-level permissions.
  statement {
    sid       = "ListInstances"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }

  # Terminate anything in this account and region. ModifyInstanceAttribute is
  # needed to clear stop and termination protection, which would otherwise make
  # TerminateInstances fail.
  statement {
    sid = "TerminateInstances"
    actions = [
      "ec2:TerminateInstances",
      "ec2:ModifyInstanceAttribute",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:ec2:${var.region}:${data.aws_caller_identity.member.account_id}:instance/*"]
  }

  statement {
    sid = "WriteLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.kill_switch.arn}:*"]
  }
}

resource "aws_iam_role" "kill_switch" {
  name               = "fleetkit-kill-switch"
  description        = "Kill switch: terminate every instance when the project cap is breached"
  assume_role_policy = data.aws_iam_policy_document.kill_switch_trust.json
}

resource "aws_iam_role_policy" "kill_switch" {
  name   = "kill-switch"
  role   = aws_iam_role.kill_switch.id
  policy = data.aws_iam_policy_document.kill_switch.json
}

resource "aws_lambda_function" "kill_switch" {
  function_name    = "fleetkit-kill-switch"
  description      = "Terminates every EC2 instance in the account when the project cap is breached"
  role             = aws_iam_role.kill_switch.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "kill_switch.handler"
  filename         = data.archive_file.kill_switch.output_path
  source_code_hash = data.archive_file.kill_switch.output_base64sha256
  # One TerminateInstances call per instance, with retries for protected ones.
  timeout = 300

  environment {
    variables = {
      BUDGET_NAME = var.budget_name
    }
  }

  # The role's permissions and the log group must exist before the first run.
  depends_on = [
    aws_iam_role_policy.kill_switch,
    aws_cloudwatch_log_group.kill_switch,
  ]
}

resource "aws_lambda_permission" "budget_alerts" {
  statement_id  = "AllowInvokeFromBudgetAlerts"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.kill_switch.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = local.budget_alerts_topic_arn
}

resource "aws_sns_topic_subscription" "kill_switch" {
  topic_arn = local.budget_alerts_topic_arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.kill_switch.arn

  depends_on = [aws_lambda_permission.budget_alerts]
}

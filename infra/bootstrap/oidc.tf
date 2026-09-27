# GitHub Actions authenticates to AWS with short-lived OIDC tokens; no access
# keys are stored anywhere. One provider per URL per account, so this is the
# only place it is created.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # No thumbprint_list: AWS validates GitHub's JWKS endpoint against its own
  # library of trusted root CAs and ignores configured thumbprints.
}

locals {
  # Immutable subject format; see the github_owner_id variable.
  github_subject_prefix = "repo:${split("/", var.github_repository)[0]}@${var.github_owner_id}/${split("/", var.github_repository)[1]}@${var.github_repository_id}"
}

# Both roles share the same shape of trust and differ only in which workflow
# contexts (the `sub` claim) may assume them.
data "aws_iam_policy_document" "github_trust" {
  for_each = {
    # pull_request and branch subjects are only presented by jobs that do NOT
    # reference an environment, so the plan job must stay environment-free.
    plan = [
      "${local.github_subject_prefix}:pull_request",
      "${local.github_subject_prefix}:ref:refs/heads/main",
    ]
    # A job bound to the environment presents this subject regardless of the
    # triggering event, and only after the environment's reviewers approve.
    apply = ["${local.github_subject_prefix}:environment:${var.github_environment}"]
  }

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = each.value
    }
  }
}

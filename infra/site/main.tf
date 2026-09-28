# The role GitHub Actions assumes to publish site/dist to evan.mx/fleetkit/ (see
# .github/workflows/site.yml and site/scripts/deploy.sh). In the evan.mx bucket it can write
# only what a build produces (fleetkit/index.html, fleetkit/assets/, fleetkit/data/) and delete
# only under fleetkit/data/; it can invalidate the evan.mx distribution. Nothing else: the
# other apps' objects (/ventrac/, /novak/, /fajitas/) and anything else under fleetkit/ are
# out of its reach in S3. README.md lists what that does not cover.
#
# The name starts with fleetkit-gha-, so the bootstrap apply role's NeverTouchCiRoles deny
# covers it: CI can never widen this role's trust or permissions.

locals {
  # The site's prefix. site/scripts/deploy.sh uses the same one.
  prefix = "fleetkit"

  # Only a job bound to the site environment presents this subject, whatever the event. The
  # environment's deployment branch policy (main only, README.md) is what ties it to main.
  # Jobs on main that are not bound to it present ...:ref:refs/heads/main instead, so they
  # cannot assume this role, and the deploy job cannot assume the bootstrap plan role.
  github_subject = "repo:${split("/", var.github_repository)[0]}@${var.github_owner_id}/${split("/", var.github_repository)[1]}@${var.github_repository_id}:environment:${var.github_environment}"

  bucket_arn       = "arn:${local.partition}:s3:::${var.site_bucket}"
  distribution_arn = "arn:${local.partition}:cloudfront::${local.account_id}:distribution/${var.distribution_id}"
}

# Read-only checks that the gitignored values name real things: plan fails on a typo.
data "aws_s3_bucket" "site" {
  bucket = var.site_bucket
}

data "aws_cloudfront_distribution" "site" {
  id = var.distribution_id

  lifecycle {
    postcondition {
      condition     = contains(self.aliases, "evan.mx")
      error_message = "distribution_id does not name the distribution that serves evan.mx."
    }
  }
}

# Created by the bootstrap stack; one per URL per account, so it is only looked up here.
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.github_subject]
    }
  }
}

resource "aws_iam_role" "site_deploy" {
  name                 = "fleetkit-gha-site-deploy"
  description          = "GitHub Actions: deploy the fleetkit site to evan.mx/fleetkit/ (site environment only)."
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "site_deploy" {
  # The deploy lists fleetkit/data/ to find data the new build no longer has. Without a
  # prefix, or with any other, a listing is denied.
  statement {
    sid       = "ListSiteData"
    actions   = ["s3:ListBucket"]
    resources = [local.bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.prefix}/data/", "${local.prefix}/data/*"]
    }
  }

  # Exactly what a build produces. Other files under fleetkit/ (added by hand, in a folder of
  # their own) cannot be overwritten or deleted by a deploy.
  statement {
    sid     = "PublishSite"
    actions = ["s3:PutObject"]
    resources = [
      "${local.bucket_arn}/${local.prefix}/index.html",
      "${local.bucket_arn}/${local.prefix}/assets/*",
      "${local.bucket_arn}/${local.prefix}/data/*",
    ]
  }

  # Hashed assets are never deleted (an open tab may still load an old chunk); stale data is.
  statement {
    sid       = "PruneSiteData"
    actions   = ["s3:DeleteObject"]
    resources = ["${local.bucket_arn}/${local.prefix}/data/*"]
  }

  # CloudFront has no condition key for invalidation paths, so this allows any path on the
  # distribution; deploy.sh invalidates /fleetkit and /fleetkit/* only.
  statement {
    sid       = "InvalidateSite"
    actions   = ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]
    resources = [local.distribution_arn]
  }
}

resource "aws_iam_role_policy" "site_deploy" {
  name   = "fleetkit-gha-site-deploy"
  role   = aws_iam_role.site_deploy.id
  policy = data.aws_iam_policy_document.site_deploy.json
}

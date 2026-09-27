# The experiment hosts: nested-virtualization instances provisioned by cloud-init
# from images/host/cloud-config.yaml. Every value the template needs comes from
# this stack's variables and from images/lock.env; the results bucket name, which
# carries the account id, only ever exists in the rendered user data, which is
# never committed.

locals {
  # images/lock.env is a shell-sourceable KEY=VALUE file shared with the image
  # builds and the host scripts; this reads it without a shell. Comment and
  # blank lines do not match the pattern and are dropped.
  lock = {
    for m in regexall("(?m)^([A-Z][A-Z0-9_]*)=([^\\r\\n]*)$", file("${path.module}/../../images/lock.env")) : m[0] => m[1]
  }
}

resource "aws_instance" "host" {
  count = var.host_count

  ami                         = jsondecode(file("${path.module}/ami.lock.json")).ami_id
  instance_type               = var.instance_type
  subnet_id                   = data.terraform_remote_state.project.outputs.public_subnet_id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.host.id]
  iam_instance_profile        = aws_iam_instance_profile.host.name

  # Firecracker needs /dev/kvm on the host; without this the guests would have
  # no hardware virtualization to run on.
  cpu_options {
    nested_virtualization = "enabled"
  }

  root_block_device {
    volume_size = var.root_volume_gib
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  # The self-shutdown timer that cloud-init arms ends in a shutdown. With this, a
  # shutdown is a termination: a forgotten host stops costing money instead of
  # sitting stopped with its volume still billed.
  instance_initiated_shutdown_behavior = "terminate"

  user_data = templatefile("${path.module}/../../images/host/cloud-config.yaml", {
    shutdown_after_minutes    = var.shutdown_after_minutes
    results_bucket            = aws_s3_bucket.results.bucket
    repo_ref                  = var.repo_ref
    FIRECRACKER_VERSION       = local.lock["FIRECRACKER_VERSION"]
    FIRECRACKER_X86_64_SHA256 = local.lock["FIRECRACKER_X86_64_SHA256"]
    KERNEL_BASE_URL           = local.lock["KERNEL_BASE_URL"]
    KERNEL_CI_PREFIX          = local.lock["KERNEL_CI_PREFIX"]
    KERNEL_VERSION            = local.lock["KERNEL_VERSION"]
    KERNEL_X86_64_SHA256      = local.lock["KERNEL_X86_64_SHA256"]
  })
  # A change to the template or to a pin replaces the host rather than leaving
  # it running with stale user data.
  user_data_replace_on_change = true

  tags = {
    Name = "fleetkit-exp-host"
  }
}

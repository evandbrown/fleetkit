# One campaign's hosts: for every run, its own worker host and its own support host, created
# when the run starts and destroyed as soon as it ends (experiments/launcher/launch.py), plus a
# support security group the campaign's runs share. Every resource carries Project and
# Campaign tags, and every instance a Run tag, so a sweep by tag finds anything left behind.
# Each host shuts itself down shutdown_after_minutes after boot, which terminates it.

resource "aws_security_group" "support" {
  name        = "fleetkit-${var.campaign}-support"
  description = "Support hosts of campaign ${var.campaign}: fixture, OTLP and health from the worker hosts only"
  vpc_id      = data.terraform_remote_state.project.outputs.vpc_id

  tags = {
    Name = "fleetkit-${var.campaign}-support"
  }
}

resource "aws_vpc_security_group_egress_rule" "support_all" {
  security_group_id = aws_security_group.support.id
  description       = "All outbound traffic"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# The worker hosts (and their guests, NATed by the worker host) reach the support host's
# fixture, collector and health service, and nothing else does.
resource "aws_vpc_security_group_ingress_rule" "support_from_workers" {
  for_each = {
    fixture   = 8081
    otlp-grpc = 4317
    otlp-http = 4318
    health    = 8082
  }

  security_group_id            = aws_security_group.support.id
  description                  = "${each.key} from the worker hosts"
  referenced_security_group_id = data.aws_security_group.host.id
  ip_protocol                  = "tcp"
  from_port                    = each.value
  to_port                      = each.value
}

resource "aws_instance" "worker" {
  for_each = var.runs

  ami                         = local.ami_id
  instance_type               = each.value.worker_instance_type
  subnet_id                   = local.subnet_id
  associate_public_ip_address = true
  vpc_security_group_ids      = [data.aws_security_group.host.id]
  iam_instance_profile        = data.aws_iam_instance_profile.host.name

  # A nested worker host needs /dev/kvm; a metal one has it already.
  dynamic "cpu_options" {
    for_each = strcontains(each.value.worker_instance_type, ".metal") ? [] : [1]
    content {
      nested_virtualization = "enabled"
    }
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

  instance_initiated_shutdown_behavior = "terminate"
  user_data                            = templatefile("${path.module}/../../../images/host/cloud-config.yaml", local.template_vars)
  user_data_replace_on_change          = true

  tags = {
    Name = "fleetkit-${var.campaign}-${each.key}-worker"
    Run  = each.key
    Role = "worker"
  }
}

resource "aws_instance" "support" {
  for_each = var.runs

  ami                         = local.ami_id
  instance_type               = each.value.support_instance_type
  subnet_id                   = local.subnet_id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.support.id]
  iam_instance_profile        = data.aws_iam_instance_profile.host.name

  root_block_device {
    volume_size = var.root_volume_gib
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  instance_initiated_shutdown_behavior = "terminate"
  user_data                            = templatefile("${path.module}/../../../images/support/cloud-config.yaml", local.template_vars)
  user_data_replace_on_change          = true

  tags = {
    Name = "fleetkit-${var.campaign}-${each.key}-support"
    Run  = each.key
    Role = "support"
  }
}

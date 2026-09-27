# The support host: the fixture web server and the observability backend (LGTM)
# for the capacity experiment, off the experiment host so that neither competes
# with the microVMs for CPU. Provisioned by cloud-init from
# images/support/cloud-config.yaml, rendered like the experiment host's. It needs
# no nested virtualization; everything else (AMI lock, subnet, instance profile,
# root volume, IMDSv2, shutdown timer, terminate on shutdown) matches host.tf.

resource "aws_security_group" "support" {
  name        = "fleetkit-exp-support"
  description = "Experiment support host: fixture and OTLP from the experiment hosts only, access via SSM"
  vpc_id      = data.terraform_remote_state.project.outputs.vpc_id

  tags = {
    Name = "fleetkit-exp-support"
  }
}

resource "aws_vpc_security_group_egress_rule" "support_all" {
  security_group_id = aws_security_group.support.id
  description       = "All outbound traffic"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# Only the experiment hosts reach the support host. The guests' traffic arrives
# NATed (MASQUERADE) from the experiment host's own interface, so the reference to
# the host group covers the guests too.
resource "aws_vpc_security_group_ingress_rule" "support_from_host" {
  for_each = {
    fixture   = 8081
    otlp-grpc = 4317
    otlp-http = 4318
  }

  security_group_id            = aws_security_group.support.id
  description                  = "${each.key} from the experiment hosts"
  referenced_security_group_id = aws_security_group.host.id
  ip_protocol                  = "tcp"
  from_port                    = each.value
  to_port                      = each.value
}

resource "aws_instance" "support" {
  count = var.support_count

  ami                         = jsondecode(file("${path.module}/ami.lock.json")).ami_id
  instance_type               = var.support_instance_type
  subnet_id                   = data.terraform_remote_state.project.outputs.public_subnet_id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.support.id]
  iam_instance_profile        = aws_iam_instance_profile.host.name

  root_block_device {
    volume_size = var.root_volume_gib
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  # Same hard stop as the experiment hosts: the shutdown timer ends the instance.
  instance_initiated_shutdown_behavior = "terminate"

  user_data = templatefile("${path.module}/../../images/support/cloud-config.yaml", {
    shutdown_after_minutes = var.shutdown_after_minutes
    results_bucket         = aws_s3_bucket.results.bucket
    repo_ref               = var.repo_ref
    NGINX_IMAGE            = local.lock["NGINX_IMAGE"]
    LGTM_IMAGE             = local.lock["LGTM_IMAGE"]
  })
  user_data_replace_on_change = true

  tags = {
    Name = "fleetkit-exp-support"
  }
}

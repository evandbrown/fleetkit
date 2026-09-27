# Egress only, in the project stack's VPC. Access to the hosts goes through SSM,
# which the instance starts outbound, so no ingress rule exists and none should
# be added here. The stack has its own group rather than using the project's
# fleetkit-host so that a rule change for an experiment never needs a CI round trip.

resource "aws_security_group" "host" {
  name        = "fleetkit-exp-host"
  description = "Experiment hosts: outbound only, access via SSM"
  vpc_id      = data.terraform_remote_state.project.outputs.vpc_id

  tags = {
    Name = "fleetkit-exp-host"
  }
}

resource "aws_vpc_security_group_egress_rule" "host_all" {
  security_group_id = aws_security_group.host.id
  description       = "All outbound traffic"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# A minimal network for short-lived experiment hosts: one public subnet with a
# route to the internet. Hosts get a public address for egress (package installs,
# image pulls) and are reached through SSM Session Manager, so nothing listens
# for inbound traffic and no NAT gateway is paid for.

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "fleetkit"
  }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "fleetkit"
  }
}

# Local Zones and Wavelength Zones are opt-in; keep to the region's own zones.
data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 0)
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true

  tags = {
    Name = "fleetkit-public"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "fleetkit-public"
  }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# Egress only. Access to the hosts goes through SSM, which the instance starts
# outbound, so no ingress rule exists and none should be added here.
resource "aws_security_group" "host" {
  name        = "fleetkit-host"
  description = "Experiment hosts: outbound only, access via SSM Session Manager"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "fleetkit-host"
  }
}

resource "aws_vpc_security_group_egress_rule" "host_all" {
  security_group_id = aws_security_group.host.id
  description       = "All outbound traffic"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

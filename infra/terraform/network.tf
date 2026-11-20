# VPC with two public subnets (ECS tasks) and two private subnets (RDS) in two AZs.
#
# Why no NAT gateway / load balancer: cost. Tasks run in public subnets with a public IP so they can
# reach ECR, Cognito and the Gemini API, but their security groups accept inbound traffic ONLY from the
# API Gateway VPC link (billing-api, agent) or from the agent (mcp-server). Nothing is reachable from
# the internet except through the API Gateway. The database has no route to the internet at all.

resource "aws_vpc" "main" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true # required by Cloud Map private DNS
  tags                 = { Name = "${local.name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-igw" }
}

resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  availability_zone = local.azs[count.index]
  tags              = { Name = "${local.name}-public-${count.index}" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index + 10)
  availability_zone = local.azs[count.index]
  tags              = { Name = "${local.name}-private-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id # local routes only: no internet access
  tags   = { Name = "${local.name}-private" }
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ---------- security groups (the network-level "do not expose directly" rule) ----------

resource "aws_security_group" "vpclink" {
  name        = "${local.name}-vpclink"
  description = "API Gateway VPC link ENIs"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "billing" {
  name        = "${local.name}-billing-api"
  description = "Billing API tasks: inbound only from the API Gateway VPC link"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "agent" {
  name        = "${local.name}-agent"
  description = "Agent tasks: inbound only from the API Gateway VPC link"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "mcp" {
  name        = "${local.name}-mcp"
  description = "MCP server tasks: inbound only from the agent"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "rds" {
  name        = "${local.name}-rds"
  description = "PostgreSQL: inbound only from the Billing API"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_egress_rule" "vpclink_to_billing" {
  security_group_id            = aws_security_group.vpclink.id
  referenced_security_group_id = aws_security_group.billing.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_egress_rule" "vpclink_to_agent" {
  security_group_id            = aws_security_group.vpclink.id
  referenced_security_group_id = aws_security_group.agent.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_ingress_rule" "billing_from_vpclink" {
  security_group_id            = aws_security_group.billing.id
  referenced_security_group_id = aws_security_group.vpclink.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_ingress_rule" "agent_from_vpclink" {
  security_group_id            = aws_security_group.agent.id
  referenced_security_group_id = aws_security_group.vpclink.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_ingress_rule" "mcp_from_agent" {
  security_group_id            = aws_security_group.mcp.id
  referenced_security_group_id = aws_security_group.agent.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_billing" {
  security_group_id            = aws_security_group.rds.id
  referenced_security_group_id = aws_security_group.billing.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

# Outbound HTTPS/DNS for image pulls, Cognito JWKS, Secrets Manager, CloudWatch Logs and the Gemini API.
resource "aws_vpc_security_group_egress_rule" "tasks_outbound" {
  for_each          = { billing = aws_security_group.billing.id, agent = aws_security_group.agent.id, mcp = aws_security_group.mcp.id }
  security_group_id = each.value
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

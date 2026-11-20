# Amazon RDS for PostgreSQL in private subnets. The master password is generated and stored by RDS in
# AWS Secrets Manager (manage_master_user_password), so it never appears in code or in Terraform state.

resource "aws_db_subnet_group" "main" {
  name       = "${local.name}-db"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier     = "${local.name}-db"
  engine         = "postgres"
  engine_version = "16"
  instance_class = "db.t4g.micro"

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_name                     = "billing"
  username                    = "billing_admin"
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  multi_az               = false

  backup_retention_period    = 1
  auto_minor_version_upgrade = true
  apply_immediately          = true

  # Course project: allow `terraform destroy` without a final snapshot. Production: keep snapshots
  # and enable deletion_protection.
  skip_final_snapshot = true
  deletion_protection = false
}

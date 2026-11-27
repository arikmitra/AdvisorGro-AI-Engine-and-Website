variable "name"   { type = string }
variable "subnet_ids" { type = list(string) }
variable "vpc_security_group_ids" { type = list(string) }

resource "aws_kms_key" "data" {
  description             = "${var.name} data key"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_s3_bucket" "objects" {
  bucket              = "${var.name}-objects"
  object_lock_enabled = true # approvals and the activity-log mirror are immutable
}
resource "aws_s3_bucket_versioning" "objects" {
  bucket = aws_s3_bucket.objects.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "objects" {
  bucket = aws_s3_bucket.objects.id
  rule { apply_server_side_encryption_by_default { sse_algorithm = "aws:kms", kms_master_key_id = aws_kms_key.data.arn } }
}
resource "aws_s3_bucket_public_access_block" "objects" {
  bucket                  = aws_s3_bucket.objects.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_db_subnet_group" "pg" { name = "${var.name}-pg", subnet_ids = var.subnet_ids }
resource "aws_db_instance" "pg" {
  identifier                   = "${var.name}-pg"
  engine                       = "postgres"
  engine_version               = "16"
  instance_class               = "db.t4g.medium"
  allocated_storage            = 50
  storage_encrypted            = true
  kms_key_id                   = aws_kms_key.data.arn
  backup_retention_period      = 14 # point-in-time recovery; restore is drilled quarterly
  multi_az                     = true
  publicly_accessible          = false
  db_subnet_group_name         = aws_db_subnet_group.pg.name
  vpc_security_group_ids       = var.vpc_security_group_ids
  manage_master_user_password  = true # secret lives in the secrets manager, never in state or the repo
  deletion_protection          = true
  iam_database_authentication_enabled = true
}
output "bucket" { value = aws_s3_bucket.objects.bucket }
output "db_endpoint" { value = aws_db_instance.pg.endpoint }

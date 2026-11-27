terraform {
  required_version = ">= 1.8"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } }
}
provider "aws" { region = "us-east-1" } # confirm region and data-residency with counsel

variable "subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "image_api" { type = string }
variable "image_worker" { type = string }

module "data" {
  source = "../../modules/data"
  name = "advisorgro-staging"
  subnet_ids = var.subnet_ids
  vpc_security_group_ids = var.security_group_ids
}
module "runtime" {
  source = "../../modules/runtime"
  name = "advisorgro-staging"
  image_api = var.image_api
  image_worker = var.image_worker
  environment = { AG_ENV = "staging", AG_USE_FAKES = "false", AG_INLINE_WORKER = "false", AG_OBJECT_STORE_BACKEND = "s3", AG_S3_BUCKET = module.data.bucket, AG_JWT_MODE = "jwks" }
  secret_arns = {} # AG_DATABASE_URL, AG_KEK_B64, AG_HMAC_KEY_B64, provider keys: add ARNs from the secrets manager
}

variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "GCP region for all regional resources."
  type        = string
  default     = "us-central1"
}

variable "cluster_name" {
  description = "GKE cluster name."
  type        = string
  default     = "architeq"
}

variable "domain" {
  description = "Base domain for the platform (e.g. architeq.example.com). Records created: api.<domain>, app.<domain>, livekit.<domain>, sip.<domain>."
  type        = string
}

variable "db_tier" {
  description = "Cloud SQL machine tier. Smallest sensible HA-capable custom tier by default (2 vCPU / 7.5 GB)."
  type        = string
  default     = "db-custom-2-7680"
}

variable "db_availability_type" {
  description = "Cloud SQL availability type: ZONAL (cheap, dev) or REGIONAL (HA, prod)."
  type        = string
  default     = "REGIONAL"
}

variable "db_deletion_protection" {
  description = "Protect the Cloud SQL instance from `terraform destroy`."
  type        = bool
  default     = true
}

variable "default_pool_machine_type" {
  description = "Machine type for the default node pool (api, dashboard, monitoring)."
  type        = string
  default     = "e2-standard-4"
}

variable "voice_pool_machine_type" {
  description = "Machine type for the voice node pool (LiveKit server/SIP + workers; CPU-sensitive realtime audio)."
  type        = string
  default     = "c2-standard-8"
}

variable "dns_zone_create" {
  description = "Whether to create the Cloud DNS managed zone (and A records) for var.domain. Set false if DNS is managed elsewhere (e.g. Cloudflare)."
  type        = bool
  default     = true
}

variable "cloudflare_api_token" {
  description = "Cloudflare API token ('Edit zone DNS' template, scoped to the target zone). Set together with cloudflare_zone_id to have Terraform manage the A records in Cloudflare; leave empty otherwise."
  type        = string
  default     = ""
  sensitive   = true
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone id for var.domain. Empty = no Cloudflare DNS records are managed."
  type        = string
  default     = ""
}

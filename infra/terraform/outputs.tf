output "cluster_name" {
  value = google_container_cluster.cluster.name
}

output "cluster_get_credentials" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.cluster.name} --region ${var.region} --project ${var.project_id}"
}

output "db_instance_connection_name" {
  description = "Cloud SQL connection name (for Cloud SQL Auth Proxy, if used)."
  value       = google_sql_database_instance.postgres.connection_name
}

output "db_private_ip" {
  value = google_sql_database_instance.postgres.private_ip_address
}

output "db_password_secret" {
  description = "Secret Manager secret holding the architeq DB password."
  value       = google_secret_manager_secret.db_password.secret_id
}

output "database_url_secret" {
  description = "Secret Manager secret holding the full ARCHITEQ_DATABASE_URL DSN."
  value       = google_secret_manager_secret.database_url.secret_id
}

output "redis_host" {
  value = google_redis_instance.redis.host
}

output "redis_url" {
  value = "redis://${google_redis_instance.redis.host}:${google_redis_instance.redis.port}/0"
}

output "recordings_bucket" {
  value = google_storage_bucket.recordings.name
}

output "artifact_registry" {
  description = "Docker repo prefix for image pushes."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.architeq.repository_id}"
}

output "web_ip" {
  description = "Global static IP for the GCE ingress (api./app. records point here)."
  value       = google_compute_global_address.web.address
}

output "web_ip_name" {
  value = google_compute_global_address.web.name
}

output "livekit_ip" {
  description = "Regional static IP for the LiveKit signalling LoadBalancer."
  value       = google_compute_address.livekit.address
}

output "sip_ip" {
  description = "Regional static IP for the livekit-sip UDP LoadBalancer (point the Telnyx trunk here / sip.<domain>)."
  value       = google_compute_address.sip.address
}

output "api_service_account" {
  value = google_service_account.api.email
}

output "worker_service_account" {
  value = google_service_account.worker.email
}

output "livekit_egress_service_account" {
  value = google_service_account.livekit_egress.email
}

output "dns_name_servers" {
  value = var.dns_zone_create ? google_dns_managed_zone.zone[0].name_servers : []
}

# Memorystore Redis — shared by architeq-api (call state, rate limits)
# and LiveKit server (multi-node room routing).

resource "google_redis_instance" "redis" {
  name           = "${var.cluster_name}-redis"
  tier           = "BASIC"
  memory_size_gb = 1
  region         = var.region

  redis_version      = "REDIS_7_0"
  authorized_network = google_compute_network.vpc.id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"

  depends_on = [
    google_project_service.services,
    google_service_networking_connection.private_services,
  ]
}

resource "google_artifact_registry_repository" "architeq" {
  repository_id = "architeq"
  location      = var.region
  format        = "DOCKER"
  description   = "Architeq service images (api, worker, dashboard)"

  depends_on = [google_project_service.services]
}

resource "google_artifact_registry_repository" "arhiteq" {
  repository_id = "arhiteq"
  location      = var.region
  format        = "DOCKER"
  description   = "Arhiteq service images (api, worker, dashboard)"

  depends_on = [google_project_service.services]
}

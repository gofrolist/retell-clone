# infra/terraform/github-deploy.tf
# Keyless GitHub Actions → GCP auth for the release deploy workflow
# (.github/workflows/deploy.yml). A Workload Identity Pool trusts GitHub's
# OIDC issuer, locked to this repository; the deployer SA can push images
# and drive helm against the cluster, and nothing else.

locals {
  github_repository = "gofrolist/retell-clone"
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = "assertion.repository == \"${local.github_repository}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deployer" {
  account_id   = "architeq-deployer"
  display_name = "GitHub Actions release deployer"
}

resource "google_project_iam_member" "deployer_artifact_registry" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_project_iam_member" "deployer_gke" {
  project = var.project_id
  role    = "roles/container.developer"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${local.github_repository}"
}

output "deploy_workload_identity_provider" {
  description = "Set as the GCP_WIF_PROVIDER repo variable."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deploy_service_account" {
  description = "Set as the GCP_DEPLOYER_SA repo variable."
  value       = google_service_account.deployer.email
}

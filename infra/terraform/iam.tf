# Service accounts + Workload Identity bindings.
# Kubernetes ServiceAccounts (namespace `architeq`) are annotated by the
# Helm chart with these GSA emails.

locals {
  wi_namespace = "architeq"
}

# ---------------------------------------------------------------- api ----
resource "google_service_account" "api" {
  account_id   = "architeq-api"
  display_name = "Architeq API control plane"
}

resource "google_project_iam_member" "api_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.api.email}"
}

# Worker calls Gemini through Vertex AI with ADC (Workload Identity) — no
# LLM API key. aiplatform.user covers endpoints.predict on publisher models.
resource "google_project_iam_member" "worker_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_project_iam_member" "api_secrets" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_storage_bucket_iam_member" "api_recordings" {
  bucket = google_storage_bucket.recordings.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.api.email}"
}

# Needed to mint V4 signed URLs for recordings without a private key file.
resource "google_service_account_iam_member" "api_self_token_creator" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.api.email}"
}

resource "google_service_account_iam_member" "api_wi" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${local.wi_namespace}/architeq-api]"
}

# ------------------------------------------------------------- worker ----
resource "google_service_account" "worker" {
  account_id   = "architeq-worker"
  display_name = "Architeq voice worker"
}

resource "google_storage_bucket_iam_member" "worker_recordings" {
  bucket = google_storage_bucket.recordings.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_service_account_iam_member" "worker_wi" {
  service_account_id = google_service_account.worker.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${local.wi_namespace}/architeq-worker]"
}

# ------------------------------------------------------------ livekit ----
# LiveKit Egress uploads recordings straight to GCS.
resource "google_service_account" "livekit_egress" {
  account_id   = "architeq-livekit-egress"
  display_name = "LiveKit Egress recordings uploader"
}

resource "google_storage_bucket_iam_member" "egress_recordings" {
  bucket = google_storage_bucket.recordings.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.livekit_egress.email}"
}

resource "google_service_account_iam_member" "egress_wi" {
  service_account_id = google_service_account.livekit_egress.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[livekit/livekit-egress]"
}

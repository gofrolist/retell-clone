# Recordings bucket. LiveKit Egress writes call recordings here; the API
# serves them to the dashboard via signed URLs (hence the CORS rule).

resource "google_storage_bucket" "recordings" {
  name     = "${var.project_id}-architeq-recordings"
  location = var.region

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  cors {
    origin          = ["https://app.${var.domain}", "http://localhost:3000"]
    method          = ["GET", "HEAD"]
    response_header = ["Content-Type", "Content-Range", "Range"]
    max_age_seconds = 3600
  }

  versioning {
    enabled = false
  }
}

# Remote state. IMPORTANT: this GCS backend MUST be enabled before any
# shared or production use — local state is only acceptable for a single
# operator experimenting solo. Do not run a state migration casually.
#
# Remote state. Create the bucket once, out of band:
#   gcloud storage buckets create gs://<PROJECT_ID>-arhiteq-tfstate \
#     --location=us-central1 --uniform-bucket-level-access
# then uncomment and `terraform init -migrate-state`.
#
terraform {
  backend "gcs" {
    bucket = "usan-retirement-arhiteq-tfstate"
    prefix = "arhiteq"
  }
}

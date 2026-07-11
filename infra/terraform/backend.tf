# Remote state. Create the bucket once, out of band:
#   gcloud storage buckets create gs://<PROJECT_ID>-architeq-tfstate \
#     --location=us-central1 --uniform-bucket-level-access
# then uncomment and `terraform init -migrate-state`.
#
# terraform {
#   backend "gcs" {
#     bucket = "<PROJECT_ID>-architeq-tfstate"
#     prefix = "architeq"
#   }
# }

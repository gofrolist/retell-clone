terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.39"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 7.39"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

provider "cloudflare" {
  # Empty token is fine when no cloudflare_* resources are created
  # (cloudflare_zone_id unset).
  api_token = var.cloudflare_api_token
}

# Cloud SQL Postgres 16, private IP only. Password generated here and
# stored in Secret Manager; nothing sensitive lands in Kubernetes values.

resource "google_sql_database_instance" "postgres" {
  name             = "${var.cluster_name}-pg"
  database_version = "POSTGRES_16"
  region           = var.region

  deletion_protection = var.db_deletion_protection

  settings {
    tier              = var.db_tier
    availability_type = var.db_availability_type
    disk_type         = "PD_SSD"
    disk_size         = 20
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "03:00"
    }

    maintenance_window {
      day  = 7 # Sunday
      hour = 4
    }

    database_flags {
      name  = "max_connections"
      value = "200"
    }
  }

  depends_on = [google_service_networking_connection.private_services]
}

resource "google_sql_database" "architeq" {
  name     = "architeq"
  instance = google_sql_database_instance.postgres.name
}

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "google_sql_user" "architeq" {
  name     = "architeq"
  instance = google_sql_database_instance.postgres.name
  password = random_password.db.result
}

resource "google_secret_manager_secret" "db_password" {
  secret_id = "architeq-db-password"

  replication {
    auto {}
  }

  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db.result
}

# Full async DSN, ready to mount as ARCHITEQ_DATABASE_URL.
resource "google_secret_manager_secret" "database_url" {
  secret_id = "architeq-database-url"

  replication {
    auto {}
  }

  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = "postgresql+asyncpg://architeq:${random_password.db.result}@${google_sql_database_instance.postgres.private_ip_address}:5432/architeq"
}

# Regional GKE cluster with Workload Identity and two node pools:
#   default — e2-standard-4, general workloads (api, dashboard, monitoring)
#   voice   — c2-standard-8, tainted for LiveKit server/SIP + agent workers

resource "google_container_cluster" "cluster" {
  name     = var.cluster_name
  location = var.region

  network    = google_compute_network.vpc.id
  subnetwork = google_compute_subnetwork.subnet.id

  # We manage node pools explicitly.
  remove_default_node_pool = true
  initial_node_count       = 1

  networking_mode = "VPC_NATIVE"
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  release_channel {
    channel = "REGULAR"
  }

  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS"]
    managed_prometheus {
      enabled = false # we run kube-prometheus-stack ourselves
    }
  }

  logging_config {
    enable_components = ["SYSTEM_COMPONENTS", "WORKLOADS"]
  }

  # Nodes get only private IPs except the voice pool, which needs public
  # node IPs for direct WebRTC/SIP media; keep the control plane public
  # endpoint for simplicity (lock down with authorized networks if needed).
  deletion_protection = false

  depends_on = [google_project_service.services]
}

resource "google_container_node_pool" "default" {
  name     = "default"
  cluster  = google_container_cluster.cluster.id
  location = var.region

  autoscaling {
    min_node_count = 1
    max_node_count = 5
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type = var.default_pool_machine_type
    disk_size_gb = 100
    disk_type    = "pd-balanced"

    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    labels = {
      workload = "general"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
  }
}

resource "google_container_node_pool" "voice" {
  name     = "voice"
  cluster  = google_container_cluster.cluster.id
  location = var.region

  autoscaling {
    min_node_count = 1
    max_node_count = 10
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type = var.voice_pool_machine_type
    disk_size_gb = 100
    disk_type    = "pd-ssd"

    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    labels = {
      workload = "voice"
    }

    taint {
      key    = "dedicated"
      value  = "voice"
      effect = "NO_SCHEDULE"
    }

    # Matches the livekit media firewall rule.
    tags = ["voice-pool"]

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
  }
}

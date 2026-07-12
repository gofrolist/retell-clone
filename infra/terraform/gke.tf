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

  # Private nodes: default-pool nodes get only private IPs and reach the
  # internet via Cloud NAT. The voice pool overrides this (its network_config
  # sets enable_private_nodes = false) because LiveKit needs public node IPs
  # for direct WebRTC/SIP media. The control plane keeps a public endpoint
  # (enable_private_endpoint = false), locked down by master_authorized_networks.
  # NOTE: applying this to an existing cluster requires recreation — review before terraform apply
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = "172.16.0.0/28" # /28 for the control plane; may need adjusting to avoid VPC overlap
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.master_authorized_networks
      content {
        cidr_block   = cidr_blocks.value.cidr_block
        display_name = cidr_blocks.value.display_name
      }
    }
  }

  deletion_protection = false

  depends_on = [google_project_service.services]
}

# Dedicated least-privilege service account for GKE nodes, replacing the
# default Compute Engine service account. Access is granted via the IAM
# bindings below; nodes still use the cloud-platform oauth scope (Google's
# recommended pattern) with actual authorization controlled by these roles.
resource "google_service_account" "gke_nodes" {
  account_id   = "${var.cluster_name}-gke-nodes"
  display_name = "GKE node service account for ${var.cluster_name}"
}

resource "google_project_iam_member" "gke_nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/artifactregistry.reader",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.gke_nodes.email}"
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

    service_account = google_service_account.gke_nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

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

    service_account = google_service_account.gke_nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

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

  # Voice nodes need public IPs for direct WebRTC/SIP media, so they opt out
  # of the cluster-wide private-nodes setting. Only this pool keeps public IPs.
  network_config {
    enable_private_nodes = false
  }
}

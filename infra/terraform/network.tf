# VPC, subnet (with secondary ranges for GKE pods/services), Cloud NAT,
# and the private-services peering used by Cloud SQL / Memorystore.

resource "google_compute_network" "vpc" {
  name                    = "${var.cluster_name}-vpc"
  auto_create_subnetworks = false

  depends_on = [google_project_service.services]
}

resource "google_compute_subnetwork" "subnet" {
  name                     = "${var.cluster_name}-subnet"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = "10.10.0.0/20"
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.20.0.0/14"
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.24.0.0/20"
  }
}

resource "google_compute_router" "router" {
  name    = "${var.cluster_name}-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  name                               = "${var.cluster_name}-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# Private services access (Cloud SQL private IP).
resource "google_compute_global_address" "private_services" {
  name          = "${var.cluster_name}-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]

  depends_on = [google_project_service.services]
}

# LiveKit publishes WebRTC/SIP media over UDP directly on node IPs
# (hostNetwork). Allow the media port ranges to the voice pool nodes.
resource "google_compute_firewall" "livekit_media" {
  name    = "${var.cluster_name}-livekit-media"
  network = google_compute_network.vpc.id

  allow {
    protocol = "udp"
    ports    = ["50000-60000"] # LiveKit RTC media
  }

  allow {
    protocol = "tcp"
    ports    = ["7881"] # LiveKit ICE/TCP fallback
  }

  allow {
    protocol = "udp"
    ports    = ["10000-20000"] # livekit-sip media (RTP)
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["voice-pool"]
}

# SIP signalling (UDP 5060) split into its own rule so it can be locked down
# to the SIP trunk provider's source ranges without also restricting the
# world-open WebRTC/SIP media ports above.
resource "google_compute_firewall" "livekit_sip_signalling" {
  name    = "${var.cluster_name}-livekit-sip-signalling"
  network = google_compute_network.vpc.id

  allow {
    protocol = "udp"
    ports    = ["5060"] # livekit-sip signalling
  }

  source_ranges = var.sip_signalling_source_ranges
  target_tags   = ["voice-pool"]
}

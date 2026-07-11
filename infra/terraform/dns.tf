# Static IPs and DNS.
#   web_ip  — global, for the GCE HTTPS ingress (api.<domain>, app.<domain>)
#   sip_ip  — regional, for the livekit-sip UDP LoadBalancer Service
# livekit.<domain> points at the LiveKit signalling LoadBalancer (regional).

resource "google_compute_global_address" "web" {
  name = "${var.cluster_name}-web-ip"

  depends_on = [google_project_service.services]
}

resource "google_compute_address" "livekit" {
  name   = "${var.cluster_name}-livekit-ip"
  region = var.region

  depends_on = [google_project_service.services]
}

resource "google_compute_address" "sip" {
  name   = "${var.cluster_name}-sip-ip"
  region = var.region

  depends_on = [google_project_service.services]
}

resource "google_dns_managed_zone" "zone" {
  count = var.dns_zone_create ? 1 : 0

  name        = replace(var.domain, ".", "-")
  dns_name    = "${var.domain}."
  description = "Architeq platform zone"

  depends_on = [google_project_service.services]
}

locals {
  zone_name = var.dns_zone_create ? google_dns_managed_zone.zone[0].name : replace(var.domain, ".", "-")
}

resource "google_dns_record_set" "api" {
  managed_zone = local.zone_name
  name         = "api.${var.domain}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_global_address.web.address]
}

resource "google_dns_record_set" "app" {
  managed_zone = local.zone_name
  name         = "app.${var.domain}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_global_address.web.address]
}

resource "google_dns_record_set" "livekit" {
  managed_zone = local.zone_name
  name         = "livekit.${var.domain}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.livekit.address]
}

resource "google_dns_record_set" "sip" {
  managed_zone = local.zone_name
  name         = "sip.${var.domain}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.sip.address]
}

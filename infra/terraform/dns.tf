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

  # subdomain -> address, shared by both DNS backends.
  dns_records = {
    api     = google_compute_global_address.web.address
    app     = google_compute_global_address.web.address
    livekit = google_compute_address.livekit.address
    sip     = google_compute_address.sip.address
  }
}

resource "google_dns_record_set" "records" {
  for_each = var.dns_zone_create ? local.dns_records : {}

  managed_zone = local.zone_name
  name         = "${each.key}.${var.domain}."
  type         = "A"
  ttl          = 300
  rrdatas      = [each.value]
}

# Cloudflare-managed DNS (dns_zone_create=false, cloudflare_zone_id set).
# Always proxied=false: Cloudflare's proxy cannot pass WebRTC UDP, SIP, or
# GCP managed-certificate HTTP validation.
resource "cloudflare_dns_record" "records" {
  for_each = var.cloudflare_zone_id != "" ? local.dns_records : {}

  zone_id = var.cloudflare_zone_id
  name    = "${each.key}.${var.domain}"
  type    = "A"
  content = each.value
  ttl     = 300
  proxied = false
}

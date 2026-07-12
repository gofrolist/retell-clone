import os

# Tool-bridge tests POST to public example.com URLs via httpx.MockTransport
# (no network). Allow private/unresolvable hosts so the SSRF guard's DNS
# lookup doesn't run against the network — mirrors the backend test setup.
os.environ.setdefault("ARCHITEQ_ALLOW_PRIVATE_WEBHOOKS", "true")

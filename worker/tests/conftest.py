import os

# Tool-bridge tests POST to public example.com URLs via httpx.MockTransport
# (no network). Allow private/unresolvable hosts so the SSRF guard's DNS
# lookup doesn't run against the network — mirrors the backend test setup.
os.environ.setdefault("ARHITEQ_ALLOW_PRIVATE_WEBHOOKS", "true")

import json
from pathlib import Path

import pytest

# The sanitized real-Retell flow fixtures live in the backend tree and are the
# shared schema authority for both projects (see the design spec). Reading them
# from here is deliberate: if the two drift, these tests fail.
_FIXTURE_DIR = (
    Path(__file__).resolve().parents[2] / "backend" / "tests" / "fixtures" / "retell_flows"
)


def load_retell_flow_fixture(name: str) -> dict:
    path = _FIXTURE_DIR / name
    if not path.is_file():
        pytest.skip(f"Retell flow fixture not available: {path}")
    return json.loads(path.read_text())


@pytest.fixture
def prior_auth_flow() -> dict:
    """18-node flow: every supported node type, plus components and notes."""
    return load_retell_flow_fixture("prior_auth_hotline.json")

#!/usr/bin/env python3
"""Import the USAN Retirement agents from the consumer repo into Arhiteq.

Reads prompts and tool declarations directly from usan-retirement-backend
(prompts/*.txt, retell/{companion,inbound,sales}/*.json) and creates, via the
Arhiteq public API:
  - one retell-llm + agent per role (companion, sales, betty),
  - preserving the EXISTING Retell agent ids (so the consumer's
    RETELL_*_AGENT_ID env vars keep working unchanged).

Usage:
  python scripts/import_usan_agents.py \
      --api-base http://localhost:8080 \
      --api-key  <ARHITEQ_API_KEY> \
      --consumer-repo ~/gofrolist/usan-retirement-backend \
      --companion-agent-id agent_... --sales-agent-id agent_... \
      --betty-agent-id agent_... \
      [--webhook-url https://<project>.supabase.co/functions/v1/retell-call-ended]

Inbound calls route to Sales or Companion dynamically via the
inbound-call-router webhook; retell/inbound/*.json tools are merged into the
SALES agent (Retell setup used the Sales agent for unknown inbound callers).
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

ROLES = {
    "companion": {
        "prompt": "prompts/checkin_v0.2_retell.txt",
        "tool_dirs": ["retell/companion"],
        "name": "CL - Check-in v0.2 Companion",
        "voice_id": "cartesia-sonic",  # closest to Retell "Cimo"
    },
    "sales": {
        "prompt": "prompts/sales_clara_v0.1_retell.txt",
        "tool_dirs": ["retell/sales", "retell/inbound"],
        "name": "CL-Sales",
        "voice_id": "cartesia-sonic",
    },
    "betty": {
        "prompt": "prompts/betty_tester_retell.txt",
        "tool_dirs": [],
        "name": "Betty",
        "voice_id": "cartesia-sonic",
    },
}


def load_tools(repo: Path, dirs: list[str]) -> list[dict]:
    tools: dict[str, dict] = {}
    for d in dirs:
        for f in sorted((repo / d).glob("*.json")):
            spec = json.loads(f.read_text())
            url = spec.get("url", "")
            if not url or url.startswith("RETELL_BUILT_IN"):
                # kb_lookup → Arhiteq knowledge-base tool, configured separately
                continue
            name = spec["name"]
            if name in tools:
                continue  # first declaration wins on duplicates across dirs
            tools[name] = {
                "type": "custom",
                "name": name,
                "description": spec.get("description", ""),
                "url": url,
                "method": spec.get("method", "POST"),
                "parameters": spec.get("parameters", {}),
                "speak_during_execution": False,
                "speak_after_execution": True,
            }
    return list(tools.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-base", default="http://localhost:8080")
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--consumer-repo", default="~/gofrolist/usan-retirement-backend")
    ap.add_argument("--companion-agent-id")
    ap.add_argument("--sales-agent-id")
    ap.add_argument("--betty-agent-id")
    ap.add_argument(
        "--webhook-url", help="agent-level call-events webhook (retell-call-ended)"
    )
    ap.add_argument("--llm-model", default="gemini-2.5-flash")
    args = ap.parse_args()

    repo = Path(args.consumer_repo).expanduser()
    if not repo.exists():
        print(f"consumer repo not found: {repo}", file=sys.stderr)
        return 1

    client = httpx.Client(
        base_url=args.api_base,
        headers={"Authorization": f"Bearer {args.api_key}"},
        timeout=30,
    )
    agent_ids = {
        "companion": args.companion_agent_id,
        "sales": args.sales_agent_id,
        "betty": args.betty_agent_id,
    }

    for role, cfg in ROLES.items():
        prompt_path = repo / cfg["prompt"]
        if not prompt_path.exists():
            print(f"!! prompt missing for {role}: {prompt_path}", file=sys.stderr)
            continue
        prompt = prompt_path.read_text()
        tools = load_tools(repo, cfg["tool_dirs"])

        llm = client.post(
            "/create-retell-llm",
            json={
                "model": args.llm_model,
                "model_temperature": 0.0,
                "general_prompt": prompt,
                "general_tools": tools,
                "begin_message": "{{bm_greeting}}",
                "start_speaker": "agent",
            },
        )
        llm.raise_for_status()
        llm_id = llm.json()["llm_id"]

        agent_body = {
            "agent_id": agent_ids[role],  # preserve Retell id if provided
            "agent_name": cfg["name"],
            "response_engine": {"type": "retell-llm", "llm_id": llm_id},
            "voice_id": cfg["voice_id"],
            "voice_speed": 0.9 if role == "companion" else 1.0,
            "interruption_sensitivity": 0.92,
            "enable_voicemail_detection": True,
            "webhook_url": args.webhook_url,
        }
        agent = client.post(
            "/create-agent", json={k: v for k, v in agent_body.items() if v is not None}
        )
        agent.raise_for_status()
        print(
            f"{role}: agent_id={agent.json()['agent_id']} llm_id={llm_id} tools={len(tools)}"
        )

    print(
        "\nNext: bind phone numbers (import-phone-number) and set the inbound "
        "webhook URL to .../inbound-call-router — see docs/MIGRATION.md"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

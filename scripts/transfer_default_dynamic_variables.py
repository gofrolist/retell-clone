#!/usr/bin/env python3
"""Transfer `default_dynamic_variables` from live Retell agents into Arhiteq.

The "Default Dynamic Variables" you configure in the Retell dashboard
(Security & Fallback Settings) live on the *response engine* — the Retell
LLM's / conversation flow's `default_dynamic_variables` map, NOT on the agent.
Arhiteq models the field the same way and already merges it as a fallback
(defaults < per-call `retell_llm_dynamic_variables`), so transferring the map
is all that's needed for parity.

For each agent id this script:
  1. GET Retell /get-agent/{id}            -> response_engine (llm_id / flow id)
  2. GET Retell /get-retell-llm/{llm_id}   -> source default_dynamic_variables
     (or /get-conversation-flow/{id})
  3. GET Arhiteq /get-agent/{same id}      -> dest engine id (ids are preserved
                                              on import, so it's the same id)
  4. PATCH Arhiteq /update-retell-llm/{id} -> { "default_dynamic_variables": … }
     (or /update-conversation-flow/{id})

Empty-string defaults are dropped by default: an empty Retell default means
"no fallback" (leave `{{var}}` literal), and writing "" would instead substitute
an empty string — a behaviour change. Pass --keep-empty to copy them verbatim.

Also optionally writes the pulled maps to a JSON file (--write-json) keyed by
agent id, so `import_usan_agents.py --defaults-json <file>` can bake the same
defaults into fresh imports (reproducible; see docs/MIGRATION.md).

Usage:
  # preview one agent (no writes)
  python scripts/transfer_default_dynamic_variables.py \
      --retell-key   key_xxx \
      --arhiteq-base https://api.arhiteq.com \
      --arhiteq-key  <ARHITEQ_WORKSPACE_API_KEY> \
      --agent-id agent_175ad5d9bfe5ce919271b539ea \
      --dry-run

  # every agent, for real, and snapshot the maps for the importer
  python scripts/transfer_default_dynamic_variables.py \
      --retell-key key_xxx \
      --arhiteq-base https://api.arhiteq.com --arhiteq-key <KEY> \
      --all --write-json scripts/usan_default_dynamic_variables.json
"""

import argparse
import json
import sys

import httpx

RETELL_BASE = "https://api.retellai.com"


def pull_defaults(retell: httpx.Client, agent_id: str) -> tuple[str, str, dict]:
    """Return (engine_type, engine_id, default_dynamic_variables) from Retell."""
    agent = retell.get(f"/get-agent/{agent_id}")
    agent.raise_for_status()
    engine = agent.json().get("response_engine") or {}
    etype = engine.get("type", "retell-llm")
    if etype == "conversation-flow":
        eid = engine.get("conversation_flow_id")
        obj = retell.get(f"/get-conversation-flow/{eid}")
    else:
        eid = engine.get("llm_id")
        obj = retell.get(f"/get-retell-llm/{eid}")
    obj.raise_for_status()
    defaults = obj.json().get("default_dynamic_variables") or {}
    return etype, eid, defaults


def dest_engine_id(arhiteq: httpx.Client, agent_id: str) -> tuple[str, str]:
    """Return (engine_type, engine_id) for the same agent id in Arhiteq."""
    resp = arhiteq.get(f"/get-agent/{agent_id}")
    resp.raise_for_status()
    engine = resp.json().get("response_engine") or {}
    etype = engine.get("type", "retell-llm")
    eid = engine.get(
        "conversation_flow_id" if etype == "conversation-flow" else "llm_id"
    )
    return etype, eid


def clean(defaults: dict, keep_empty: bool) -> dict:
    out = {str(k): ("" if v is None else str(v)) for k, v in defaults.items()}
    if keep_empty:
        return out
    return {k: v for k, v in out.items() if v != ""}


def transfer_one(
    retell: httpx.Client, arhiteq: httpx.Client, agent_id: str, args, snapshot: dict
) -> None:
    """Pull from Retell (recording the map in `snapshot`), then PATCH the dest.

    The snapshot is a Retell-side artifact, so it's recorded right after a
    successful pull — a dest-side failure (e.g. the agent isn't imported yet)
    fails only the PATCH, never the snapshot.
    """
    etype, src_eid, raw = pull_defaults(retell, agent_id)
    defaults = clean(raw, args.keep_empty)
    dropped = sorted(set(raw) - set(defaults))
    print(f"\n{agent_id}  (engine={etype}:{src_eid})")
    if not raw:
        print("  no default_dynamic_variables set in Retell — nothing to transfer")
        return
    for k, v in defaults.items():
        print(f"  + {k} = {v!r}")
    if dropped:
        print(f"  (dropped empty: {', '.join(dropped)}; use --keep-empty to copy)")
    if defaults:
        snapshot[agent_id] = defaults  # captured regardless of the PATCH below

    if args.dry_run:
        print("  [dry-run] would PATCH the matching Arhiteq engine")
        return

    dest_type, dest_eid = dest_engine_id(arhiteq, agent_id)
    path = (
        f"/update-conversation-flow/{dest_eid}"
        if dest_type == "conversation-flow"
        else f"/update-retell-llm/{dest_eid}"
    )
    resp = arhiteq.patch(path, json={"default_dynamic_variables": defaults})
    resp.raise_for_status()
    print(f"  OK -> PATCH {path} ({len(defaults)} vars)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--retell-key", required=True, help="Retell API key (source)")
    ap.add_argument("--retell-base", default=RETELL_BASE)
    ap.add_argument("--arhiteq-base", default="https://api.arhiteq.com")
    ap.add_argument(
        "--arhiteq-key", help="Arhiteq workspace key (dest); omit with --dry-run"
    )
    ap.add_argument("--agent-id", action="append", default=[], help="repeatable")
    ap.add_argument("--all", action="store_true", help="every Retell agent")
    ap.add_argument("--keep-empty", action="store_true", help="copy empty defaults too")
    ap.add_argument(
        "--write-json", metavar="PATH", help="snapshot pulled maps for the importer"
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.dry_run and not args.arhiteq_key:
        print("--arhiteq-key is required unless --dry-run", file=sys.stderr)
        return 2

    retell = httpx.Client(
        base_url=args.retell_base,
        headers={"Authorization": f"Bearer {args.retell_key}"},
        timeout=30,
    )
    arhiteq = httpx.Client(
        base_url=args.arhiteq_base,
        headers={"Authorization": f"Bearer {args.arhiteq_key}"}
        if args.arhiteq_key
        else {},
        timeout=30,
    )

    agent_ids = list(args.agent_id)
    if args.all:
        resp = retell.get("/list-agents")
        resp.raise_for_status()
        agent_ids += [a["agent_id"] for a in resp.json()]
    agent_ids = list(dict.fromkeys(agent_ids))  # de-dup, keep order
    if not agent_ids:
        print("nothing to do: pass --agent-id or --all", file=sys.stderr)
        return 2

    snapshot: dict[str, dict] = {}
    failures = 0
    for aid in agent_ids:
        try:
            transfer_one(retell, arhiteq, aid, args, snapshot)
        except httpx.HTTPStatusError as e:
            failures += 1
            print(
                f"FAIL {aid}: {e.response.status_code} {e.response.text[:300]}",
                file=sys.stderr,
            )
        except Exception as e:  # noqa: BLE001 - keep going on the rest
            failures += 1
            print(f"FAIL {aid}: {e}", file=sys.stderr)

    if args.write_json and snapshot:
        with open(args.write_json, "w") as fh:
            json.dump(snapshot, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"\nwrote {len(snapshot)} agent map(s) -> {args.write_json}")

    if failures:
        print(f"\n{failures} agent(s) failed", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

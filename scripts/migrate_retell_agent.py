#!/usr/bin/env python3
"""Migrate agents + their custom functions from a live Retell account to Arhiteq.

Pulls straight from Retell's public API (no local JSON needed) and recreates each
agent in Arhiteq via the wire-compatible endpoints:

  Retell (source)                     Arhiteq (dest)
  ────────────────                    ────────────────
  GET  /get-agent/{id}          ->    POST /create-retell-llm   (general_tools,
  GET  /get-retell-llm/{id}     ->                               states, prompt…)
  GET  /get-conversation-flow/… ->    POST /create-conversation-flow
                                      POST /create-agent         (-> new engine)

The custom functions you see in the Retell dashboard live on the response engine
(the Retell LLM's `general_tools`, or a conversation flow's tools/nodes), NOT on
the agent — so copying the engine verbatim brings every function across.

Because both APIs tolerate unknown fields, the source objects are copied almost
unchanged: only server-managed fields are stripped and the engine reference is
repointed. Original Retell ids are preserved by default so consumer env vars
(RETELL_*_AGENT_ID) keep working after the DNS/base-URL flip.

Usage:
  # one agent, preview only
  python scripts/migrate_retell_agent.py \
      --retell-key   key_xxx \
      --arhiteq-base http://localhost:8080 \
      --arhiteq-key  <ARHITEQ_API_KEY> \
      --agent-id agent_175ad5d9bfe5ce919271b539ea \
      --dry-run

  # every agent in the Retell account, for real
  python scripts/migrate_retell_agent.py \
      --retell-key key_xxx \
      --arhiteq-base https://api.arhiteq.com \
      --arhiteq-key  <ARHITEQ_API_KEY> \
      --all

Options of note:
  --all                 migrate every agent returned by Retell /list-agents.
  --agent-id ID         migrate one agent (repeatable). Mutually fine with --all.
  --new-ids             let Arhiteq assign fresh ids instead of preserving
                        Retell's (use when importing into a workspace that must
                        not collide with the originals).
  --rewrite-url OLD=NEW rewrite custom-function webhook hosts, e.g.
                        --rewrite-url https://old.example.com=https://new.example.com
                        (repeatable; substring match on each tool `url`).
  --voice-id ID         override every agent's voice_id (Retell voice ids may not
                        exist in Arhiteq; defaults to passing the source value
                        through unchanged).
  --keep-kb-ids         keep the source `knowledge_base_ids` (they reference
                        Retell KB ids that won't exist in Arhiteq; dropped by
                        default).
  --dry-run             fetch + plan + print a summary, but write nothing.
"""

import argparse
import sys

import httpx

RETELL_BASE = "https://api.retellai.com"

# Server-managed fields we must not echo back on create.
LLM_READONLY = {"llm_id", "version", "is_published", "last_modification_timestamp"}
FLOW_READONLY = {
    "conversation_flow_id",
    "version",
    "is_published",
    "last_modification_timestamp",
}
AGENT_READONLY = {"version", "is_published", "last_modification_timestamp"}


def rewrite_tool_urls(tools: list[dict], rules: list[tuple[str, str]]) -> int:
    """Substring-rewrite the `url` of each custom tool in place. Returns count."""
    n = 0
    for tool in tools or []:
        url = tool.get("url")
        if not isinstance(url, str):
            continue
        new = url
        for old, repl in rules:
            new = new.replace(old, repl)
        if new != url:
            tool["url"] = new
            n += 1
    return n


def count_tools(engine_obj: dict) -> int:
    """Total custom/general tools across top level and any states."""
    total = len(engine_obj.get("general_tools") or [])
    for st in engine_obj.get("states") or []:
        total += len(st.get("tools") or [])
    return total


def apply_url_rewrites(engine_obj: dict, rules: list[tuple[str, str]]) -> int:
    if not rules:
        return 0
    n = rewrite_tool_urls(engine_obj.get("general_tools") or [], rules)
    for st in engine_obj.get("states") or []:
        n += rewrite_tool_urls(st.get("tools") or [], rules)
    # conversation-flow nodes carry tools under `nodes[].tool` / `tools`
    for node in engine_obj.get("nodes") or []:
        for key in ("tools", "tool"):
            val = node.get(key)
            if isinstance(val, list):
                n += rewrite_tool_urls(val, rules)
            elif isinstance(val, dict):
                n += rewrite_tool_urls([val], rules)
    return n


def strip_keys(obj: dict, keys: set[str]) -> dict:
    return {k: v for k, v in obj.items() if k not in keys and v is not None}


def migrate_llm(
    dst: httpx.Client, src_llm: dict, rules, keep_kb: bool, dry: bool
) -> tuple[str, int]:
    """Recreate a Retell LLM in Arhiteq. Returns (new_llm_id, tools_migrated)."""
    body = strip_keys(src_llm, LLM_READONLY)
    if not keep_kb:
        body.pop("knowledge_base_ids", None)
    apply_url_rewrites(body, rules)
    total = count_tools(body)
    if dry:
        return ("<dry-run-llm-id>", total)
    resp = dst.post("/create-retell-llm", json=body)
    resp.raise_for_status()
    return (resp.json()["llm_id"], total)


def migrate_flow(
    dst: httpx.Client, src_flow: dict, rules, dry: bool
) -> tuple[str, int]:
    body = strip_keys(src_flow, FLOW_READONLY)
    n = apply_url_rewrites(body, rules)
    if dry:
        return ("<dry-run-flow-id>", n)
    resp = dst.post("/create-conversation-flow", json=body)
    resp.raise_for_status()
    j = resp.json()
    return (j.get("conversation_flow_id") or j.get("id"), n)


def migrate_agent(
    retell: httpx.Client,
    dst: httpx.Client,
    agent_id: str,
    args,
    rules,
) -> None:
    src = retell.get(f"/get-agent/{agent_id}")
    src.raise_for_status()
    agent = src.json()
    engine = agent.get("response_engine") or {}
    etype = engine.get("type", "retell-llm")

    # 1. Recreate the response engine (this is where the custom functions live).
    if etype == "conversation-flow":
        flow_id = engine.get("conversation_flow_id")
        src_flow = retell.get(f"/get-conversation-flow/{flow_id}")
        src_flow.raise_for_status()
        new_engine_id, n_tools = migrate_flow(dst, src_flow.json(), rules, args.dry_run)
        new_engine = {
            "type": "conversation-flow",
            "conversation_flow_id": new_engine_id,
        }
    else:  # retell-llm (default)
        llm_id = engine.get("llm_id")
        src_llm = retell.get(f"/get-retell-llm/{llm_id}")
        src_llm.raise_for_status()
        new_engine_id, n_tools = migrate_llm(
            dst, src_llm.json(), rules, args.keep_kb_ids, args.dry_run
        )
        new_engine = {"type": "retell-llm", "llm_id": new_engine_id}

    # 2. Recreate the agent, repointed at the new engine.
    body = strip_keys(agent, AGENT_READONLY)
    body["response_engine"] = new_engine
    if args.new_ids:
        body.pop("agent_id", None)
    if args.voice_id:
        body["voice_id"] = args.voice_id
    body.setdefault("voice_id", "cartesia-sonic")  # required by Arhiteq

    if args.dry_run:
        print(
            f"[dry-run] {agent.get('agent_name')!r} "
            f"(agent_id={agent.get('agent_id')}) engine={etype} "
            f"tools={n_tools} -> would create agent + engine"
        )
        return

    resp = dst.post("/create-agent", json=body)
    resp.raise_for_status()
    out = resp.json()
    print(
        f"OK  {agent.get('agent_name')!r}: agent_id={out['agent_id']} "
        f"engine={etype}:{new_engine_id} tools={n_tools}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--retell-key", required=True, help="Retell API key (source)")
    ap.add_argument("--retell-base", default=RETELL_BASE)
    ap.add_argument("--arhiteq-base", default="http://localhost:8080")
    ap.add_argument("--arhiteq-key", required=True, help="Arhiteq API key (dest)")
    ap.add_argument("--agent-id", action="append", default=[], help="repeatable")
    ap.add_argument("--all", action="store_true", help="migrate every Retell agent")
    ap.add_argument("--new-ids", action="store_true")
    ap.add_argument("--keep-kb-ids", action="store_true")
    ap.add_argument("--voice-id")
    ap.add_argument(
        "--rewrite-url",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="rewrite custom-function webhook hosts (repeatable)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rules: list[tuple[str, str]] = []
    for r in args.rewrite_url:
        if "=" not in r:
            print(f"bad --rewrite-url (need OLD=NEW): {r}", file=sys.stderr)
            return 2
        old, new = r.split("=", 1)
        rules.append((old, new))

    retell = httpx.Client(
        base_url=args.retell_base,
        headers={"Authorization": f"Bearer {args.retell_key}"},
        timeout=30,
    )
    dst = httpx.Client(
        base_url=args.arhiteq_base,
        headers={"Authorization": f"Bearer {args.arhiteq_key}"},
        timeout=30,
    )

    agent_ids = list(args.agent_id)
    if args.all:
        resp = retell.get("/list-agents")
        resp.raise_for_status()
        agent_ids += [a["agent_id"] for a in resp.json()]
    # de-dup, preserve order
    agent_ids = list(dict.fromkeys(agent_ids))

    if not agent_ids:
        print("nothing to do: pass --agent-id or --all", file=sys.stderr)
        return 2

    print(
        f"migrating {len(agent_ids)} agent(s){' [DRY RUN]' if args.dry_run else ''}\n"
    )
    failures = 0
    for aid in agent_ids:
        try:
            migrate_agent(retell, dst, aid, args, rules)
        except httpx.HTTPStatusError as e:
            failures += 1
            print(
                f"FAIL {aid}: {e.response.status_code} {e.response.text[:300]}",
                file=sys.stderr,
            )
        except Exception as e:  # noqa: BLE001 - keep going on the rest
            failures += 1
            print(f"FAIL {aid}: {e}", file=sys.stderr)

    if failures:
        print(f"\n{failures} agent(s) failed", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

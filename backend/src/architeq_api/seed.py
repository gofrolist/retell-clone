"""Bootstrap a workspace + API key (and optionally demo data).

Usage:
    python -m architeq_api.seed --api-key key_... [--workspace-name "USAN"] [--demo]

The API key doubles as the webhook-signature HMAC key, so pass the exact key
the consumer has in RETELL_API_KEY to make cutover a pure env flip.
"""

import argparse
import asyncio

from sqlalchemy import select

from .auth import hash_key
from .db import get_engine, session_factory
from .ids import new_api_key
from .models import Agent, ApiKey, Base, Call, PhoneNumber, RetellLLM, Workspace, now_ms


async def seed(api_key: str | None, workspace_name: str, demo: bool) -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    key = api_key or new_api_key()
    async with session_factory()() as session:
        existing = await session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(key)))
        if existing:
            print(f"API key already present (workspace {existing.workspace_id})")
            return
        ws = Workspace(name=workspace_name)
        session.add(ws)
        await session.flush()
        session.add(
            ApiKey(workspace_id=ws.id, key_hash=hash_key(key), key_material=key, name="default")
        )
        if demo:
            llm = RetellLLM(
                workspace_id=ws.id,
                general_prompt="You are a helpful voice agent for {{company}}.",
                begin_message="Hello {{first_name}}, how can I help you today?",
            )
            session.add(llm)
            await session.flush()
            agent = Agent(
                workspace_id=ws.id,
                agent_name="Demo Agent",
                response_engine={"type": "retell-llm", "llm_id": llm.llm_id},
                voice_id="cartesia-sonic",
            )
            session.add(agent)
            await session.flush()
            session.add(
                PhoneNumber(
                    phone_number="+15555550100",
                    workspace_id=ws.id,
                    nickname="Demo number",
                    inbound_agent_id=agent.agent_id,
                    outbound_agent_id=agent.agent_id,
                )
            )
            # A few finished calls so Call History / Analytics / Contacts
            # render real rows out of the box.
            day_ms = 86_400_000
            samples = [
                (0, 143_000, "user_hangup", "Positive", True),
                (0, 21_000, "agent_hangup", "Neutral", True),
                (1, 0, "dial_no_answer", "Unknown", None),
                (2, 87_000, "user_hangup", "Neutral", True),
                (3, 34_000, "machine_detected", "Unknown", False),
                (4, 205_000, "agent_hangup", "Positive", True),
            ]
            for i, (days_ago, duration, reason, sentiment, ok) in enumerate(samples):
                start = now_ms() - days_ago * day_ms - 3_600_000
                connected = duration > 0
                session.add(
                    Call(
                        workspace_id=ws.id,
                        agent_id=agent.agent_id,
                        agent_name=agent.agent_name,
                        call_status="ended" if connected else "not_connected",
                        direction="outbound" if i % 3 else "inbound",
                        from_number="+15555550100",
                        to_number=f"+1626544{1000 + i * 37}",
                        start_timestamp=start,
                        end_timestamp=start + duration,
                        duration_ms=duration or None,
                        disconnection_reason=reason,
                        transcript=(
                            "Agent: Hello, this is the demo agent.\nUser: Hi there."
                            if connected
                            else None
                        ),
                        call_analysis=(
                            {
                                "call_summary": "Demo call seeded for the dashboard.",
                                "summary": "Demo call seeded for the dashboard.",
                                "user_sentiment": sentiment,
                                "call_successful": ok,
                                "in_voicemail": reason == "machine_detected",
                            }
                            if connected
                            else None
                        ),
                        created_at_ms=start,
                    )
                )
        await session.commit()
        print(f"workspace={ws.id}\napi_key={key}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", default=None, help="use this exact key (import mode)")
    parser.add_argument("--workspace-name", default="Default workspace")
    parser.add_argument("--demo", action="store_true", help="create demo agent/LLM/number")
    args = parser.parse_args()
    asyncio.run(seed(args.api_key, args.workspace_name, args.demo))


if __name__ == "__main__":
    main()

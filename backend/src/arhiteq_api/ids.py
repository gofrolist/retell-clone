"""Retell-style opaque identifiers.

Retell uses `call_<32 hex>`, `agent_<30+ alnum>`, `llm_<30+ alnum>`. We keep the
same prefixes so ids remain recognizably typed, and the same alphabet/length
constraints so downstream columns sized for Retell ids keep working.
"""

import secrets


def _hex(n: int) -> str:
    return secrets.token_hex(n // 2)


def new_call_id() -> str:
    return f"call_{_hex(32)}"


def new_agent_id() -> str:
    return f"agent_{_hex(30)}"


def new_llm_id() -> str:
    return f"llm_{_hex(30)}"


def new_folder_id() -> str:
    return f"folder_{_hex(24)}"


def new_phone_number_id() -> str:
    return f"pn_{_hex(30)}"


def new_api_key() -> str:
    return f"key_{_hex(32)}"


def new_workspace_id() -> str:
    return f"ws_{_hex(24)}"


def new_batch_call_id() -> str:
    return f"bc_{_hex(24)}"


def new_knowledge_base_id() -> str:
    return f"know_{_hex(24)}"


def new_source_id() -> str:
    return f"src_{_hex(24)}"


def new_conversation_flow_id() -> str:
    return f"conversation_flow_{_hex(24)}"


def new_chat_id() -> str:
    return f"chat_{_hex(32)}"


def new_contact_id() -> str:
    return f"contact_{_hex(24)}"


def new_alert_id() -> str:
    return f"alert_{_hex(24)}"


def new_cohort_id() -> str:
    return f"cohort_{_hex(24)}"


def new_invite_id() -> str:
    return f"invite_{_hex(24)}"


def new_invite_token() -> str:
    return secrets.token_urlsafe(32)

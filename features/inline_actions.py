from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol


class ActionHandler(Protocol):
    async def execute(self, send_fn, payload: str) -> None: ...


@dataclass
class JustTextAction:
    async def execute(self, send_fn, payload: str) -> None:
        await send_fn(payload)


ACTION_ALIASES: dict[str, str] = {
    "text": "just_text",
    "send_text": "just_text",
    "reply_text": "just_text",
    "message": "just_text",
    "plain_text": "just_text",
}


ACTION_REGISTRY: dict[str, ActionHandler] = {
    "just_text": JustTextAction(),
}


def normalize_action_type(action_type: str | None) -> str:
    raw = str(action_type or "").strip().lower()
    return ACTION_ALIASES.get(raw, raw)


def get_action_handler(action_type: str | None) -> ActionHandler | None:
    return ACTION_REGISTRY.get(normalize_action_type(action_type))


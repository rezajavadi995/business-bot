from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol


class ActionHandler(Protocol):
    async def execute(self, send_fn, payload: str) -> None: ...


@dataclass
class JustTextAction:
    async def execute(self, send_fn, payload: str) -> None:
        await send_fn(payload)


ACTION_REGISTRY: dict[str, ActionHandler] = {
    "just_text": JustTextAction(),
}


from __future__ import annotations

from dataclasses import dataclass

MAX_CALLBACK_LEN = 64


@dataclass(frozen=True)
class ParsedCallback:
    ns: str
    parts: list[str]


def cb(*parts: str) -> str:
    value = ":".join(parts)
    if len(value.encode("utf-8")) > MAX_CALLBACK_LEN:
        raise ValueError("callback_data exceeds Telegram 64-byte limit")
    return value


def parse(raw: str | None) -> ParsedCallback | None:
    if not raw or ":" not in raw:
        return None
    parts = raw.split(":")
    return ParsedCallback(ns=parts[0], parts=parts[1:])


def is_valid_im_callback(raw: str | None) -> bool:
    if not raw:
        return False
    if len(raw.encode("utf-8")) > MAX_CALLBACK_LEN:
        return False
    return raw.startswith("im:")


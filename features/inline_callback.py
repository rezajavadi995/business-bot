from __future__ import annotations

from dataclasses import dataclass
import hashlib

MAX_CALLBACK_LEN = 64

LEGACY_CALLBACK_ALIASES: dict[str, str] = {
    "inline:root": "im:root",
    "inline_menu:root": "im:root",
    "inline-menu:root": "im:root",
    "inline:toggle": "im:toggle",
    "inline_menu:toggle": "im:toggle",
    "inline-menu:toggle": "im:toggle",
    "inline:create": "im:create",
    "inline_menu:create": "im:create",
    "inline-menu:create": "im:create",
    "inline:addbtn": "im:addbtn",
    "inline_menu:addbtn": "im:addbtn",
    "inline-menu:addbtn": "im:addbtn",
    "inline:mgr": "im:mgr",
    "inline_menu:mgr": "im:mgr",
    "inline-menu:mgr": "im:mgr",
    "inline:edit": "im:edit",
    "inline_menu:edit": "im:edit",
    "inline-menu:edit": "im:edit",
    "inline:live": "im:live",
    "inline_menu:live": "im:live",
    "inline-menu:live": "im:live",
    "inline:active": "im:active",
    "inline_menu:active": "im:active",
    "inline-menu:active": "im:active",
    "inline:cancel": "im:cancel",
    "inline_menu:cancel": "im:cancel",
    "inline-menu:cancel": "im:cancel",
}

LEGACY_PREFIX_ALIASES: tuple[tuple[str, str], ...] = (
    ("im_btn:", "im:btn:"),
    ("inline:btn:", "im:btn:"),
    ("inline_btn:", "im:btn:"),
    ("inline_menu:btn:", "im:btn:"),
    ("inline-menu:btn:", "im:btn:"),
    ("menu_btn:", "im:btn:"),
    ("button:", "im:btn:"),
    ("im-button:", "im:btn:"),
    ("im:button:", "im:btn:"),
    ("inline:addbtnpick:", "im:addbtnpick:"),
    ("inline:mgrpick:", "im:mgrpick:"),
    ("inline:editpick:", "im:editpick:"),
    ("inline:livepick:", "im:livepick:"),
    ("inline:togpick:", "im:togpick:"),
    ("inline:togmenu:", "im:togmenu:"),
    ("inline:togbtn:", "im:togbtn:"),
    ("inline:delmenu:", "im:delmenu:"),
    ("inline:delbtn:", "im:delbtn:"),
)


@dataclass(frozen=True)
class ParsedCallback:
    ns: str
    parts: list[str]


@dataclass(frozen=True)
class NormalizedCallback:
    """Callback data after the single pre-dispatch normalization layer.

    canonical is the architecture-level vX:namespace:action:payload shape.
    runtime is the backward-compatible shape consumed by existing handlers.
    """
    raw: str
    runtime: str
    canonical: str
    namespace: str
    action: str
    payload: str
    legacy: bool = False


def cb(*parts: str) -> str:
    value = ":".join(parts)
    if len(value.encode("utf-8")) > MAX_CALLBACK_LEN:
        raise ValueError("callback_data exceeds Telegram 64-byte limit")
    return value


def normalize_callback_data(raw: str | None) -> str | None:
    """Map legacy inline callback_data shapes onto the current im:* namespace."""
    if not raw:
        return None
    value = str(raw).strip()
    if not value or len(value.encode("utf-8")) > MAX_CALLBACK_LEN:
        return None
    if value in LEGACY_CALLBACK_ALIASES:
        return LEGACY_CALLBACK_ALIASES[value]
    if value.startswith("im-btn-") and value[7:].isdigit():
        return f"im:btn:{value[7:]}"
    if value.startswith("inline-btn-") and value[11:].isdigit():
        return f"im:btn:{value[11:]}"
    for old, new in LEGACY_PREFIX_ALIASES:
        if value.startswith(old):
            return f"{new}{value[len(old):]}"
    return value


def canonicalize_callback_data(runtime: str | None) -> str | None:
    if not runtime:
        return None
    parts = str(runtime).split(":")
    namespace = parts[0] if parts else "unknown"
    action = parts[1] if len(parts) > 1 else "noop"
    payload = ":".join(parts[2:]) if len(parts) > 2 else ""
    return ":".join(["v1", namespace, action, payload]) if payload else ":".join(["v1", namespace, action])


def normalize_for_dispatch(raw: str | None) -> NormalizedCallback | None:
    if not raw:
        return None
    original = str(raw).strip()
    runtime = normalize_callback_data(original)
    if not runtime:
        return None
    canonical = canonicalize_callback_data(runtime)
    if not canonical:
        return None
    parts = runtime.split(":")
    namespace = parts[0] if parts else "unknown"
    action = parts[1] if len(parts) > 1 else "noop"
    payload = ":".join(parts[2:]) if len(parts) > 2 else ""
    return NormalizedCallback(original, runtime, canonical, namespace, action, payload, legacy=(runtime != original))


def callback_execution_key(callback_id: str | None, message_id: object | None, normalized_payload: str | None) -> str | None:
    if not callback_id:
        return None
    seed = f"{callback_id}:{message_id or '-'}:{normalized_payload or '-'}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def parse(raw: str | None) -> ParsedCallback | None:
    value = normalize_callback_data(raw)
    if not value or ":" not in value:
        return None
    parts = value.split(":")
    return ParsedCallback(ns=parts[0], parts=parts[1:])


def is_valid_im_callback(raw: str | None) -> bool:
    value = normalize_callback_data(raw)
    if not value:
        return False
    if len(value.encode("utf-8")) > MAX_CALLBACK_LEN:
        return False
    return value.startswith("im:")

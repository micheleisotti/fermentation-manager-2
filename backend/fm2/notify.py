"""
Push notifications for events (alerts, setpoint reached, ...), via ntfy or Telegram.

Sending happens on a background thread with a small queue, so a slow or unreachable
notification service never delays acquisition. Which events are sent is decided per rule on
the Settings page; every event is stored in the database regardless.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, Optional

import httpx

from .services.app_settings import AppSettings, rule_of

log = logging.getLogger(__name__)

PRIORITY = {"alert": "high", "info": "default", "good": "default"}
TAGS = {"alert": "warning", "info": "information_source", "good": "white_check_mark"}


class NotifyError(RuntimeError):
    pass


def send(cfg: dict, title: str, message: str, severity: str = "info",
         client: Optional[httpx.Client] = None) -> None:
    """Send one message with the configured channel. Raises NotifyError."""
    ch = cfg.get("channel", "none")
    own = client is None
    client = client or httpx.Client(timeout=10)
    try:
        if ch == "ntfy":
            headers = {"Title": title.encode("utf-8"), "Priority": PRIORITY.get(severity, "default"),
                       "Tags": TAGS.get(severity, "")}
            if cfg.get("ntfy_token"):
                headers["Authorization"] = f"Bearer {cfg['ntfy_token']}"
            r = client.post(f"{cfg['ntfy_server'].rstrip('/')}/{cfg['ntfy_topic']}",
                            content=message.encode("utf-8"), headers=headers)
        elif ch == "telegram":
            r = client.post(f"https://api.telegram.org/bot{cfg['telegram_token']}/sendMessage",
                            json={"chat_id": cfg["telegram_chat_id"], "text": f"{title}\n{message}"})
        else:
            raise NotifyError("no notification channel configured")
        if r.status_code >= 400:
            hint = ""
            if ch == "telegram" and r.status_code == 401:
                hint = (" — the bot token is not valid: copy it again from @BotFather (/mybots → your bot → API Token;"
                        " /revoke issues a new one and invalidates the old).")
            elif ch == "telegram" and r.status_code in (400, 403):
                hint = (" — check the chat id, and open the chat with your bot and press Start (a bot cannot write first).")
            raise NotifyError(f"{ch}: HTTP {r.status_code} {r.text[:200]}{hint}")
    except httpx.HTTPError as e:
        raise NotifyError(f"{ch}: {e}") from e
    finally:
        if own:
            client.close()


class Notifier:
    """Callable used by Acquisition: notifier(event_row)."""

    def __init__(self, settings: AppSettings, sender: Callable[..., None] = send):
        self.settings = settings
        self._send = sender
        self._q: queue.Queue = queue.Queue(maxsize=200)
        self._t = threading.Thread(target=self._run, name="fm2-notify", daemon=True)
        self._t.start()
        self.last_error: Optional[str] = None

    def __call__(self, ev) -> None:
        cfg = self.settings.get("notifications")
        if cfg["channel"] == "none":
            return
        rule = rule_of(ev.kind, ev.message)
        if rule is None or not cfg["rules"].get(rule, True):
            return
        title = {"alert": "⚠ FM2 alert", "good": "✓ FM2", "info": "FM2"}.get(ev.severity, "FM2")
        try:
            self._q.put_nowait((cfg, f"{title} · {ev.reactor_id}", ev.message, ev.severity))
        except queue.Full:
            log.warning("notification queue full: dropped %s", ev.message)

    def _run(self) -> None:
        while True:
            cfg, title, msg, sev = self._q.get()
            for attempt in (1, 2):
                try:
                    self._send(cfg, title, msg, sev)
                    self.last_error = None
                    break
                except NotifyError as e:
                    self.last_error = str(e)
                    log.warning("notification failed (attempt %d): %s", attempt, e)

    def drain(self, timeout: float = 5.0) -> None:
        """Tests: wait until the queue is empty."""
        import time
        end = time.monotonic() + timeout
        while not self._q.empty() and time.monotonic() < end:
            time.sleep(0.02)
        time.sleep(0.05)


def telegram_chats(token: str, client: Optional[httpx.Client] = None) -> list[dict]:
    """
    Chats that recently wrote to the bot (getUpdates), newest first, without bots:
    the user writes anything to the bot, then picks their chat here.
    """
    own = client is None
    client = client or httpx.Client(timeout=10)
    try:
        r = client.get(f"https://api.telegram.org/bot{token}/getUpdates")
        if r.status_code == 401:
            raise NotifyError("telegram: the bot token is not valid (HTTP 401)")
        if r.status_code >= 400:
            raise NotifyError(f"telegram: HTTP {r.status_code} {r.text[:200]}")
        seen: dict[int, dict] = {}
        for u in reversed(r.json().get("result", [])):
            msg = u.get("message") or u.get("edited_message") or u.get("channel_post") or (u.get("my_chat_member") or {})
            chat = msg.get("chat") or {}
            if not chat or chat.get("id") in seen or chat.get("is_bot"):
                continue
            name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) \
                or chat.get("username") or str(chat["id"])
            seen[chat["id"]] = {"id": str(chat["id"]), "name": name, "type": chat.get("type", "")}
        return list(seen.values())
    except httpx.HTTPError as e:
        raise NotifyError(f"telegram: {e}") from e
    finally:
        if own:
            client.close()

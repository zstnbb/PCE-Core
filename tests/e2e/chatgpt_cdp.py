"""Raw CDP helper for driving the currently-open ChatGPT page.

This module deliberately avoids Selenium for the active ChatGPT page because
the attached WebDriver session can lose page execution context on Cloudflare-
guarded ChatGPT tabs even when the underlying DevTools page socket remains
healthy.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import websocket


CHATGPT_HOSTS = ("chatgpt.com", "chat.openai.com")


class CDPError(RuntimeError):
    """Raised when a CDP command or page action fails."""


@dataclass(frozen=True)
class TargetInfo:
    id: str
    title: str
    url: str
    ws_url: str


class ChatGPTCDPClient:
    """Attach to the currently open ChatGPT tab via raw DevTools Protocol."""

    def __init__(self, port: int = 9222):
        self.port = int(port)
        self.ws: websocket.WebSocket | None = None
        self.target: TargetInfo | None = None
        self._msg_id = 0

    @property
    def debugger_base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _http_json(self, path: str) -> Any:
        with urllib.request.urlopen(f"{self.debugger_base}{path}", timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def list_targets(self) -> list[TargetInfo]:
        raw = self._http_json("/json/list")
        targets: list[TargetInfo] = []
        for item in raw:
            if item.get("type") != "page":
                continue
            url = item.get("url", "")
            if not any(host in url for host in CHATGPT_HOSTS):
                continue
            ws_url = item.get("webSocketDebuggerUrl")
            if not ws_url:
                continue
            targets.append(
                TargetInfo(
                    id=str(item.get("id", "")),
                    title=str(item.get("title", "")),
                    url=url,
                    ws_url=ws_url,
                )
            )
        return targets

    def pick_target(self) -> TargetInfo:
        targets = self.list_targets()
        if not targets:
            raise CDPError("no_chatgpt_page_found")

        def score(target: TargetInfo) -> tuple[int, int, int]:
            url = target.url.lower()
            title = target.title.lower()
            return (
                1 if "/c/" in url or url.rstrip("/") == "https://chatgpt.com" else 0,
                1 if "chatgpt" in title or title else 0,
                len(target.url),
            )

        return sorted(targets, key=score, reverse=True)[0]

    def connect(self) -> "ChatGPTCDPClient":
        self.close()
        self.target = self.pick_target()
        self.ws = websocket.create_connection(self.target.ws_url, timeout=20)
        self.command("Page.enable")
        self.command("Runtime.enable")
        self.command("DOM.enable")
        self.command("Network.enable")
        return self

    def close(self) -> None:
        if self.ws is None:
            return
        try:
            self.ws.close()
        finally:
            self.ws = None

    def reconnect(self) -> "ChatGPTCDPClient":
        time.sleep(0.5)
        return self.connect()

    def command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.ws is None:
            raise CDPError("cdp_not_connected")
        self._msg_id += 1
        msg_id = self._msg_id
        self.ws.send(
            json.dumps(
                {
                    "id": msg_id,
                    "method": method,
                    "params": params or {},
                }
            )
        )
        while True:
            raw = self.ws.recv()
            data = json.loads(raw)
            if data.get("id") != msg_id:
                continue
            if "error" in data:
                raise CDPError(f"{method}_failed: {data['error']}")
            return data.get("result", {})

    def evaluate(
        self,
        expression: str,
        *,
        return_by_value: bool = True,
        await_promise: bool = True,
    ) -> Any:
        payload = self.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": return_by_value,
                "awaitPromise": await_promise,
            },
        )
        if "exceptionDetails" in payload:
            raise CDPError(f"evaluation_failed: {payload['exceptionDetails']}")
        result = payload.get("result", {})
        if return_by_value:
            return result.get("value")
        return result

    def page_state(self) -> dict[str, Any]:
        return self.evaluate(
            """
            (() => {
              const input = document.querySelector(
                '#prompt-textarea, [contenteditable="true"][id="prompt-textarea"], [contenteditable="true"][data-testid*="prompt"]'
              );
              const stopBtn = [...document.querySelectorAll('button')].find((btn) => {
                const label = [btn.getAttribute('data-testid'), btn.getAttribute('aria-label'), btn.innerText || '']
                  .filter(Boolean).join(' ');
                return /stop-button|\bstop\b/i.test(label);
              });
              return {
                title: document.title,
                url: location.href,
                readyState: document.readyState,
                hasInput: !!input,
                stopVisible: !!stopBtn && stopBtn.offsetParent !== null,
                bodyText: (document.body && document.body.innerText || '').slice(0, 4000),
              };
            })()
            """
        )

    def wait_for(
        self,
        expression: str,
        *,
        timeout_s: float = 20,
        poll_s: float = 0.5,
        description: str = "condition",
    ) -> Any:
        deadline = time.time() + timeout_s
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                value = self.evaluate(expression)
                if value:
                    return value
            except Exception as exc:
                last_error = exc
            time.sleep(poll_s)
        if last_error is not None:
            raise CDPError(f"timeout_waiting_for_{description}: {last_error}")
        raise CDPError(f"timeout_waiting_for_{description}")

    def navigate(self, url: str, *, timeout_s: float = 20) -> dict[str, Any]:
        self.command("Page.navigate", {"url": url})
        self.wait_for(
            "document.readyState === 'complete' || document.readyState === 'interactive'",
            timeout_s=timeout_s,
            description="page_ready",
        )
        time.sleep(1.0)
        return self.page_state()

    def navigate_home(self) -> dict[str, Any]:
        return self.navigate("https://chatgpt.com/")

    def open_settings(self) -> dict[str, Any]:
        return self.navigate("https://chatgpt.com/#settings")

    def open_temporary_chat(self) -> dict[str, Any]:
        state = self.navigate("https://chatgpt.com/?temporary-chat=true")
        self.dismiss_temporary_modal()
        return state

    def wait_for_input(self, *, timeout_s: float = 20) -> bool:
        self.wait_for(
            """
            (() => {
              const el = document.querySelector(
                '#prompt-textarea, [contenteditable="true"][id="prompt-textarea"], [contenteditable="true"][data-testid*="prompt"]'
              );
              return !!el && el.offsetParent !== null;
            })()
            """,
            timeout_s=timeout_s,
            description="chat_input",
        )
        return True

    def body_contains(self, needles: Iterable[str]) -> bool:
        needles_json = json.dumps([needle.lower() for needle in needles], ensure_ascii=False)
        return bool(
            self.evaluate(
                f"""
                (() => {{
                  const body = (document.body && document.body.innerText || '').toLowerCase();
                  return {needles_json}.some((needle) => body.includes(needle));
                }})()
                """
            )
        )

    def click_first(self, selectors: Iterable[str]) -> dict[str, Any]:
        selectors_json = json.dumps(list(selectors), ensure_ascii=False)
        result = self.evaluate(
            f"""
            (() => {{
              const selectors = {selectors_json};
              const isVisible = (el) => {{
                if (!el) return false;
                const style = getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
              }};
              for (const selector of selectors) {{
                const candidates = [...document.querySelectorAll(selector)];
                const target = candidates.find(isVisible);
                if (!target) continue;
                target.scrollIntoView({{block: 'center', inline: 'center'}});
                target.click();
                return {{
                  ok: true,
                  selector,
                  text: (target.innerText || '').trim().slice(0, 200),
                  aria: target.getAttribute('aria-label'),
                  testid: target.getAttribute('data-testid'),
                }};
              }}
              return {{ ok: false, selectors }};
            }})()
            """
        )
        if not result.get("ok"):
            raise CDPError(f"click_failed: {result}")
        time.sleep(0.8)
        return result

    def click_by_labels(
        self,
        labels: Iterable[str],
        *,
        root_selector: str | None = None,
        allow_roles: tuple[str, ...] = ("button", "a", "div", "span"),
    ) -> dict[str, Any]:
        labels_json = json.dumps([label.lower() for label in labels], ensure_ascii=False)
        root_selector_json = json.dumps(root_selector, ensure_ascii=False)
        allow_roles_json = json.dumps(list(allow_roles), ensure_ascii=False)
        result = self.evaluate(
            f"""
            (() => {{
              const lowered = {labels_json};
              const rootSelector = {root_selector_json};
              const allowed = new Set({allow_roles_json});
              const root = rootSelector ? document.querySelector(rootSelector) : document;
              if (!root) return {{ ok: false, reason: 'root_not_found', rootSelector }};
              const isVisible = (el) => {{
                if (!el) return false;
                const style = getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
              }};
              const textFor = (el) => [
                el.innerText || '',
                el.textContent || '',
                el.getAttribute('aria-label') || '',
                el.getAttribute('data-testid') || '',
              ].join(' ').replace(/\\s+/g, ' ').trim().toLowerCase();
              const scoreFor = (el) => {{
                const raw = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                const text = textFor(el);
                const lines = (el.innerText || el.textContent || '')
                  .split(/\\n+/)
                  .map((line) => line.replace(/\\s+/g, ' ').trim().toLowerCase())
                  .filter(Boolean);
                let score = 100;
                for (const label of lowered) {{
                  if (lines.some((line) => line === label)) score = Math.min(score, 0);
                  else if (text === label) score = Math.min(score, 1);
                  else if (lines.some((line) => line.includes(label))) score = Math.min(score, 2);
                  else if (text.includes(label)) score = Math.min(score, 3);
                }}
                return [score, raw.length || 9999];
              }};
              const candidates = [...root.querySelectorAll('*')].filter((el) => {{
                if (!allowed.has(el.tagName.toLowerCase()) && el.getAttribute('role') !== 'menuitem' && el.getAttribute('role') !== 'button') {{
                  return false;
                }}
                if (!isVisible(el)) return false;
                const text = textFor(el);
                return lowered.some((label) => text.includes(label));
              }});
              if (!candidates.length) {{
                return {{ ok: false, reason: 'label_not_found', labels: lowered }};
              }}
              candidates.sort((a, b) => {{
                const as = scoreFor(a);
                const bs = scoreFor(b);
                return as[0] - bs[0] || as[1] - bs[1];
              }});
              const target = candidates[0];
              target.scrollIntoView({{block: 'center', inline: 'center'}});
              target.click();
              return {{
                ok: true,
                text: (target.innerText || target.textContent || '').trim().slice(0, 200),
                aria: target.getAttribute('aria-label'),
                testid: target.getAttribute('data-testid'),
              }};
            }})()
            """
        )
        if not result.get("ok"):
            raise CDPError(f"click_by_label_failed: {result}")
        time.sleep(0.8)
        return result

    def click_new_chat(self) -> dict[str, Any]:
        try:
            return self.click_first(
                [
                    'button[data-testid*="new-chat"]',
                    'button[aria-label*="New chat"]',
                    'button[aria-label*="新聊天"]',
                    'button[aria-label*="新建聊天"]',
                    'a[href="/"] button',
                    'a[href="/"]',
                ]
            )
        except Exception:
            self.navigate_home()
            self.wait_for_input(timeout_s=20)
            return {"ok": True, "selector": "Page.navigate(home)"}

    def open_existing_conversation(self) -> dict[str, Any]:
        state = self.page_state()
        if "/c/" in state.get("url", ""):
            return state
        try:
            result = self.click_first(
                [
                    'a[href^="/c/"]',
                    'a[href*="/c/"]',
                ]
            )
        except Exception as exc:
            raise CDPError(f"existing_conversation_not_found: {exc}") from exc
        self.wait_for(
            "location.href.includes('/c/')",
            timeout_s=12,
            poll_s=0.4,
            description="existing_conversation_route",
        )
        state = self.page_state()
        state["navigation"] = result
        return state

    def dismiss_temporary_modal(self) -> bool:
        for _ in range(10):
            try:
                self.click_by_labels(["Continue", "继续", "Start temporary chat", "开始临时聊天"])
                return True
            except Exception:
                pass
            if self.wait_until_dialog_hidden(timeout_s=0.5, raise_on_timeout=False):
                return True
        return False

    def wait_until_dialog_hidden(
        self,
        *,
        timeout_s: float = 8,
        raise_on_timeout: bool = True,
    ) -> bool:
        expr = """
        (() => {
          const dialogs = [...document.querySelectorAll('[role="dialog"]')].filter((el) => {
            const style = getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden';
          });
          return dialogs.length === 0;
        })()
        """
        try:
            self.wait_for(expr, timeout_s=timeout_s, poll_s=0.4, description="dialogs_hidden")
            return True
        except Exception:
            if raise_on_timeout:
                raise
            return False

    def dismiss_known_dialogs(self) -> bool:
        """Close upload duplicate / transient dialogs without touching normal chat UI."""
        result = self.evaluate(
            """
            (() => {
              const isVisible = (el) => {
                if (!el) return false;
                const style = getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
              };
              const dialogs = [...document.querySelectorAll('[role="dialog"]')].filter(isVisible);
              for (const dialog of dialogs) {
                const text = (dialog.innerText || dialog.textContent || '').trim();
                const known = /already uploaded|try uploading|upload some new|duplicate/i.test(text) ||
                  /\u4f60\u5df2\u4e0a\u4f20\u8fc7\u6b64\u6587\u4ef6|\u5c1d\u8bd5\u4e0a\u4f20\u4e00\u4e9b\u65b0\u5185\u5bb9/.test(text);
                if (!known) continue;
                const buttons = [...dialog.querySelectorAll('button, [role="button"]')].filter(isVisible);
                const target = buttons.find((btn) => {
                  const label = [
                    btn.innerText || '',
                    btn.textContent || '',
                    btn.getAttribute('aria-label') || '',
                    btn.getAttribute('data-testid') || ''
                  ].join(' ').trim();
                  return /^(ok|okay|close|dismiss|got it|cancel)$/i.test(label) ||
                    /\u786e\u5b9a|\u5173\u95ed|\u53d6\u6d88|\u77e5\u9053\u4e86/.test(label);
                }) || buttons[buttons.length - 1];
                if (!target) continue;
                target.click();
                return { ok: true, text: text.slice(0, 200) };
              }
              return { ok: false };
            })()
            """
        )
        if result.get("ok"):
            time.sleep(0.8)
            return True
        return False

    def set_prompt_text(self, text: str) -> dict[str, Any]:
        payload = json.dumps(text, ensure_ascii=False)
        result = self.evaluate(
            f"""
            (() => {{
              const input = document.querySelector(
                '#prompt-textarea, [contenteditable="true"][id="prompt-textarea"], [contenteditable="true"][data-testid*="prompt"]'
              );
              if (!input) return {{ ok: false, reason: 'input_not_found' }};
              input.focus();
              const value = {payload};
              if ('value' in input) {{
                input.value = value;
              }} else {{
                input.textContent = value;
              }}
              input.dispatchEvent(new InputEvent('input', {{
                bubbles: true,
                data: value,
                inputType: 'insertText',
              }}));
              input.dispatchEvent(new Event('change', {{ bubbles: true }}));
              return {{
                ok: true,
                text: input.innerText || input.textContent || input.value || '',
              }};
            }})()
            """
        )
        if not result.get("ok"):
            raise CDPError(f"set_prompt_failed: {result}")
        return result

    def click_send(self) -> dict[str, Any]:
        selectors = [
            'button[data-testid="send-button"]',
            'button[aria-label*="Send prompt"]',
            'button[aria-label*="发送提示"]',
            'button[aria-label*="发送"]',
        ]
        self.wait_for_send_enabled(timeout_s=35)
        return self.click_first(selectors)

    def wait_for_send_enabled(self, *, timeout_s: float = 30) -> bool:
        self.wait_for(
            """
            (() => {
              const buttons = [...document.querySelectorAll('button')];
              return buttons.some((btn) => {
                if (btn.offsetParent === null) return false;
                if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return false;
                const label = [
                  btn.getAttribute('data-testid'),
                  btn.getAttribute('aria-label'),
                  btn.innerText || ''
                ].filter(Boolean).join(' ');
                return /send-button|send prompt/i.test(label);
              });
            })()
            """,
            timeout_s=timeout_s,
            poll_s=0.5,
            description="send_button_enabled",
        )
        return True

    def send_message(self, text: str, *, wait_before_click_s: float = 1.0) -> dict[str, Any]:
        assistant_before = self.assistant_turn_count()
        self.set_prompt_text(text)
        time.sleep(wait_before_click_s)
        click = self.click_send()
        return {
            "assistant_turns_before": assistant_before,
            "click": click,
        }

    def assistant_turn_count(self) -> int:
        return int(
            self.evaluate(
                """
                (() => {
                  const roleNodes = [...document.querySelectorAll('[data-message-author-role="assistant"]')]
                    .filter((el) => el.offsetParent !== null);
                  const turnNodes = [...document.querySelectorAll('[data-testid^="conversation-turn"][data-turn="assistant"], [data-turn="assistant"]')]
                    .filter((el) => el.offsetParent !== null);
                  const nodes = [...roleNodes, ...turnNodes];
                  return nodes.filter((el, i) =>
                    !nodes.some((other, j) => i !== j && other.contains(el))
                  ).length;
                })()
                """
            )
            or 0
        )

    def user_turn_count(self) -> int:
        return int(
            self.evaluate(
                """
                (() => {
                  const roleNodes = [...document.querySelectorAll('[data-message-author-role="user"]')]
                    .filter((el) => el.offsetParent !== null);
                  const turnNodes = [...document.querySelectorAll('[data-testid^="conversation-turn"][data-turn="user"], [data-turn="user"]')]
                    .filter((el) => el.offsetParent !== null);
                  const nodes = [...roleNodes, ...turnNodes];
                  return nodes.filter((el, i) =>
                    !nodes.some((other, j) => i !== j && other.contains(el))
                  ).length;
                })()
                """
            )
            or 0
        )

    def assistant_texts(self) -> list[str]:
        return list(
            self.evaluate(
                """
                (() => {
                  const roleNodes = [...document.querySelectorAll('[data-message-author-role="assistant"]')]
                    .filter((el) => el.offsetParent !== null);
                  const turnNodes = [...document.querySelectorAll('[data-testid^="conversation-turn"][data-turn="assistant"], [data-turn="assistant"]')]
                    .filter((el) => el.offsetParent !== null);
                  const cleanAssistantText = (value) => value
                    .replace(/\\b(?:thought|thinking)\\s+(?:for\\s+)?\\d+\\s*(?:s|sec|second|seconds)\\b/gi, '')
                    .replace(/\\u5df2\\u601d\\u8003\\s*\\d+\\s*\\u79d2/g, '')
                    .replace(/^\\s*ChatGPT\\s*(?:said|\\u8bf4)?\\s*[:\\uff1a]?\\s*/i, '')
                    .replace(/\\s+/g, ' ')
                    .trim();
                  const isPlaceholder = (value) => {
                    const normalized = value.replace(/\\s+/g, ' ').trim();
                    return /^chatgpt(?:\\s+said)?[:：]?$/i.test(normalized);
                  };
                  const collect = (nodes) => {
                    const texts = [];
                    for (const el of nodes) {
                      const text = cleanAssistantText(el.innerText || el.textContent || '');
                      if (!text) continue;
                      if (isPlaceholder(text)) continue;
                      if (!texts.includes(text)) texts.push(text);
                    }
                    return texts;
                  };
                  return collect([...roleNodes, ...turnNodes]);
                })()
                """
            )
            or []
        )

    def stop_button_visible(self) -> bool:
        return bool(
            self.evaluate(
                """
                (() => {
                  const buttons = [...document.querySelectorAll('button')];
                  return buttons.some((btn) => {
                    const label = [btn.getAttribute('data-testid'), btn.getAttribute('aria-label'), btn.innerText || '']
                      .filter(Boolean).join(' ');
                    return /stop-button|\bstop\b/i.test(label) && btn.offsetParent !== null;
                  });
                })()
                """
            )
        )

    def wait_for_stop_button(self, *, timeout_s: float = 12) -> bool:
        try:
            self.wait_for(
                """
                (() => {
                  const buttons = [...document.querySelectorAll('button')];
                  return buttons.some((btn) => {
                    const label = [btn.getAttribute('data-testid'), btn.getAttribute('aria-label'), btn.innerText || '']
                      .filter(Boolean).join(' ');
                    return /stop-button|\bstop\b/i.test(label) && btn.offsetParent !== null;
                  });
                })()
                """,
                timeout_s=timeout_s,
                poll_s=0.4,
                description="stop_button",
            )
            return True
        except Exception:
            return False

    def wait_for_response_complete(
        self,
        *,
        previous_assistant_count: int,
        token: str | None = None,
        timeout_s: float = 90,
    ) -> dict[str, Any]:
        soft_deadline = time.time() + timeout_s
        # Business accounts can create an assistant "thinking" turn quickly and
        # fill in the final text about a minute later. If the turn exists, keep
        # polling long enough to avoid marking that delayed fill as a failure.
        grace_deadline = soft_deadline + 90
        stable_hits = 0
        last_snapshot = ""
        ready_seen_at: float | None = None
        last_debug: dict[str, Any] = {}
        while time.time() < grace_deadline:
            now = time.time()
            texts = self.assistant_texts()
            assistant_turns_after = self.assistant_turn_count()
            stop_visible = self.stop_button_visible()
            joined = "\n\n".join(texts)
            has_new_turn = assistant_turns_after > previous_assistant_count
            token_seen = token in joined if token else has_new_turn
            assistant_token_occurrences = 0
            if token:
                assistant_token_occurrences = int(
                    self.evaluate(
                        f"""
                        (() => {{
                          const roleNodes = [...document.querySelectorAll('[data-message-author-role="assistant"]')]
                            .filter((el) => el.offsetParent !== null);
                          const turnNodes = [...document.querySelectorAll('[data-testid^="conversation-turn"][data-turn="assistant"], [data-turn="assistant"]')]
                            .filter((el) => el.offsetParent !== null);
                          const nodes = [...roleNodes, ...turnNodes];
                          const text = nodes.map((el) => (el.innerText || el.textContent || '')).join('\\n');
                          const token = {json.dumps(token, ensure_ascii=False)};
                          return token ? text.split(token).length - 1 : 0;
                        }})()
                        """
                    )
                    or 0
                )
                token_seen = token_seen or assistant_token_occurrences > 0
            last_debug = {
                "assistant_turns_after": assistant_turns_after,
                "previous_assistant_count": previous_assistant_count,
                "stop_visible": stop_visible,
                "token": token,
                "token_seen": token_seen,
                "assistant_token_occurrences": assistant_token_occurrences,
                "stable_hits": stable_hits,
                "in_grace": now >= soft_deadline,
                "texts": [text[:240] for text in texts[-3:]],
            }
            if now >= soft_deadline and not has_new_turn:
                break
            ready = token_seen and joined.strip()
            if ready:
                if ready_seen_at is None:
                    ready_seen_at = time.time()
                if joined == last_snapshot:
                    stable_hits += 1
                else:
                    stable_hits = 1
                    last_snapshot = joined
                stop_grace_elapsed = time.time() - ready_seen_at >= 5
                if stable_hits >= 3 and (not stop_visible or stop_grace_elapsed):
                    return {
                        "assistant_texts": texts,
                        "assistant_turns_after": assistant_turns_after,
                        "token_seen": token_seen,
                        "assistant_token_occurrences": assistant_token_occurrences,
                        "stop_visible": stop_visible,
                    }
            elif token and assistant_token_occurrences > 0 and not stop_visible and has_new_turn:
                stable_hits += 1
                if stable_hits >= 3:
                    return {
                        "assistant_texts": texts,
                        "assistant_turns_after": assistant_turns_after,
                        "token_seen": True,
                        "assistant_token_occurrences": assistant_token_occurrences,
                        "stop_visible": stop_visible,
                    }
            else:
                stable_hits = 0
                ready_seen_at = None
                if joined:
                    last_snapshot = joined
            time.sleep(1.0)
        raise CDPError(
            "response_not_stable "
            + json.dumps(last_debug, ensure_ascii=False, default=str)[:1200]
        )

    def trigger_manual_capture(self) -> None:
        self.evaluate(
            """
            (() => {
              document.dispatchEvent(new CustomEvent('pce-manual-capture'));
              return true;
            })()
            """
        )

    def take_screenshot(self, path: str | Path) -> str:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        previous_timeout = None
        if self.ws is not None:
            previous_timeout = self.ws.gettimeout()
            self.ws.settimeout(60)
        try:
            data = self.command("Page.captureScreenshot", {"format": "png"}).get("data")
        except (TimeoutError, websocket.WebSocketTimeoutException):
            self.reconnect()
            if self.ws is not None:
                self.ws.settimeout(60)
            data = self.command("Page.captureScreenshot", {"format": "png"}).get("data")
        finally:
            if self.ws is not None:
                self.ws.settimeout(previous_timeout if previous_timeout is not None else 20)
        if not data:
            raise CDPError("screenshot_failed")
        target.write_bytes(base64.b64decode(data))
        return str(target)

    def set_offline(self, offline: bool) -> None:
        params = {
            "offline": bool(offline),
            "latency": 0,
            "downloadThroughput": 0 if offline else -1,
            "uploadThroughput": 0 if offline else -1,
            "connectionType": "none" if offline else "wifi",
        }
        self.command("Network.emulateNetworkConditions", params)

    def wait_for_error_banner(self, *, timeout_s: float = 20) -> bool:
        try:
            self.wait_for(
                """
                (() => {
                  const body = (document.body && document.body.innerText || '').toLowerCase();
                  const retryVisible = [...document.querySelectorAll('button')].some((btn) => {
                    if (btn.offsetParent === null) return false;
                    const text = [btn.innerText || '', btn.textContent || '', btn.getAttribute('aria-label') || '']
                      .join(' ')
                      .toLowerCase();
                    return /retry|重试/.test(text);
                  });
                  if (retryVisible) return true;
                  return [
                    'something went wrong',
                    'network error',
                    'request failed',
                    '请求失败',
                    '出现问题',
                    '重新生成',
                    'retry',
                    '重试'
                  ].some((needle) => body.includes(needle));
                })()
                """,
                timeout_s=timeout_s,
                poll_s=0.8,
                description="error_banner",
            )
            return True
        except Exception:
            return False

    def open_model_menu(self) -> dict[str, Any]:
        return self.click_first(
            [
                'button[data-testid="model-switcher-dropdown-button"]',
                'button[aria-label*="Model"]',
                'button[aria-label*="模型"]',
                'button[aria-label*="切换模型"]',
            ]
        )

    def press_escape(self) -> None:
        for event_type in ("keyDown", "keyUp"):
            self.command(
                "Input.dispatchKeyEvent",
                {
                    "type": event_type,
                    "key": "Escape",
                    "code": "Escape",
                    "windowsVirtualKeyCode": 27,
                    "nativeVirtualKeyCode": 27,
                },
            )
        time.sleep(0.2)

    def switch_model(self, labels: Iterable[str]) -> dict[str, Any]:
        self.open_model_menu()
        try:
            result = self.click_by_labels(
                labels,
                root_selector='[role="menu"], [role="listbox"], [data-radix-popper-content-wrapper]',
            )
        except Exception:
            result = self.click_by_labels(labels)
        try:
            self.press_escape()
        except Exception:
            pass
        return result

    def click_last_visible_button_by_aria(self, labels: Iterable[str]) -> dict[str, Any]:
        labels_json = json.dumps([label.lower() for label in labels], ensure_ascii=False)
        result = self.evaluate(
            f"""
            (() => {{
              const lowered = {labels_json};
              const buttons = [...document.querySelectorAll('button')].filter((btn) => {{
                if (btn.offsetParent === null) return false;
                const text = [
                  btn.getAttribute('aria-label') || '',
                  btn.getAttribute('data-testid') || '',
                  btn.innerText || ''
                ].join(' ').toLowerCase();
                return lowered.some((label) => text.includes(label));
              }});
              if (!buttons.length) return {{ ok: false, reason: 'button_not_found', labels: lowered }};
              const target = buttons[buttons.length - 1];
              target.scrollIntoView({{block: 'center', inline: 'center'}});
              target.click();
              return {{
                ok: true,
                text: (target.innerText || '').trim(),
                aria: target.getAttribute('aria-label'),
                testid: target.getAttribute('data-testid'),
              }};
            }})()
            """
        )
        if not result.get("ok"):
            raise CDPError(f"click_last_visible_button_failed: {result}")
        time.sleep(0.8)
        return result

    def edit_last_user_message(self, text: str) -> dict[str, Any]:
        self.click_last_visible_button_by_aria(["Edit", "编辑", "編輯"])
        time.sleep(0.8)
        self.set_prompt_text(text)
        time.sleep(0.8)
        return self.click_send()

    def click_regenerate(self) -> dict[str, Any]:
        try:
            return self.click_first(
                [
                    'button[data-testid*="regenerate"]',
                    'button[aria-label*="Regenerate"]',
                    'button[aria-label*="重新生成"]',
                    'button[aria-label*="Try again"]',
                    'button[aria-label*="重试"]',
                ]
            )
        except Exception:
            pass

        try:
            self.click_retry_model_menu()
            return self.click_by_labels(
                ["Regenerate", "重新生成", "Try again", "Retry", "重试", "后重试"],
                root_selector='[role="menu"], [role="listbox"], [data-radix-popper-content-wrapper]',
            )
        except Exception:
            self.press_escape()

        self.click_last_visible_button_by_aria(["More actions", "更多操作"])
        return self.click_by_labels(["Regenerate", "重新生成", "Try again", "Retry", "重试", "后重试"])

    def click_retry_model_menu(self) -> dict[str, Any]:
        result = self.evaluate(
            """
            (() => {
              const buttons = [...document.querySelectorAll('button')].filter((btn) => {
                if (btn.offsetParent === null) return false;
                const label = [
                  btn.getAttribute('aria-label') || '',
                  btn.getAttribute('data-testid') || '',
                  btn.innerText || '',
                  btn.textContent || ''
                ].join(' ');
                return /Switch model|Retry|Try again|切换模型|重试|重新生成/i.test(label);
              });
              if (!buttons.length) return { ok: false, reason: 'retry_model_button_not_found' };
              const target = buttons[buttons.length - 1];
              target.scrollIntoView({block: 'center', inline: 'center'});
              target.focus();
              for (const type of ['pointerover', 'mouseover', 'pointerenter', 'mouseenter', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                const event = type.startsWith('pointer')
                  ? new PointerEvent(type, {bubbles: true, pointerType: 'mouse', button: 0})
                  : new MouseEvent(type, {bubbles: true, button: 0});
                target.dispatchEvent(event);
              }
              return {
                ok: true,
                text: (target.innerText || '').trim(),
                aria: target.getAttribute('aria-label'),
                testid: target.getAttribute('data-testid'),
                expanded: target.getAttribute('aria-expanded'),
              };
            })()
            """
        )
        if not result.get("ok"):
            raise CDPError(f"click_retry_model_menu_failed: {result}")
        time.sleep(0.8)
        return result

    def flip_branch(self, *, direction: str = "prev") -> dict[str, Any]:
        labels = ["Previous", "上一个", "上一条", "上一分支"]
        if direction == "next":
            labels = ["Next", "下一个", "下一条", "下一分支"]
        return self.click_last_visible_button_by_aria(labels)

    def _query_selector_node_id(self, selector: str) -> int:
        document = self.command("DOM.getDocument", {"depth": -1, "pierce": True})
        root_id = (document.get("root") or {}).get("nodeId")
        if not root_id:
            return 0
        return int(
            self.command("DOM.querySelector", {"nodeId": root_id, "selector": selector}).get(
                "nodeId"
            )
            or 0
        )

    def _locate_file_input(self, *, accept_image: bool) -> tuple[int, dict[str, Any]]:
        selectors = (
            ["#upload-photos", "#upload-camera", 'input[type="file"][accept*="image"]']
            if accept_image
            else ["#upload-files", 'input[type="file"]:not([accept*="image"])']
        )
        last_result: dict[str, Any] = {"ok": False, "reason": "file_input_not_found"}
        for selector in selectors:
            result = self.evaluate(
                f"""
                (() => {{
                  const target = document.querySelector({json.dumps(selector)});
                  if (!target) return {{ ok: false, selector: {json.dumps(selector)}, reason: 'selector_not_found' }};
                  return {{
                    ok: true,
                    selector: {json.dumps(selector)},
                    id: target.id || null,
                    accept: target.getAttribute('accept'),
                    multiple: !!target.multiple,
                    hidden: target.offsetParent === null,
                  }};
                }})()
                """
            )
            last_result = result
            if not result.get("ok"):
                continue
            node_id = self._query_selector_node_id(selector)
            if node_id:
                return node_id, result
        raise CDPError(f"file_input_node_not_found: {last_result}")

    def upload_files(self, paths: Iterable[str], *, accept_image: bool = False) -> dict[str, Any]:
        path_list = [str(Path(path).resolve()) for path in paths]
        if not path_list:
            raise CDPError("upload_paths_empty")
        node_id, meta = self._locate_file_input(accept_image=accept_image)
        self.command(
            "DOM.setFileInputFiles",
            {
                "nodeId": node_id,
                "files": path_list,
            },
        )
        selector = meta.get("selector") or ("#upload-photos" if accept_image else "#upload-files")
        self.evaluate(
            f"""
            (() => {{
              const target = document.querySelector({json.dumps(selector)});
              if (!target) return false;
              target.dispatchEvent(new Event('input', {{ bubbles: true }}));
              target.dispatchEvent(new Event('change', {{ bubbles: true }}));
              return true;
            }})()
            """
        )
        time.sleep(1.0)
        return {
            "files": path_list,
            "meta": meta,
        }

    def navigate_to_gpt(self, url: str | None = None) -> dict[str, Any]:
        if url:
            return self.navigate(url)
        self.click_by_labels(["GPTs", "GPT"])
        time.sleep(1.0)
        state = self.page_state()
        if not any(part in state["url"] for part in ("/g/", "/gpts")):
            raise CDPError("custom_gpt_route_not_reached")
        return state

    def navigate_to_project(self, url: str | None = None) -> dict[str, Any]:
        def is_project_url(value: str) -> bool:
            return "/projects" in value or ("/g/g-p-" in value and "/project" in value)

        if url:
            state = self.navigate(url)
            if not is_project_url(state["url"]):
                raise CDPError("project_route_not_reached")
            return state
        self.navigate("https://chatgpt.com/projects")
        time.sleep(1.5)
        state = self.page_state()
        if not is_project_url(state["url"]):
            raise CDPError("project_route_not_reached")
        return state

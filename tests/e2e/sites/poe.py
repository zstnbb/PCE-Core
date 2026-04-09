"""Poe (poe.com) site adapter."""

from .base import BaseSiteAdapter


class PoeAdapter(BaseSiteAdapter):
    name = "poe"
    provider = "poe"
    url = "https://poe.com/"

    input_selector = 'textarea.GrowingTextArea_textArea__ZWQbP, textarea[placeholder*="聊天" i], textarea[placeholder*="chat" i], textarea'
    send_button_selector = 'button[aria-label="发送信息"], button[aria-label="Send message"]'
    response_container_selector = '[class*="Message_botMessage"], [class*="botMessage"], [class*="ChatMessage"]'

    page_load_wait_s = 5
    response_timeout_s = 60

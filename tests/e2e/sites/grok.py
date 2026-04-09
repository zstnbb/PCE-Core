"""Grok (grok.com) site adapter."""

from .base import BaseSiteAdapter


class GrokAdapter(BaseSiteAdapter):
    name = "grok"
    provider = "xai"
    url = "https://grok.com/"

    input_selector = 'div.tiptap.ProseMirror[contenteditable="true"]'
    send_button_selector = 'button[type="submit"], button[aria-label="提交"], button[aria-label="Send"]'
    response_container_selector = '[class*="message"], [class*="response"], .prose'

    page_load_wait_s = 5
    response_timeout_s = 60

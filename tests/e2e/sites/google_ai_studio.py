"""Google AI Studio (aistudio.google.com) site adapter."""

from .base import BaseSiteAdapter


class GoogleAIStudioAdapter(BaseSiteAdapter):
    name = "googleaistudio"
    provider = "google"
    url = "https://aistudio.google.com/prompts/new_chat"

    input_selector = 'textarea[aria-label="Enter a prompt"], textarea[placeholder*="prompt" i], textarea'
    send_button_selector = 'button[type="submit"], button[aria-label="Run"]'
    response_container_selector = '[class*="response"], [class*="output"], [class*="message"], .markdown, .prose'

    page_load_wait_s = 6
    response_timeout_s = 90

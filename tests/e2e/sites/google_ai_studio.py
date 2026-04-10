"""Google AI Studio (aistudio.google.com) site adapter."""

from .base import BaseSiteAdapter


class GoogleAIStudioAdapter(BaseSiteAdapter):
    name = "googleaistudio"
    provider = "google"
    url = "https://aistudio.google.com/prompts/new_chat"

    input_selector = 'textarea[aria-label="Enter a prompt"], textarea[placeholder*="prompt" i], textarea'
    send_button_selector = 'button[type="submit"], button[aria-label="Run"]'
    response_container_selector = '[class*="response"], [class*="output"], [class*="message"], .markdown, .prose'
    file_input_selector = 'input[data-test-upload-file-input], input[type="file"].file-input'
    image_input_selector = 'input[data-test-upload-file-input], input[type="file"].file-input'
    upload_reveal_selector = 'button[aria-label="Insert images, videos, audio, or files"]'
    supports_file_upload = True
    supports_image_upload = True

    page_load_wait_s = 6
    response_timeout_s = 90

    def prepare_for_upload(self, driver, kind: str = "file") -> None:
        super().prepare_for_upload(driver, kind=kind)

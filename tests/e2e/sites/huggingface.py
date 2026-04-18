"""HuggingFace Chat (huggingface.co/chat) site adapter.

Covers the Phase 3b batch 4 TypeScript extractor
(`entrypoints/huggingface.content.ts`). HF Chat hosts open-source
models in a clean chat UI with a `[class*="chat-container"]` shell and
turn nodes tagged with `data-message-role` or class keywords.
"""

from .base import BaseSiteAdapter


class HuggingFaceAdapter(BaseSiteAdapter):
    name = "huggingface"
    provider = "huggingface"
    url = "https://huggingface.co/chat/"

    input_selector = (
        'textarea[placeholder*="message" i], '
        'textarea[placeholder*="ask" i], '
        'textarea'
    )
    send_button_selector = (
        'button[type="submit"], '
        'button[aria-label*="send" i], '
        'form button:last-child'
    )
    response_container_selector = (
        '[data-message-role="assistant"], '
        '[class*="chat-message"], '
        '[class*="message"] .prose, '
        '.prose'
    )

    # HF Chat accepts files via the paperclip icon; fall back to paste.
    file_input_selector = 'input[type="file"]'
    image_input_selector = 'input[type="file"][accept*="image"], input[type="file"]'
    supports_file_upload = True
    supports_image_upload = True

    page_load_wait_s = 5
    response_timeout_s = 60

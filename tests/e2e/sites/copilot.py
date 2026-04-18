"""Microsoft Copilot (copilot.microsoft.com) site adapter.

Covers the Phase 3b batch 3 TypeScript extractor
(`entrypoints/copilot.content.ts`). The UI is a React app with CIB
("Conversational AI in Bing") chat turns that carry `source="user"`
or `source="bot"` attributes on the root, plus `.ac-textBlock` / markdown
children.
"""

from .base import BaseSiteAdapter


class CopilotAdapter(BaseSiteAdapter):
    name = "copilot"
    # The WXT extractor (entrypoints/copilot.content.ts) emits
    # provider="microsoft" to align with OpenInference vendor naming.
    # Keep the adapter in lockstep so wait_for_new_captures() and
    # wait_for_session_with_messages() actually see copilot traffic.
    provider = "microsoft"
    url = "https://copilot.microsoft.com/"

    # Input is a contenteditable + textarea depending on variant. We
    # accept either.
    input_selector = (
        'textarea[placeholder*="message" i], '
        'textarea[placeholder*="ask" i], '
        'textarea[data-testid="composer-input"], '
        'div[role="textbox"][contenteditable="true"], '
        'textarea'
    )
    # Copilot's send button has varied testids across versions.
    send_button_selector = (
        'button[data-testid="submit-button"], '
        'button[aria-label*="send" i], '
        'button[aria-label*="提交" i], '
        'button[type="submit"]'
    )
    response_container_selector = (
        '[data-content="ai-message"], '
        '[data-testid*="message"], '
        '[class*="ac-textBlock"], '
        '[class*="conversation-content"] .prose, '
        '[class*="message"]'
    )

    # Copilot supports both file + image via native `input[type="file"]`.
    file_input_selector = 'input[type="file"]'
    image_input_selector = 'input[type="file"][accept*="image"], input[type="file"]'
    supports_file_upload = True
    supports_image_upload = True

    page_load_wait_s = 5
    response_timeout_s = 60

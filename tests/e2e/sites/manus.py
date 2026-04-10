"""Manus (manus.im) site adapter."""

import time

from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver

from .base import BaseSiteAdapter, logger


class ManusAdapter(BaseSiteAdapter):
    name = "manus"
    provider = "manus"
    url = "https://manus.im/app"

    input_selector = 'div.tiptap.ProseMirror[contenteditable="true"]'
    send_button_selector = None
    response_container_selector = '[class*="message"], [class*="task"], [class*="answer"], .prose'

    supports_file_upload = True   # via clipboard paste fallback
    supports_image_upload = True  # via clipboard paste fallback

    page_load_wait_s = 6
    response_timeout_s = 90

    def send_message(self, driver: WebDriver, message: str = None) -> bool:
        msg = message or self.test_message
        try:
            input_el = self._wait_for_element(driver, self.input_selector, timeout=12)
            if not input_el:
                logger.error("[%s] Input element not found", self.name)
                return False

            input_el.click()
            time.sleep(0.3)
            input_el.send_keys(msg)
            time.sleep(0.5)
            input_el.send_keys(Keys.ENTER)

            logger.info("[%s] Message sent: %s", self.name, msg[:50])
            time.sleep(self.post_send_settle_s)
            return True
        except Exception as e:
            logger.error("[%s] Send failed: %s", self.name, e)
            return False

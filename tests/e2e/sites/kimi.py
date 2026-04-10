"""Kimi (kimi.com) site adapter."""

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from .base import BaseSiteAdapter, logger


class KimiAdapter(BaseSiteAdapter):
    name = "kimi"
    provider = "moonshot"
    url = "https://www.kimi.com/"

    input_selector = 'div.chat-input-editor[contenteditable="true"], div[role="textbox"][contenteditable="true"]'
    send_button_selector = None  # Use Enter key — send icon is a non-standard SVG
    response_container_selector = '[class*="assistant"], [class*="bot-message"], [class*="answer"], [class*="markdown"]'

    supports_file_upload = True   # via clipboard paste fallback
    supports_image_upload = True  # via clipboard paste fallback

    page_load_wait_s = 5
    response_timeout_s = 45

    def send_message(self, driver: WebDriver, message: str = None) -> bool:
        msg = message or self.test_message
        try:
            input_el = self._wait_for_element(driver, self.input_selector, timeout=10)
            if not input_el:
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

    def wait_for_response(self, driver: WebDriver) -> bool:
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, self.response_container_selector)
                )
            )
            return self._wait_for_stable_response(driver)
        except Exception as e:
            logger.warning("[%s] Response wait: %s", self.name, e)
            return False

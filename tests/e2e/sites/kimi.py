"""Kimi (kimi.moonshot.cn) site adapter."""

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from .base import BaseSiteAdapter, logger


class KimiAdapter(BaseSiteAdapter):
    name = "kimi"
    provider = "moonshot"
    url = "https://kimi.moonshot.cn/"

    input_selector = 'textarea, [contenteditable="true"], [class*="chat-input"]'
    send_button_selector = 'button[class*="send"], [class*="send-btn"]'
    response_container_selector = '[class*="assistant"], [class*="bot-message"], [class*="answer"], [class*="markdown"]'

    page_load_wait_s = 5
    response_timeout_s = 45

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

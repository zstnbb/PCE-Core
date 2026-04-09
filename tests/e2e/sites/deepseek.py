"""DeepSeek (chat.deepseek.com) site adapter."""

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from .base import BaseSiteAdapter, logger


class DeepSeekAdapter(BaseSiteAdapter):
    name = "deepseek"
    provider = "deepseek"
    url = "https://chat.deepseek.com/"

    input_selector = 'textarea#chat-input, textarea[placeholder*="message" i], textarea'
    send_button_selector = 'button[class*="send"], div[class*="send"]'
    response_container_selector = '[class*="assistant-message"], [class*="markdown-body"], [class*="bot-message"]'

    page_load_wait_s = 4
    response_timeout_s = 60

    def wait_for_response(self, driver: WebDriver) -> bool:
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, self.response_container_selector)
                )
            )
            try:
                WebDriverWait(driver, self.response_timeout_s).until_not(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, '[class*="loading"], [class*="thinking"], [class*="generating"]')
                    )
                )
                time.sleep(1.5)
                return True
            except TimeoutException:
                return self._wait_for_stable_response(driver)
        except Exception as e:
            logger.warning("[%s] Response wait: %s", self.name, e)
            return False

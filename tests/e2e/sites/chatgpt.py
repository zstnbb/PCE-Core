"""ChatGPT (chatgpt.com) site adapter."""

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from .base import BaseSiteAdapter, logger


class ChatGPTAdapter(BaseSiteAdapter):
    name = "chatgpt"
    provider = "openai"
    url = "https://chatgpt.com/"

    input_selector = "#prompt-textarea"
    send_button_selector = 'button[data-testid="send-button"]'
    response_container_selector = '[data-message-author-role="assistant"]'

    page_load_wait_s = 4
    response_timeout_s = 45

    def send_message(self, driver: WebDriver, message: str = None) -> bool:
        """ChatGPT uses a contenteditable div, not a plain textarea."""
        msg = message or self.test_message
        try:
            input_el = self._wait_for_element(driver, self.input_selector, timeout=10)
            if not input_el:
                return False

            input_el.click()
            time.sleep(0.3)

            # ChatGPT's prompt-textarea is contenteditable — use send_keys
            input_el.send_keys(msg)
            time.sleep(0.5)

            # Click send button
            btns = self._find_elements(driver, self.send_button_selector)
            clicked = False
            for btn in btns:
                try:
                    if btn.is_displayed() and btn.is_enabled():
                        btn.click()
                        clicked = True
                        break
                except Exception:
                    pass
            if not clicked:
                input_el.send_keys(Keys.ENTER)

            logger.info("[%s] Message sent: %s", self.name, msg[:50])
            time.sleep(self.post_send_settle_s)
            return True

        except Exception as e:
            logger.error("[%s] Send failed: %s", self.name, e)
            return False

    def wait_for_response(self, driver: WebDriver) -> bool:
        """Wait for streaming to finish — ChatGPT has a stop button during generation."""
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, self.response_container_selector)
                )
            )

            # Wait for the stop button to disappear (= streaming done)
            try:
                WebDriverWait(driver, self.response_timeout_s).until_not(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, 'button[data-testid="stop-button"]')
                    )
                )
                time.sleep(1)
                logger.info("[%s] Response complete (stop button gone)", self.name)
                return True
            except TimeoutException:
                return self._wait_for_stable_response(driver)

        except Exception as e:
            logger.warning("[%s] Response wait failed: %s", self.name, e)
            return False

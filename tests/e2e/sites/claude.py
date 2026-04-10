"""Claude (claude.ai) site adapter."""

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from .base import BaseSiteAdapter, logger


class ClaudeAdapter(BaseSiteAdapter):
    name = "claude"
    provider = "anthropic"
    url = "https://claude.ai/new"

    input_selector = '[contenteditable="true"]'
    send_button_selector = 'button[aria-label="Send Message"]'
    response_container_selector = '[data-is-streaming], .font-claude-message'
    login_wall_selectors = ['button[data-testid="login-button"]', 'a[href*="/login"]']
    file_input_selector = 'input[type="file"]'
    image_input_selector = 'input[type="file"]'
    supports_file_upload = True
    supports_image_upload = True

    page_load_wait_s = 4
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

            btns = self._find_elements(driver, self.send_button_selector)
            clicked = False
            for btn in btns:
                try:
                    if btn.is_displayed():
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
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, '.font-claude-message, [data-is-streaming]')
                )
            )
            try:
                WebDriverWait(driver, self.response_timeout_s).until_not(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, '[data-is-streaming="true"]')
                    )
                )
                time.sleep(1)
                logger.info("[%s] Response complete", self.name)
                return True
            except TimeoutException:
                return self._wait_for_stable_response(driver)
        except Exception as e:
            logger.warning("[%s] Response wait: %s", self.name, e)
            return False

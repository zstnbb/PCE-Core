"""Perplexity (www.perplexity.ai) site adapter."""

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from .base import BaseSiteAdapter, logger


class PerplexityAdapter(BaseSiteAdapter):
    name = "perplexity"
    provider = "perplexity"
    url = "https://www.perplexity.ai/"

    input_selector = 'div[role="textbox"][contenteditable="true"], #ask-input, textarea[placeholder*="ask" i], textarea'
    send_button_selector = 'button[aria-label="提交"], button[aria-label="Submit"], button[class*="submit"], button[type="submit"]'
    response_container_selector = '[class*="answer"], [class*="response"], [class*="prose"], .markdown-body'
    file_input_selector = 'input[type="file"]'
    image_input_selector = 'input[type="file"]'
    upload_reveal_selector = 'button[aria-label*="添加文件"], button[aria-label*="Attach"], button[aria-label*="Add file"]'
    supports_file_upload = True
    supports_image_upload = True

    page_load_wait_s = 3
    response_timeout_s = 30

    def navigate(self, driver: WebDriver) -> bool:
        """Navigate to home and wait for the input to appear (SPA needs extra time)."""
        try:
            driver.get(self.url)
            # Perplexity SPA may redirect; wait for input to appear
            WebDriverWait(driver, 12).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, self.input_selector))
            )
            time.sleep(1)
            logger.info("[%s] Navigated to %s", self.name, self.url)
            return True
        except TimeoutException:
            logger.warning("[%s] Input not found after navigation, retrying...", self.name)
            try:
                driver.get(self.url)
                WebDriverWait(driver, 12).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, self.input_selector))
                )
                time.sleep(1)
                return True
            except Exception:
                logger.error("[%s] Navigation failed after retry", self.name)
                return False
        except Exception as e:
            logger.error("[%s] Navigation failed: %s", self.name, e)
            return False

    def find_input(self, driver: WebDriver) -> bool:
        """Wait up to 8s for the input (SPA rendering delay)."""
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, self.input_selector))
            )
            return True
        except Exception:
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

"""Perplexity (www.perplexity.ai) site adapter."""

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from .base import BaseSiteAdapter, logger


class PerplexityAdapter(BaseSiteAdapter):
    name = "perplexity"
    provider = "perplexity"
    url = "https://www.perplexity.ai/"

    input_selector = '#ask-input, div[role="textbox"][contenteditable="true"], textarea[placeholder*="ask" i], textarea[placeholder*="search" i], textarea'
    send_button_selector = 'button[aria-label="提交"], button[aria-label="Submit"], button[class*="submit"], button[type="submit"]'
    response_container_selector = '[class*="answer"], [class*="response"], [class*="prose"], .markdown-body'

    page_load_wait_s = 4
    response_timeout_s = 30

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

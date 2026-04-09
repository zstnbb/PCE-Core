"""Base site adapter for E2E capture testing (Selenium).

Each AI chat site gets an adapter that knows:
  - How to navigate to a new conversation
  - Where the message input is
  - How to send a message
  - How to detect when the AI has finished responding
  - What provider name PCE uses for this site
"""

import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException,
)

logger = logging.getLogger("pce.e2e.site")

SCREENSHOTS_DIR = Path(__file__).resolve().parent.parent / "screenshots"


@dataclass
class SiteResult:
    """Result of a single site test interaction."""
    site_name: str
    success: bool
    message_sent: bool = False
    response_received: bool = False
    error: Optional[str] = None
    screenshot_path: Optional[str] = None
    elapsed_s: float = 0


class BaseSiteAdapter:
    """Abstract base for AI chat site adapters."""

    # Subclasses MUST override these
    name: str = "unknown"
    provider: str = "unknown"
    url: str = ""

    # CSS selectors — subclasses override as needed
    input_selector: str = "textarea"
    send_button_selector: Optional[str] = None  # None = use Enter key
    response_container_selector: str = '[class*="message"]'

    # Timing
    page_load_wait_s: float = 3
    response_timeout_s: float = 45
    post_send_settle_s: float = 1

    # Test message: short, distinctive, fast response, minimal tokens
    test_message: str = "Reply with exactly one word: PONG"

    def navigate(self, driver: WebDriver) -> bool:
        """Navigate to a fresh conversation. Returns True on success."""
        try:
            driver.get(self.url)
            time.sleep(self.page_load_wait_s)
            logger.info("[%s] Navigated to %s", self.name, self.url)
            return True
        except Exception as e:
            logger.error("[%s] Navigation failed: %s", self.name, e)
            return False

    def find_input(self, driver: WebDriver) -> bool:
        """Check if the message input element exists."""
        try:
            el = driver.find_element(By.CSS_SELECTOR, self.input_selector)
            return el is not None
        except NoSuchElementException:
            return False

    def _wait_for_element(
        self, driver: WebDriver, selector: str, timeout: float = 10
    ) -> Optional[WebElement]:
        """Wait for an element to be visible and return it."""
        try:
            return WebDriverWait(driver, timeout).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, selector))
            )
        except TimeoutException:
            return None

    def _find_elements(self, driver: WebDriver, selector: str) -> list[WebElement]:
        """Find all matching elements (never raises)."""
        try:
            return driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            return []

    def send_message(self, driver: WebDriver, message: str = None) -> bool:
        """Type and send a message. Returns True on success."""
        msg = message or self.test_message
        try:
            input_el = self._wait_for_element(driver, self.input_selector, timeout=10)
            if not input_el:
                logger.error("[%s] Input element not found", self.name)
                return False

            input_el.click()
            time.sleep(0.3)

            # Type the message
            input_el.send_keys(msg)
            time.sleep(0.3)

            # Send: click button or press Enter
            if self.send_button_selector:
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
            else:
                input_el.send_keys(Keys.ENTER)

            logger.info("[%s] Message sent: %s", self.name, msg[:50])
            time.sleep(self.post_send_settle_s)
            return True

        except Exception as e:
            logger.error("[%s] Send failed: %s", self.name, e)
            return False

    def wait_for_response(self, driver: WebDriver) -> bool:
        """Wait for the AI response to complete. Returns True if response detected."""
        try:
            WebDriverWait(driver, self.response_timeout_s).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, self.response_container_selector)
                )
            )
            return self._wait_for_stable_response(driver)
        except TimeoutException:
            logger.warning(
                "[%s] Response timeout after %ss", self.name, self.response_timeout_s
            )
            return False
        except Exception as e:
            logger.error("[%s] Wait error: %s", self.name, e)
            return False

    def _wait_for_stable_response(
        self, driver: WebDriver, stable_s: float = 3.0
    ) -> bool:
        """Wait until the last response element's text stops changing."""
        last_text = ""
        stable_since = time.time()
        deadline = time.time() + self.response_timeout_s

        while time.time() < deadline:
            try:
                elements = self._find_elements(driver, self.response_container_selector)
                if elements:
                    current_text = elements[-1].text
                    if current_text != last_text:
                        last_text = current_text
                        stable_since = time.time()
                    elif (time.time() - stable_since) >= stable_s:
                        logger.info(
                            "[%s] Response stable (%d chars)", self.name, len(last_text)
                        )
                        return True
            except StaleElementReferenceException:
                pass
            time.sleep(0.5)

        return len(last_text) > 0

    def take_screenshot(self, driver: WebDriver, suffix: str = "") -> str:
        """Save a screenshot and return the path."""
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        name = f"{self.name}_{suffix}_{ts}.png" if suffix else f"{self.name}_{ts}.png"
        path = SCREENSHOTS_DIR / name
        try:
            driver.save_screenshot(str(path))
            logger.info("[%s] Screenshot: %s", self.name, path)
        except Exception as e:
            logger.warning("[%s] Screenshot failed: %s", self.name, e)
        return str(path)

    def run_test(self, driver: WebDriver) -> SiteResult:
        """Full test cycle: navigate → send → wait → screenshot."""
        start = time.time()
        result = SiteResult(site_name=self.name, success=False)

        # 1. Navigate
        if not self.navigate(driver):
            result.error = "navigation_failed"
            result.screenshot_path = self.take_screenshot(driver, "nav_fail")
            result.elapsed_s = time.time() - start
            return result

        # 2. Check input exists
        if not self.find_input(driver):
            result.error = "input_not_found"
            result.screenshot_path = self.take_screenshot(driver, "no_input")
            result.elapsed_s = time.time() - start
            return result

        self.take_screenshot(driver, "before_send")

        # 3. Send message
        if not self.send_message(driver):
            result.error = "send_failed"
            result.screenshot_path = self.take_screenshot(driver, "send_fail")
            result.elapsed_s = time.time() - start
            return result
        result.message_sent = True

        # 4. Wait for response
        if self.wait_for_response(driver):
            result.response_received = True

        result.screenshot_path = self.take_screenshot(driver, "after_response")
        result.success = result.message_sent
        result.elapsed_s = time.time() - start
        return result

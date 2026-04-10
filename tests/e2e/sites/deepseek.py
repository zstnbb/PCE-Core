"""DeepSeek (chat.deepseek.com) site adapter."""

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException

from .base import BaseSiteAdapter, logger


class DeepSeekAdapter(BaseSiteAdapter):
    name = "deepseek"
    provider = "deepseek"
    url = "https://chat.deepseek.com/"

    input_selector = 'textarea#chat-input, textarea[placeholder*="message" i], textarea'
    send_button_selector = 'button[class*="send"], div[class*="send"]'
    response_container_selector = '.ds-markdown, [class*="assistant-message"], [class*="markdown-body"], [class*="bot-message"]'
    file_input_selector = 'input[type="file"]'
    image_input_selector = 'input[type="file"]'
    supports_file_upload = True
    supports_image_upload = True

    page_load_wait_s = 4
    response_timeout_s = 60

    login_wall_selectors = ['a[href*="/login"]', 'button[class*="login"]']

    def check_logged_in(self, driver: WebDriver, timeout: float = 10) -> bool:
        """Dismiss cookie banner before checking input availability."""
        try:
            driver.get(self.url)
            time.sleep(min(self.page_load_wait_s, 4))
            self._dismiss_cookie_banner(driver)
        except Exception:
            pass
        return self._check_input_visible(driver, timeout)

    def _check_input_visible(self, driver: WebDriver, timeout: float = 10) -> bool:
        """Check if chat input is visible after cookie banner is dismissed."""
        for sel in self.login_wall_selectors:
            try:
                if driver.find_elements(By.CSS_SELECTOR, sel):
                    logger.info("[%s] Login wall detected (%s)", self.name, sel)
                    return False
            except Exception:
                pass
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, self.input_selector):
                    if el.is_displayed():
                        logger.info("[%s] Logged in — input found", self.name)
                        return True
            except Exception:
                pass
            time.sleep(0.5)
        logger.warning("[%s] Input not found — likely not logged in", self.name)
        return False

    def navigate(self, driver: WebDriver) -> bool:
        if not super().navigate(driver):
            return False
        self._dismiss_cookie_banner(driver)
        return True

    def _dismiss_cookie_banner(self, driver: WebDriver) -> None:
        """DeepSeek's cookie banner can cover the input area on fresh profiles."""
        for text in ("接受全部", "仅接受必要 Cookies"):
            try:
                el = WebDriverWait(driver, 2).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, f"//*[normalize-space()='{text}']")
                    )
                )
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                time.sleep(0.5)
                return
            except Exception:
                continue

    def send_message(self, driver: WebDriver, message: str = None) -> bool:
        """DeepSeek replaces the textarea after upload; refind on stale refs."""
        msg = message or self.test_message
        last_error = None
        for _ in range(3):
            try:
                input_el = self._wait_for_element(driver, self.input_selector, timeout=10)
                if not input_el:
                    logger.error("[%s] Input element not found", self.name)
                    return False
                try:
                    input_el.click()
                except Exception:
                    driver.execute_script("arguments[0].focus();", input_el)
                time.sleep(0.3)
                input_el.send_keys(msg)
                time.sleep(0.3)
                clicked = self._click_send_button(driver, timeout_s=20)
                if not clicked:
                    try:
                        input_el.send_keys(Keys.ENTER)
                    except StaleElementReferenceException:
                        input_el = self._wait_for_element(driver, self.input_selector, timeout=5)
                        if not input_el:
                            raise
                        input_el.send_keys(Keys.ENTER)
                logger.info("[%s] Message sent: %s", self.name, msg[:50])
                time.sleep(self.post_send_settle_s)
                return True
            except StaleElementReferenceException as exc:
                last_error = exc
                time.sleep(0.8)
            except Exception as exc:
                last_error = exc
                time.sleep(0.8)

        logger.error("[%s] Send failed: %s", self.name, last_error)
        return False

    def _click_send_button(self, driver: WebDriver, timeout_s: float = 20) -> bool:
        """Click DeepSeek's SVG up-arrow send button when it becomes enabled."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                clicked = driver.execute_script(
                    """
                    const buttons = Array.from(
                      document.querySelectorAll('div[role="button"].ds-icon-button')
                    );
                    const send = buttons.find((el) => {
                      if (el.getAttribute('aria-disabled') === 'true') return false;
                      const html = el.innerHTML || '';
                      return html.includes('M8.3125') && html.includes('V15.0431');
                    });
                    if (!send) return false;
                    send.click();
                    return true;
                    """
                )
                if clicked:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

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

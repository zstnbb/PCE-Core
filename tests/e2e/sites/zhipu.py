"""ZhiPu / ChatGLM (chat.z.ai) site adapter."""

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException

from .base import BaseSiteAdapter, logger


class ZhiPuAdapter(BaseSiteAdapter):
    name = "zhipu"
    provider = "zhipu"
    url = "https://chat.z.ai/"

    input_selector = 'textarea, [contenteditable="true"], [class*="chat-input"], [class*="input-box"]'
    send_button_selector = 'button[class*="send"], [class*="send-btn"], button[type="submit"]'
    response_container_selector = '[class*="message"][class*="assistant"], [class*="bot-message"], [class*="answer"], [class*="markdown"]'
    file_input_selector = 'input[type="file"]'
    image_input_selector = 'input[type="file"]'
    supports_file_upload = True
    supports_image_upload = True

    page_load_wait_s = 10
    response_timeout_s = 45

    def check_logged_in(self, driver: WebDriver, timeout: float = 12) -> bool:
        """Z.ai needs extra time for redirects and landing-page clicks."""
        try:
            driver.get(self.url)
            time.sleep(min(self.page_load_wait_s, 6))

            # Try clicking "new chat" if we landed on a splash page
            for sel in ['a[href*="new"]', '[class*="new-chat"]']:
                for el in self._find_elements(driver, sel):
                    try:
                        if el.is_displayed():
                            el.click()
                            time.sleep(2)
                            break
                    except Exception:
                        pass

            deadline = time.time() + timeout
            selectors = [self.input_selector, 'textarea', '[contenteditable="true"]']
            while time.time() < deadline:
                for sel in selectors:
                    try:
                        for el in driver.find_elements(By.CSS_SELECTOR, sel):
                            if el.is_displayed():
                                return True
                    except Exception:
                        pass
                time.sleep(0.5)
            return False
        except Exception:
            return False

    def navigate(self, driver: WebDriver) -> bool:
        """Z.ai may redirect or have a landing page; handle it."""
        try:
            driver.get(self.url)
            time.sleep(self.page_load_wait_s)

            # If we landed on a non-chat page, try clicking "new chat"
            for sel in ['a[href*="new"]', '[class*="new-chat"]']:
                els = self._find_elements(driver, sel)
                for el in els:
                    try:
                        if el.is_displayed():
                            el.click()
                            time.sleep(2)
                            break
                    except Exception:
                        pass

            logger.info("[%s] Navigated to %s", self.name, self.url)
            return True
        except Exception as e:
            logger.error("[%s] Navigation failed: %s", self.name, e)
            return False

    def find_input(self, driver: WebDriver) -> bool:
        deadline = time.time() + 18
        selectors = [self.input_selector, 'textarea', '[contenteditable="true"]']
        while time.time() < deadline:
            for sel in selectors:
                try:
                    if driver.find_elements(By.CSS_SELECTOR, sel):
                        return True
                except WebDriverException:
                    pass
            time.sleep(0.5)
        return False

    def send_message(self, driver: WebDriver, message: str = None) -> bool:
        """Z.ai occasionally loads a shell without the chat box on first paint."""
        msg = message or self.test_message
        selectors = ['textarea', self.input_selector, '[contenteditable="true"]']

        for attempt in range(2):
            for sel in selectors:
                try:
                    input_el = None
                    for el in driver.find_elements(By.CSS_SELECTOR, sel):
                        if el.is_displayed():
                            input_el = el
                            break
                    if input_el is None:
                        found = driver.find_elements(By.CSS_SELECTOR, sel)
                        input_el = found[0] if found else None
                    if input_el is None:
                        continue

                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", input_el)
                    try:
                        input_el.click()
                    except Exception:
                        driver.execute_script("arguments[0].focus();", input_el)
                    time.sleep(0.3)

                    try:
                        input_el.send_keys(msg)
                    except Exception:
                        driver.execute_script(
                            """
                            const el = arguments[0];
                            const text = arguments[1];
                            el.focus();
                            if ('value' in el) {
                              el.value = text;
                            } else {
                              el.textContent = text;
                            }
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            """,
                            input_el,
                            msg,
                        )
                    time.sleep(0.5)

                    send_el = None
                    for btn in self._find_elements(driver, self.send_button_selector):
                        try:
                            if btn.is_displayed():
                                send_el = btn
                                break
                        except Exception:
                            continue

                    if send_el and send_el.is_displayed():
                        try:
                            send_el.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", send_el)
                    else:
                        input_el.send_keys("\n")

                    logger.info("[%s] Message sent: %s", self.name, msg[:50])
                    time.sleep(self.post_send_settle_s)
                    return True
                except Exception as e:
                    logger.debug("[%s] send selector %s failed: %s", self.name, sel, e)
                    continue

            if attempt == 0:
                logger.warning("[%s] Input not found on first attempt; reloading page", self.name)
                driver.get(self.url)
                time.sleep(6)

        logger.error("[%s] Input element not found after retry", self.name)
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

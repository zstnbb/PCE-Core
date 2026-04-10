"""Base site adapter for E2E capture testing (Selenium).

Each AI chat site gets an adapter that knows:
  - How to navigate to a new conversation
  - Where the message input is
  - How to send a message
  - How to detect when the AI has finished responding
  - What provider name PCE uses for this site
"""

import base64
import mimetypes
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
    file_input_selector: Optional[str] = None
    image_input_selector: Optional[str] = None
    upload_reveal_selector: Optional[str] = None
    supports_file_upload: bool = False
    supports_image_upload: bool = False

    # Timing
    page_load_wait_s: float = 3
    response_timeout_s: float = 45
    post_send_settle_s: float = 1
    upload_ready_timeout_s: float = 12

    # Test message: short, distinctive, fast response, minimal tokens
    test_message: str = "Reply with exactly one word: PONG"

    # Selectors that indicate the user is NOT logged in (login wall).
    # Subclasses can override to add site-specific login indicators.
    login_wall_selectors: list[str] = []

    def check_logged_in(self, driver: WebDriver, timeout: float = 8) -> bool:
        """Navigate to the site and quickly check if the user is logged in.

        Returns True if the chat input is found within *timeout* seconds,
        meaning the user is authenticated and the site is ready for
        interaction.  Returns False if a login wall is detected or the
        input never appears.
        """
        try:
            driver.get(self.url)
            time.sleep(min(self.page_load_wait_s, 4))

            # Quick check for login wall indicators
            for sel in self.login_wall_selectors:
                try:
                    if driver.find_elements(By.CSS_SELECTOR, sel):
                        logger.info("[%s] Login wall detected (%s)", self.name, sel)
                        return False
                except Exception:
                    pass

            # Wait for the chat input to appear
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    els = driver.find_elements(By.CSS_SELECTOR, self.input_selector)
                    for el in els:
                        try:
                            if el.is_displayed():
                                logger.info("[%s] Logged in — input found", self.name)
                                return True
                        except StaleElementReferenceException:
                            pass
                except Exception:
                    pass
                time.sleep(0.5)

            logger.warning("[%s] Input not found within %ss — likely not logged in", self.name, timeout)
            return False
        except Exception as e:
            logger.error("[%s] check_logged_in error: %s", self.name, e)
            return False

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

    def _upload_selector_for_kind(self, kind: str) -> Optional[str]:
        """Return the best file input selector for a given upload kind."""
        if kind == "image":
            return self.image_input_selector or self.file_input_selector
        return self.file_input_selector or self.image_input_selector

    def prepare_for_upload(self, driver: WebDriver, kind: str = "file") -> None:
        """Open any site-specific upload affordance before locating a file input."""
        if not self.upload_reveal_selector:
            return

        for btn in self._find_elements(driver, self.upload_reveal_selector):
            try:
                if not btn.is_displayed():
                    continue
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});",
                    btn,
                )
                try:
                    btn.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", btn)
                time.sleep(0.8)
                return
            except Exception:
                continue

    def upload_paths(
        self,
        driver: WebDriver,
        paths: list[str],
        *,
        kind: str = "file",
        selector: Optional[str] = None,
    ) -> bool:
        """Upload one or more files through the site's hidden file input."""
        valid_paths = [str(Path(p).resolve()) for p in (paths or []) if p]
        if not valid_paths:
            return True

        selector = selector or self._upload_selector_for_kind(kind)
        if not selector:
            logger.warning("[%s] No upload selector configured for %s", self.name, kind)
            return False

        self.prepare_for_upload(driver, kind=kind)

        try:
            WebDriverWait(driver, self.upload_ready_timeout_s).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )
        except TimeoutException:
            logger.error("[%s] Upload input not found for %s", self.name, kind)
            return False

        input_el = None
        for el in self._find_elements(driver, selector):
            input_el = el
            break
        if input_el is None:
            logger.error("[%s] Upload input not found for %s", self.name, kind)
            return False

        try:
            driver.execute_script(
                """
                const el = arguments[0];
                el.hidden = false;
                el.removeAttribute('hidden');
                el.style.display = 'block';
                el.style.visibility = 'visible';
                el.style.opacity = '1';
                el.style.width = '1px';
                el.style.height = '1px';
                """,
                input_el,
            )
        except Exception:
            pass

        try:
            input_el.send_keys("\n".join(valid_paths))
            try:
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            except Exception:
                pass
            logger.info(
                "[%s] Uploaded %s via %s",
                self.name,
                ", ".join(Path(p).name for p in valid_paths),
                selector,
            )
            time.sleep(1.5)
            return True
        except Exception as e:
            logger.error("[%s] Upload failed for %s: %s", self.name, kind, e)
            return False

    def upload_via_paste(
        self,
        driver: WebDriver,
        paths: list[str],
        *,
        kind: str = "file",
    ) -> bool:
        """Upload files by dispatching a clipboard paste event on the chat input.

        This is a universal fallback that works on virtually all AI chat sites
        since they handle paste events for images and files.  It reads each
        file, encodes it as base64, then uses JavaScript to construct a
        ``ClipboardEvent`` with a ``DataTransfer`` containing the file data and
        dispatches it on the currently focused input element.
        """
        valid_paths = [str(Path(p).resolve()) for p in (paths or []) if p]
        if not valid_paths:
            return True

        # Build a list of {name, mime, b64} dicts for JS
        file_datas: list[dict] = []
        for p in valid_paths:
            path_obj = Path(p)
            if not path_obj.is_file():
                logger.error("[%s] File not found for paste upload: %s", self.name, p)
                return False
            raw = path_obj.read_bytes()
            mime = mimetypes.guess_type(p)[0] or "application/octet-stream"
            file_datas.append({
                "name": path_obj.name,
                "mime": mime,
                "b64": base64.b64encode(raw).decode("ascii"),
            })

        # Focus the chat input first
        try:
            input_el = driver.find_element(By.CSS_SELECTOR, self.input_selector)
            input_el.click()
            time.sleep(0.3)
        except Exception as e:
            logger.warning("[%s] Could not focus input before paste: %s", self.name, e)

        # Dispatch paste event via JS
        js = """
        const fileDatas = arguments[0];
        const targetSel = arguments[1];
        const target = document.querySelector(targetSel)
                     || document.activeElement
                     || document.body;

        const dataTransfer = new DataTransfer();
        for (const fd of fileDatas) {
            const binary = atob(fd.b64);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) {
                bytes[i] = binary.charCodeAt(i);
            }
            const file = new File([bytes], fd.name, { type: fd.mime });
            dataTransfer.items.add(file);
        }

        const event = new ClipboardEvent('paste', {
            clipboardData: dataTransfer,
            bubbles: true,
            cancelable: true,
        });
        target.dispatchEvent(event);
        return true;
        """
        try:
            driver.execute_script(js, file_datas, self.input_selector)
            names = ", ".join(fd["name"] for fd in file_datas)
            logger.info("[%s] Pasted %s via ClipboardEvent (%s)", self.name, names, kind)
            time.sleep(2)
            return True
        except Exception as e:
            logger.error("[%s] Paste upload failed for %s: %s", self.name, kind, e)
            return False

    def send_rich_message(
        self,
        driver: WebDriver,
        *,
        message: str,
        file_paths: Optional[list[str]] = None,
        image_paths: Optional[list[str]] = None,
    ) -> bool:
        """Upload any attachments, then send the composed message.

        Strategy:
          1. If a standard ``input[type="file"]`` selector is configured, use
             the classic ``upload_paths`` flow (send_keys on the hidden input).
          2. Otherwise fall back to ``upload_via_paste`` which dispatches a
             synthetic ``ClipboardEvent`` on the chat input — this works on
             virtually every AI chat site that accepts drag-and-drop / paste.
        """
        all_uploads: list[tuple[list[str], str]] = []  # (paths, kind)
        if image_paths:
            all_uploads.append((list(image_paths), "image"))
        if file_paths:
            all_uploads.append((list(file_paths), "file"))

        for paths, kind in all_uploads:
            selector = self._upload_selector_for_kind(kind)
            if selector:
                # Classic: hidden file input
                if not self.upload_paths(driver, paths, kind=kind, selector=selector):
                    return False
            else:
                # Fallback: clipboard paste
                logger.info("[%s] No file input for %s — using clipboard paste", self.name, kind)
                if not self.upload_via_paste(driver, paths, kind=kind):
                    return False

        return self.send_message(driver, message=message)

    def trigger_manual_capture(self, driver: WebDriver) -> None:
        """Force the page extractor to emit a fresh conversation capture."""
        try:
            driver.execute_script(
                """
                const root = document.documentElement;
                if (root) {
                  root.setAttribute('data-pce-manual-capture', String(Date.now()));
                }
                document.dispatchEvent(
                  new Event('pce-manual-capture', { bubbles: true })
                );
                """
            )
            logger.info("[%s] Triggered manual capture event", self.name)
            time.sleep(1.5)
        except Exception as e:
            logger.warning("[%s] Manual capture trigger failed: %s", self.name, e)

    def send_message(self, driver: WebDriver, message: str = None) -> bool:
        """Type and send a message. Returns True on success."""
        msg = message or self.test_message
        try:
            input_el = self._wait_for_element(driver, self.input_selector, timeout=10)
            if not input_el:
                logger.error("[%s] Input element not found", self.name)
                return False

            try:
                input_el.click()
            except Exception:
                try:
                    driver.execute_script("arguments[0].focus();", input_el)
                except Exception:
                    pass
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

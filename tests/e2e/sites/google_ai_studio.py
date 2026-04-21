# SPDX-License-Identifier: Apache-2.0
"""Google AI Studio (aistudio.google.com) site adapter — extended for autopilot."""

from __future__ import annotations

import re
import time
from typing import Iterable

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .base import BaseSiteAdapter, logger


_PROMPT_RE = re.compile(r"/prompts/([a-zA-Z0-9_\-]+)", re.IGNORECASE)


class GoogleAIStudioAdapter(BaseSiteAdapter):
    name = "googleaistudio"
    provider = "google"
    url = "https://aistudio.google.com/prompts/new_chat"
    base_url = "https://aistudio.google.com"

    input_selector = (
        'textarea[aria-label="Enter a prompt"], '
        'textarea[placeholder*="prompt" i], '
        'textarea'
    )
    send_button_selector = (
        'button[type="submit"], '
        'button[aria-label="Run" i], '
        'button[aria-label*="Send" i]'
    )
    stop_button_selector = (
        'button[aria-label*="Stop" i], '
        'button[aria-label*="Cancel" i]'
    )
    response_container_selector = (
        '[class*="response"], '
        '[class*="output"], '
        '[class*="message"], '
        '.markdown, '
        '.prose, '
        'ms-chat-turn .chat-turn-container.model'
    )
    user_turn_selector = (
        'ms-chat-turn .chat-turn-container.user, '
        '[class*="user-turn"]'
    )
    assistant_turn_selector = (
        'ms-chat-turn .chat-turn-container.model, '
        '[class*="model-turn"]'
    )
    file_input_selector = (
        'input[data-test-upload-file-input], '
        'input[type="file"].file-input, '
        'input[type="file"]'
    )
    image_input_selector = file_input_selector
    upload_reveal_selector = 'button[aria-label="Insert images, videos, audio, or files"]'
    supports_file_upload = True
    supports_image_upload = True

    page_load_wait_s = 6
    response_timeout_s = 90

    # --- Generic helpers (same shape as Claude/Gemini/ChatGPT) -------------

    def _find_elements_safe(
        self,
        root: WebDriver | WebElement,
        by: str,
        selector: str,
    ) -> list[WebElement]:
        try:
            return root.find_elements(by, selector)
        except Exception:
            return []

    def _first_displayed(self, elements: Iterable[WebElement]) -> WebElement | None:
        for el in elements:
            try:
                if el.is_displayed():
                    return el
            except StaleElementReferenceException:
                continue
            except Exception:
                continue
        return None

    def _click_element(self, driver: WebDriver, element: WebElement) -> bool:
        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'center'});",
                element,
            )
        except Exception:
            pass
        try:
            element.click()
            return True
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", element)
                return True
            except Exception:
                return False

    def _click_first(
        self,
        driver: WebDriver,
        selectors: Iterable[str],
        *,
        by: str = By.CSS_SELECTOR,
        root: WebDriver | WebElement | None = None,
    ) -> bool:
        container = root or driver
        for selector in selectors:
            found = self._find_elements_safe(container, by, selector)
            target = self._first_displayed(found)
            if target is not None and self._click_element(driver, target):
                return True
        return False

    def _xpath_literal(self, value: str) -> str:
        if "'" not in value:
            return f"'{value}'"
        if '"' not in value:
            return f'"{value}"'
        parts = value.split("'")
        return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"

    def _xpath_contains_aria(self, labels: Iterable[str]) -> str:
        checks = " or ".join(
            f"contains(@aria-label, {self._xpath_literal(label)})" for label in labels
        )
        return f".//button[{checks}]"

    def _xpath_contains_text(self, labels: Iterable[str]) -> str:
        checks = " or ".join(
            (
                f"contains(normalize-space(.), {self._xpath_literal(label)}) "
                f"or contains(@aria-label, {self._xpath_literal(label)})"
            )
            for label in labels
        )
        return (
            ".//*[(self::button or self::a or @role='menuitem' or self::div)"
            f" and ({checks})]"
        )

    def _hover(self, driver: WebDriver, element: WebElement) -> None:
        try:
            ActionChains(driver).move_to_element(element).perform()
            time.sleep(0.25)
        except Exception:
            pass

    # --- Turn + prompt helpers --------------------------------------------

    def _visible_turns(self, driver: WebDriver, role: str) -> list[WebElement]:
        selector = self.user_turn_selector if role == "user" else self.assistant_turn_selector
        turns: list[WebElement] = []
        for el in self._find_elements(driver, selector):
            try:
                if el.is_displayed():
                    turns.append(el)
            except Exception:
                continue
        return turns

    def last_turn(self, driver: WebDriver, role: str) -> WebElement | None:
        turns = self._visible_turns(driver, role)
        return turns[-1] if turns else None

    def turn_count(self, driver: WebDriver, role: str | None = None) -> int:
        if role == "user":
            return len(self._visible_turns(driver, "user"))
        if role == "assistant":
            return len(self._visible_turns(driver, "assistant"))
        return self.turn_count(driver, role="user") + self.turn_count(driver, role="assistant")

    def wait_for_turn_count(
        self,
        driver: WebDriver,
        previous_count: int,
        *,
        role: str | None = None,
        timeout_s: float = 25,
    ) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.turn_count(driver, role=role) > previous_count:
                return True
            time.sleep(0.5)
        return False

    def current_prompt_id(self, driver: WebDriver) -> str | None:
        match = _PROMPT_RE.search(driver.current_url)
        return match.group(1) if match else None

    def page_contains_any_text(self, driver: WebDriver, needles: Iterable[str]) -> bool:
        text = ""
        try:
            text = driver.find_element(By.TAG_NAME, "body").text or ""
        except Exception:
            try:
                text = driver.page_source or ""
            except Exception:
                text = ""
        lowered = text.lower()
        return any(needle.lower() in lowered for needle in needles)

    def wait_for_page_text(
        self,
        driver: WebDriver,
        needles: Iterable[str],
        *,
        timeout_s: float = 15,
    ) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.page_contains_any_text(driver, needles):
                return True
            time.sleep(0.5)
        return False

    def clear_prompt(self, driver: WebDriver) -> bool:
        input_el = self._wait_for_element(driver, self.input_selector, timeout=10)
        if not input_el:
            return False
        try:
            input_el.click()
        except Exception:
            driver.execute_script("arguments[0].focus();", input_el)
        time.sleep(0.2)
        input_el.send_keys(Keys.CONTROL, "a")
        time.sleep(0.1)
        input_el.send_keys(Keys.DELETE)
        time.sleep(0.2)
        return True

    def prepare_for_upload(self, driver, kind: str = "file") -> None:
        super().prepare_for_upload(driver, kind=kind)

    # --- Core actions ------------------------------------------------------

    def wait_for_stop_button_visible(
        self,
        driver: WebDriver,
        timeout_s: float = 12,
    ) -> bool:
        try:
            WebDriverWait(driver, timeout_s).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, self.stop_button_selector)
                )
            )
            return True
        except TimeoutException:
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
                        (By.CSS_SELECTOR, self.stop_button_selector)
                    )
                )
                time.sleep(1)
                return True
            except TimeoutException:
                return self._wait_for_stable_response(driver)
        except Exception as e:
            logger.warning("[%s] Response wait failed: %s", self.name, e)
            return False

    def navigate_to_new_chat(self, driver: WebDriver) -> bool:
        try:
            driver.get(self.base_url + "/prompts/new_chat")
            time.sleep(self.page_load_wait_s)
            return True
        except Exception:
            return False

    def navigate_to_new_freeform(self, driver: WebDriver) -> bool:
        try:
            driver.get(self.base_url + "/prompts/new_freeform")
            time.sleep(self.page_load_wait_s)
            return True
        except Exception:
            return False

    def navigate_to_new_structured(self, driver: WebDriver) -> bool:
        try:
            driver.get(self.base_url + "/prompts/new_structured")
            time.sleep(self.page_load_wait_s)
            return True
        except Exception:
            return False

    def navigate_to_gallery(self, driver: WebDriver) -> bool:
        try:
            driver.get(self.base_url + "/gallery")
            time.sleep(self.page_load_wait_s)
            return True
        except Exception:
            return False

    def navigate_to_library(self, driver: WebDriver) -> bool:
        try:
            driver.get(self.base_url + "/library")
            time.sleep(self.page_load_wait_s)
            return True
        except Exception:
            return False

    def navigate_to_tune(self, driver: WebDriver) -> bool:
        try:
            driver.get(self.base_url + "/tune")
            time.sleep(self.page_load_wait_s)
            return True
        except Exception:
            return False

    def navigate_to_apikey(self, driver: WebDriver) -> bool:
        try:
            driver.get(self.base_url + "/apikey")
            time.sleep(self.page_load_wait_s)
            return True
        except Exception:
            return False

    def click_regenerate(self, driver: WebDriver) -> bool:
        if self._click_first(
            driver,
            [
                'button[aria-label*="Rerun" i]',
                'button[aria-label*="Regenerate" i]',
                'button[aria-label*="重新" i]',
                'button[mattooltip*="Rerun" i]',
            ],
        ):
            time.sleep(0.8)
            return True
        return False

    def click_edit_last_user_message(self, driver: WebDriver) -> bool:
        turn = self.last_turn(driver, "user")
        if turn is None:
            return False
        self._hover(driver, turn)
        buttons = self._find_elements_safe(
            turn,
            By.XPATH,
            self._xpath_contains_aria(["Edit", "编辑"]),
        )
        target = self._first_displayed(buttons)
        if target is not None and self._click_element(driver, target):
            time.sleep(0.8)
            return True
        return False

    def edit_last_user_message(self, driver: WebDriver, new_message: str) -> bool:
        if not self.click_edit_last_user_message(driver):
            return False
        candidates = self._find_elements(
            driver, '[contenteditable="true"], textarea'
        )
        editor = self._first_displayed(candidates)
        if editor is None:
            return False
        try:
            editor.click()
        except Exception:
            driver.execute_script("arguments[0].focus();", editor)
        time.sleep(0.3)
        editor.send_keys(Keys.CONTROL, "a")
        time.sleep(0.1)
        editor.send_keys(Keys.DELETE)
        time.sleep(0.2)
        editor.send_keys(new_message)
        time.sleep(0.4)

        if self._click_first(
            driver,
            [
                'button[aria-label*="Save" i]',
                'button[aria-label*="Update" i]',
                'button[aria-label="Run" i]',
                'button[type="submit"]',
            ],
        ):
            time.sleep(0.8)
            return True
        try:
            editor.send_keys(Keys.ENTER)
            time.sleep(0.8)
            return True
        except Exception:
            return False

    def switch_model(self, driver: WebDriver, labels: Iterable[str]) -> bool:
        if not self._click_first(
            driver,
            [
                'ms-model-selector button',
                'button[aria-label*="model" i]',
                'button[aria-label*="Model" i]',
            ],
        ):
            return False
        time.sleep(0.5)
        xpath = self._xpath_contains_text(labels)
        found = self._find_elements_safe(driver, By.XPATH, xpath)
        target = self._first_displayed(found)
        if target is not None and self._click_element(driver, target):
            time.sleep(0.8)
            return True
        return False

    def set_system_instructions(self, driver: WebDriver, text: str) -> bool:
        """Fill the 'System instructions' left-rail textbox."""
        input_el = None
        for selector in (
            'textarea[aria-label*="System instructions" i]',
            'textarea[aria-label*="system" i]',
            '[class*="system-instructions"] textarea',
        ):
            els = self._find_elements_safe(driver, By.CSS_SELECTOR, selector)
            input_el = self._first_displayed(els)
            if input_el is not None:
                break
        if input_el is None:
            return False
        try:
            input_el.click()
        except Exception:
            driver.execute_script("arguments[0].focus();", input_el)
        time.sleep(0.2)
        input_el.send_keys(Keys.CONTROL, "a")
        time.sleep(0.1)
        input_el.send_keys(Keys.DELETE)
        time.sleep(0.2)
        input_el.send_keys(text)
        time.sleep(0.3)
        return True

    def toggle_tool(self, driver: WebDriver, tool_labels: Iterable[str]) -> bool:
        """Toggle a developer tool (Grounding / Code exec / URL context / Function calling)."""
        xpath = self._xpath_contains_text(tool_labels)
        found = self._find_elements_safe(driver, By.XPATH, xpath)
        target = self._first_displayed(found)
        if target is None:
            return False
        # Hover the label and click the sibling toggle
        self._hover(driver, target)
        time.sleep(0.3)

        # Look for the sibling checkbox / toggle
        parent_xpath = ".."
        try:
            parent = target.find_element(By.XPATH, parent_xpath)
            toggle = self._first_displayed(
                self._find_elements_safe(
                    parent, By.CSS_SELECTOR, 'input[type="checkbox"], button[role="switch"], mat-slide-toggle'
                )
            )
            if toggle and self._click_element(driver, toggle):
                time.sleep(0.5)
                return True
        except Exception:
            pass

        # Fallback: click the label itself (may toggle if it's the trigger)
        if self._click_element(driver, target):
            time.sleep(0.5)
            return True
        return False

    def click_get_code(self, driver: WebDriver) -> bool:
        """Open the 'Get code' modal (must NOT be captured)."""
        if self._click_first(
            driver,
            [
                'button[aria-label*="Get code" i]',
                'button[aria-label*="View code" i]',
            ],
        ):
            time.sleep(1)
            return True
        return False

    def close_modal(self, driver: WebDriver) -> bool:
        return self._click_first(
            driver,
            [
                'button[aria-label*="Close" i]',
                'button[aria-label*="关闭"]',
                '[role="dialog"] button[aria-label*="Close" i]',
            ],
        )

    def force_error(self, driver: WebDriver, message: str) -> bool:
        if not self.find_input(driver):
            if not self.navigate(driver):
                return False
            if not self.find_input(driver):
                return False
        try:
            driver.execute_cdp_cmd("Network.enable", {})
            driver.execute_cdp_cmd(
                "Network.emulateNetworkConditions",
                {
                    "offline": True,
                    "latency": 0,
                    "downloadThroughput": 0,
                    "uploadThroughput": 0,
                    "connectionType": "none",
                },
            )
            if not self.send_message(driver, message=message):
                return False
            return self.wait_for_error_state(driver, timeout_s=20)
        finally:
            try:
                driver.execute_cdp_cmd(
                    "Network.emulateNetworkConditions",
                    {
                        "offline": False,
                        "latency": 0,
                        "downloadThroughput": -1,
                        "uploadThroughput": -1,
                        "connectionType": "wifi",
                    },
                )
            except Exception:
                pass

    def wait_for_error_state(self, driver: WebDriver, timeout_s: float = 20) -> bool:
        needles = [
            "Something went wrong",
            "An error occurred",
            "failed",
            "offline",
            "出现问题",
            "出错",
            "错误",
        ]
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.page_contains_any_text(driver, needles):
                return True
            time.sleep(0.5)
        return False

    def detect_features(self, driver: WebDriver) -> dict[str, bool]:
        features = {
            "logged_in": False,
            "freeform": False,
            "structured": False,
            "gallery": False,
            "library": False,
            "tune": False,
            "apikey": False,
        }
        try:
            if not self.navigate(driver):
                return features
            features["logged_in"] = self.find_input(driver)
            page_source = (driver.page_source or "").lower()
            features["freeform"] = "new_freeform" in page_source or "freeform" in page_source
            features["structured"] = "new_structured" in page_source or "structured" in page_source
            features["gallery"] = "/gallery" in page_source
            features["library"] = "/library" in page_source
            features["tune"] = "/tune" in page_source
            features["apikey"] = "/apikey" in page_source
        except Exception:
            return features
        return features

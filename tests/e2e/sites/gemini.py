# SPDX-License-Identifier: Apache-2.0
"""Gemini (gemini.google.com) site adapter — extended for autopilot matrix."""

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


_CONVERSATION_RE = re.compile(r"/(?:app|chat)/([a-f0-9]+)", re.IGNORECASE)
_GEM_RE = re.compile(r"/gem/([a-zA-Z0-9_\-]+)", re.IGNORECASE)


class GeminiAdapter(BaseSiteAdapter):
    name = "gemini"
    provider = "google"
    url = "https://gemini.google.com/app"
    base_url = "https://gemini.google.com"

    input_selector = (
        '.ql-editor, '
        '[contenteditable="true"], '
        'rich-textarea .text-input-field'
    )
    send_button_selector = (
        'button[aria-label*="Send" i], '
        'button[aria-label*="发送"], '
        'button[aria-label*="提交"], '
        'button.send-button, '
        'button[data-test-id*="send"], '
        'button[mattooltip*="Send" i], '
        'button[mattooltip*="发送"], '
        'button[mattooltip*="提交"], '
        'button.submit'
    )
    stop_button_selector = (
        'button[aria-label*="Stop" i], '
        'button[mattooltip*="Stop" i], '
        'mat-icon[fonticon="stop"]'
    )
    response_container_selector = (
        '.response-container, '
        '.model-response-text, '
        'message-content, '
        'model-response'
    )
    user_turn_selector = (
        'user-query, '
        '[data-turn-role="user"], '
        'user-query-content, '
        '.query-content, '
        '[class*="user-query"]'
    )
    assistant_turn_selector = (
        'model-response, '
        '[data-turn-role="model"], '
        '[class*="model-response"]'
    )

    supports_file_upload = True   # via clipboard paste fallback
    supports_image_upload = True  # via clipboard paste fallback

    page_load_wait_s = 5
    response_timeout_s = 60

    # --- Generic helpers (same shape as ChatGPT/Claude) --------------------

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
            ".//*[(self::button or self::a or self::div "
            "or @role='menuitem' or @role='button' "
            "or contains(@class, 'chip') or contains(@class, 'suggestion'))"
            f" and ({checks})]"
        )

    def _hover(self, driver: WebDriver, element: WebElement) -> None:
        try:
            ActionChains(driver).move_to_element(element).perform()
            time.sleep(0.25)
        except Exception:
            try:
                driver.execute_script(
                    "arguments[0].dispatchEvent(new MouseEvent('mouseenter', {bubbles:true}));",
                    element,
                )
                time.sleep(0.25)
            except Exception:
                pass

    # --- Turn + conversation helpers ---------------------------------------

    def _visible_turns(self, driver: WebDriver, role: str) -> list[WebElement]:
        if role == "user":
            preferred = self._visible_turns_for_selectors(
                driver,
                [
                    "user-query",
                    '[data-turn-role="user"]',
                    "user-query-content",
                    ".query-content",
                    '[class*="user-query"]',
                ],
            )
            if preferred:
                return preferred
            return []

        selector = self.assistant_turn_selector
        turns: list[WebElement] = []
        for el in self._find_elements(driver, selector):
            try:
                if el.is_displayed() and not self._is_noise_turn_candidate(el):
                    turns.append(el)
            except Exception:
                continue
        return turns

    def _visible_turns_for_selectors(
        self,
        driver: WebDriver,
        selectors: Iterable[str],
    ) -> list[WebElement]:
        for selector in selectors:
            turns: list[WebElement] = []
            for el in self._find_elements_safe(driver, By.CSS_SELECTOR, selector):
                try:
                    if not el.is_displayed() or self._is_noise_turn_candidate(el):
                        continue
                    turns.append(el)
                except Exception:
                    continue
            if turns:
                return turns
        return []

    def _is_noise_turn_candidate(self, el: WebElement) -> bool:
        try:
            cls = (el.get_attribute("class") or "").lower()
            tag = (el.tag_name or "").lower()
            rect = el.rect or {}
            width = float(rect.get("width") or 0)
            height = float(rect.get("height") or 0)
            if "cdk-visually-hidden" in cls or "screen-reader" in cls:
                return True
            if tag in {"span", "mat-icon"} and width <= 2 and height <= 2:
                return True
        except Exception:
            return False
        return False

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

    def current_conversation_id(self, driver: WebDriver) -> str | None:
        match = _CONVERSATION_RE.search(driver.current_url)
        return match.group(1) if match else None

    def current_gem_id(self, driver: WebDriver) -> str | None:
        match = _GEM_RE.search(driver.current_url)
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
        input_el = self._best_input_element(driver, timeout=10)
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

    def _visible_input_elements(self, driver: WebDriver) -> list[WebElement]:
        inputs: list[WebElement] = []
        for el in self._find_elements_safe(driver, By.CSS_SELECTOR, self.input_selector):
            try:
                if not el.is_displayed():
                    continue
                rect = el.rect or {}
                if float(rect.get("width") or 0) < 40 or float(rect.get("height") or 0) < 12:
                    continue
                inputs.append(el)
            except Exception:
                continue
        return inputs

    def _best_input_element(
        self,
        driver: WebDriver,
        *,
        timeout: float = 10,
    ) -> WebElement | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            inputs = self._visible_input_elements(driver)
            if inputs:
                def _sort_key(el: WebElement) -> tuple[float, float]:
                    try:
                        rect = el.rect or {}
                        y = float(rect.get("y") or 0)
                        area = float(rect.get("width") or 0) * float(rect.get("height") or 0)
                        return (y, area)
                    except Exception:
                        return (0.0, 0.0)

                return sorted(inputs, key=_sort_key, reverse=True)[0]
            time.sleep(0.25)
        return None

    def _current_input_text(
        self,
        driver: WebDriver,
        input_el: WebElement | None = None,
    ) -> str:
        element = input_el
        if element is None:
            element = self._best_input_element(driver, timeout=2)
        if not element:
            return ""
        try:
            text = (
                element.get_attribute("innerText")
                or element.get_attribute("textContent")
                or element.get_attribute("value")
                or element.text
                or ""
            )
        except Exception:
            return ""
        return text.strip()

    def _wait_for_send_effect(
        self,
        driver: WebDriver,
        *,
        previous_user_count: int,
        previous_assistant_count: int,
        previous_conversation_id: str | None,
        message: str,
        input_el: WebElement | None = None,
        timeout_s: float = 10,
    ) -> bool:
        expected = (message or "").strip()
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            current_user_count = self.turn_count(driver, role="user")
            current_assistant_count = self.turn_count(driver, role="assistant")
            current_conversation_id = self.current_conversation_id(driver)
            current_input_text = self._current_input_text(driver, input_el)
            if current_user_count > previous_user_count:
                return True
            if current_assistant_count > previous_assistant_count:
                return True
            if (
                current_conversation_id
                and current_conversation_id != previous_conversation_id
                and current_input_text != expected
            ):
                return True
            time.sleep(0.5)
        return False

    # --- Core actions ------------------------------------------------------

    def _click_send_button(self, driver: WebDriver) -> bool:
        selectors = [
            self.send_button_selector,
            'button[aria-label="发送"]',
            'button[aria-label="提交"]',
            'button[class*="send-button"]',
            'button[class*="submit"]',
        ]
        if self._click_first(driver, selectors):
            return True

        xpath = (
            ".//button[.//mat-icon[@fonticon='send' or @data-mat-icon-name='send'] "
            "or contains(@aria-label, '发送') "
            "or contains(@aria-label, '提交') "
            "or contains(@aria-label, 'Send') "
            "or contains(@mattooltip, '发送') "
            "or contains(@mattooltip, '提交') "
            "or contains(@mattooltip, 'Send')]"
        )
        buttons = self._find_elements_safe(driver, By.XPATH, xpath)
        target = self._first_displayed(buttons)
        if target is not None:
            return self._click_element(driver, target)
        return False

    def send_message(self, driver: WebDriver, message: str = None) -> bool:
        msg = message or self.test_message
        try:
            input_el = self._wait_for_element(driver, self.input_selector, timeout=10)
            if not input_el:
                return False

            try:
                input_el.click()
            except Exception:
                driver.execute_script("arguments[0].focus();", input_el)
            time.sleep(0.3)

            input_el.send_keys(msg)
            time.sleep(0.5)

            user_turns_before = self.turn_count(driver, role="user")
            clicked = False
            deadline = time.time() + 20
            while time.time() < deadline and not clicked:
                clicked = self._click_send_button(driver)
                if not clicked:
                    time.sleep(0.5)
            if not clicked:
                input_el.send_keys(Keys.ENTER)
                time.sleep(0.5)

            if not self._wait_for_send_effect(
                driver,
                previous_count=user_turns_before,
                message=msg,
                input_el=input_el,
                timeout_s=8,
            ):
                if self._click_send_button(driver):
                    if not self._wait_for_send_effect(
                        driver,
                        previous_count=user_turns_before,
                        message=msg,
                        input_el=input_el,
                        timeout_s=8,
                    ):
                        return False
                else:
                    return False

            logger.info("[%s] Message sent: %s", self.name, msg[:80])
            time.sleep(self.post_send_settle_s)
            return True

        except Exception as e:
            logger.error("[%s] Send failed: %s", self.name, e)
            return False

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
        """Wait for streaming to finish."""
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
                logger.info("[%s] Response complete (stop button gone)", self.name)
                return True
            except TimeoutException:
                return self._wait_for_stable_response(driver)
        except Exception as e:
            logger.warning("[%s] Response wait failed: %s", self.name, e)
            return False

    def click_new_chat(self, driver: WebDriver) -> bool:
        old_id = self.current_conversation_id(driver)
        if self._click_first(
            driver,
            [
                'button[aria-label*="New chat" i]',
                'button[aria-label*="新聊天"]',
                'button[aria-label*="New conversation" i]',
                'a[href*="/app"] button',
            ],
        ):
            time.sleep(1)
            new_id = self.current_conversation_id(driver)
            if old_id and new_id == old_id:
                time.sleep(1)
            return True

        try:
            driver.get(self.base_url + "/app")
            time.sleep(self.page_load_wait_s)
            return True
        except Exception:
            return False

    def click_edit_last_user_message(self, driver: WebDriver) -> bool:
        turn = self.last_turn(driver, "user")
        if turn is None:
            return False
        self._hover(driver, turn)

        buttons = self._find_elements_safe(
            turn,
            By.XPATH,
            self._xpath_contains_aria(["Edit", "编辑", "編輯", "Modify", "修改"]),
        )
        buttons.extend(
            self._find_elements_safe(
                turn,
                By.CSS_SELECTOR,
                'button[data-test-id="prompt-edit-button"], button[mattooltip*="修改"], button[mattooltip*="edit" i]',
            )
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
            driver, '[contenteditable="true"], textarea, .ql-editor'
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
                'button[aria-label*="Update" i]',
                'button[aria-label*="Save" i]',
                'button[aria-label*="Send" i]',
                'button.send-button',
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

    def accept_upload_consent_if_present(
        self,
        driver: WebDriver,
        *,
        timeout_s: float = 6,
    ) -> bool:
        """Accept Gemini's first-use image/file generation consent dialog."""
        labels = ["Agree", "I agree", "Accept", "同意", "接受"]
        xpath = (
            "//*[(@role='dialog' or contains(@class, 'dialog') or "
            "contains(@class, 'mat-mdc-dialog'))]//button["
            + " or ".join(
                (
                    f"contains(normalize-space(.), {self._xpath_literal(label)}) "
                    f"or contains(@aria-label, {self._xpath_literal(label)})"
                )
                for label in labels
            )
            + "]"
        )
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            buttons = self._find_elements_safe(driver, By.XPATH, xpath)
            target = self._first_displayed(buttons)
            if target is not None and self._click_element(driver, target):
                time.sleep(1.0)
                return True
            time.sleep(0.4)
        return False

    def _visible_body_text(self, driver: WebDriver) -> str:
        try:
            return driver.find_element(By.TAG_NAME, "body").text or ""
        except Exception:
            try:
                return driver.page_source or ""
            except Exception:
                return ""

    def _displayed_dialog_count(self, driver: WebDriver) -> int:
        selectors = [
            '[role="dialog"]',
            '.mat-mdc-dialog-container',
            '.cdk-overlay-pane',
            'iframe[src*="picker"]',
            'iframe[src*="drive"]',
            'iframe[src*="photos"]',
        ]
        count = 0
        for selector in selectors:
            for el in self._find_elements_safe(driver, By.CSS_SELECTOR, selector):
                try:
                    if el.is_displayed():
                        count += 1
                except Exception:
                    continue
        return count

    def _dismiss_overlay(self, driver: WebDriver) -> None:
        try:
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.5)
        except Exception:
            pass

    def dismiss_blocking_dialogs(self, driver: WebDriver) -> bool:
        """Dismiss non-essential Gemini dialogs without enabling account features."""
        labels = [
            "Close",
            "Cancel",
            "Not now",
            "Maybe later",
            "\u5173\u95ed",
            "\u53d6\u6d88",
            "\u7a0d\u540e",
            "\u4e0d\u7528",
        ]
        xpath = (
            "//*[(@role='dialog' or contains(@class, 'dialog') or "
            "contains(@class, 'mat-mdc-dialog'))]//button["
            + " or ".join(
                (
                    f"contains(normalize-space(.), {self._xpath_literal(label)}) "
                    f"or contains(@aria-label, {self._xpath_literal(label)})"
                )
                for label in labels
            )
            + "]"
        )
        clicked_any = False
        deadline = time.time() + 3
        while time.time() < deadline:
            target = self._first_displayed(self._find_elements_safe(driver, By.XPATH, xpath))
            if target is None:
                break
            if not self._click_element(driver, target):
                break
            clicked_any = True
            time.sleep(0.8)
        return clicked_any

    def clear_selected_tool(self, driver: WebDriver) -> bool:
        """Return the composer to normal prompt mode when a tool chip is selected."""
        labels = [
            "Remove",
            "Close",
            "Clear",
            "\u79fb\u9664",
            "\u5173\u95ed",
            "\u6e05\u9664",
        ]
        label_checks = " ".join(
            (
                f"or contains(@aria-label, {self._xpath_literal(label)}) "
                f"or contains(@mattooltip, {self._xpath_literal(label)})"
            )
            for label in labels
        )
        xpath = (
            ".//button["
            ".//mat-icon[normalize-space(.)='close' or @fonticon='close' "
            "or @data-mat-icon-name='close'] "
            "or normalize-space(.)='\u00d7' "
            + label_checks
            + "]"
        )
        buttons = self._find_elements_safe(driver, By.XPATH, xpath)
        small_close_xpath = (
            ".//*[normalize-space(.)='\u00d7' or normalize-space(.)='x' "
            "or contains(normalize-space(.), 'close') "
            "or contains(@fonticon, 'close') "
            "or contains(@data-mat-icon-name, 'close') "
            "or contains(@fonticon, 'cancel') "
            "or contains(@data-mat-icon-name, 'cancel')]"
        )
        candidates = buttons + self._find_elements_safe(driver, By.XPATH, small_close_xpath)
        for button in candidates:
            try:
                if not button.is_displayed():
                    continue
                rect = button.rect or {}
                width = float(rect.get("width") or 0)
                height = float(rect.get("height") or 0)
                if width > 90 or height > 90:
                    continue
                if self._click_element(driver, button):
                    time.sleep(0.8)
                    return True
            except Exception:
                continue
        return False

    def _click_text_entry(
        self,
        driver: WebDriver,
        labels: Iterable[str],
        *,
        timeout_s: float = 6,
    ) -> bool:
        xpath = self._xpath_contains_text(labels)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            candidates = self._find_elements_safe(driver, By.XPATH, xpath)
            for candidate in candidates:
                try:
                    if not candidate.is_displayed():
                        continue
                    click_target = candidate
                    ancestors = self._find_elements_safe(
                        candidate,
                        By.XPATH,
                        (
                            "./ancestor-or-self::*[self::button or self::a "
                            "or @role='menuitem' or @role='button' "
                            "or contains(@class, 'chip') or contains(@class, 'suggestion')][1]"
                        ),
                    )
                    if ancestors:
                        click_target = ancestors[0]
                    if self._click_element(driver, click_target):
                        time.sleep(0.8)
                        return True
                except StaleElementReferenceException:
                    continue
                except Exception:
                    continue
            time.sleep(0.25)
        return False

    def open_attachment_menu(self, driver: WebDriver) -> bool:
        """Open Gemini's "+" attachment menu near the prompt composer."""
        self.dismiss_blocking_dialogs(driver)
        if self._click_first(
            driver,
            [
                "button.upload-card-button",
                'button[class*="upload-card-button"]',
                'button[aria-label*="Add" i]',
                'button[aria-label*="Attach" i]',
                'button[aria-label*="Upload" i]',
                'button[aria-label*="add files" i]',
                'button[aria-label*="\u6253\u5f00\u6587\u4ef6\u4e0a\u4f20\u83dc\u5355"]',
                'button[aria-label*="\u6dfb\u52a0"]',
                'button[aria-label*="\u4e0a\u4f20"]',
                'button[mattooltip*="Add" i]',
                'button[mattooltip*="Attach" i]',
                'button[mattooltip*="\u6dfb\u52a0"]',
            ],
        ):
            time.sleep(0.6)
            return True

        xpath = (
            ".//button[normalize-space(.)='+' "
            "or .//*[normalize-space(.)='+'] "
            "or .//mat-icon[@fonticon='add' or @data-mat-icon-name='add']]"
        )
        target = self._first_displayed(self._find_elements_safe(driver, By.XPATH, xpath))
        if target is not None and self._click_element(driver, target):
            time.sleep(0.6)
            return True
        return False

    def open_tools_menu(self, driver: WebDriver) -> bool:
        """Open Gemini's "Tools" menu near the prompt composer."""
        self.dismiss_blocking_dialogs(driver)
        if self._click_first(
            driver,
            [
                "button.toolbox-drawer-button",
                'button[class*="toolbox-drawer-button"]',
            ],
        ):
            time.sleep(0.6)
            return True

        exact_xpath = (
            ".//button[(normalize-space(.)='Tools' or normalize-space(.)='\u5de5\u5177' "
            "or .//*[normalize-space(.)='Tools' or normalize-space(.)='\u5de5\u5177']) "
            "and not(contains(normalize-space(.), '\u5236\u4f5c\u56fe\u7247')) "
            "and not(contains(normalize-space(.), '\u521b\u4f5c\u89c6\u9891')) "
            "and not(contains(normalize-space(.), '\u521b\u4f5c\u97f3\u4e50'))]"
        )
        target = self._first_displayed(self._find_elements_safe(driver, By.XPATH, exact_xpath))
        if target is not None and self._click_element(driver, target):
            time.sleep(0.6)
            return True

        if self._click_first(
            driver,
            [
                'button[mattooltip*="Tools" i]',
                'button[mattooltip*="\u5de5\u5177"]',
                '[data-test-id="tools-menu-button"]',
                '[data-test-id="tool-menu-button"]',
            ],
        ):
            time.sleep(0.6)
            return True

        xpath = self._xpath_contains_text(["Tools", "\u5de5\u5177"])
        target = self._first_displayed(self._find_elements_safe(driver, By.XPATH, xpath))
        if target is not None and self._click_element(driver, target):
            time.sleep(0.6)
            return True
        return False

    def click_attachment_menu_entry(
        self,
        driver: WebDriver,
        labels: Iterable[str],
    ) -> bool:
        if not self.open_attachment_menu(driver):
            return False
        return self._click_text_entry(driver, labels)

    def click_tool_menu_entry(
        self,
        driver: WebDriver,
        labels: Iterable[str],
    ) -> bool:
        if not self.open_tools_menu(driver):
            return False
        return self._click_text_entry(driver, labels)

    def upload_from_attachment_menu(
        self,
        driver: WebDriver,
        *,
        labels: Iterable[str],
        paths: list[str],
    ) -> bool:
        """Click a concrete "+" menu upload entry, then feed the resulting file input."""
        if not self.click_attachment_menu_entry(driver, labels):
            return False

        deadline = time.time() + self.upload_ready_timeout_s
        while time.time() < deadline:
            inputs = self._find_elements_safe(driver, By.CSS_SELECTOR, 'input[type="file"]')
            for input_el in inputs:
                try:
                    driver.execute_script(
                        "arguments[0].style.display='block';"
                        "arguments[0].style.visibility='visible';"
                        "arguments[0].style.opacity=1;",
                        input_el,
                    )
                    input_el.send_keys("\n".join(paths))
                    time.sleep(2)
                    self.accept_upload_consent_if_present(driver)
                    return True
                except Exception:
                    continue
            time.sleep(0.4)
        # Gemini currently opens a native file picker for these menu entries in
        # some locales. Selenium cannot feed that picker directly, so keep the
        # entry-click evidence and use the existing paste-upload path to attach
        # the same file for capture verification.
        self._dismiss_overlay(driver)
        if self.upload_via_paste(driver, paths, kind="file"):
            self.accept_upload_consent_if_present(driver)
            return True
        return False

    def activate_attachment_entry(
        self,
        driver: WebDriver,
        *,
        labels: Iterable[str],
        evidence_labels: Iterable[str] = (),
        allow_new_tab: bool = False,
    ) -> dict[str, object]:
        """Click a non-upload "+" entry and return menu/dialog evidence."""
        before_handles = set(driver.window_handles)
        clicked = self.click_attachment_menu_entry(driver, labels)
        time.sleep(2)
        after_handles = set(driver.window_handles)
        new_handles = list(after_handles - before_handles)
        new_url = None
        if new_handles and allow_new_tab:
            current = driver.current_window_handle
            try:
                driver.switch_to.window(new_handles[0])
                time.sleep(1)
                new_url = driver.current_url
                driver.close()
            finally:
                driver.switch_to.window(current)

        body_text = self._visible_body_text(driver)
        labels_seen = [
            label for label in evidence_labels
            if label.lower() in body_text.lower()
        ]
        result = {
            "clicked": clicked,
            "dialog_count": self._displayed_dialog_count(driver),
            "labels_seen": labels_seen,
            "current_url": driver.current_url,
            "new_url": new_url,
            "new_tab_opened": bool(new_handles),
        }
        self._dismiss_overlay(driver)
        return result

    def select_tool_entry(
        self,
        driver: WebDriver,
        *,
        labels: Iterable[str],
    ) -> dict[str, object]:
        clicked = self._click_text_entry(driver, labels, timeout_s=2)
        if not clicked:
            clicked = self.click_tool_menu_entry(driver, labels)
        time.sleep(1)
        body_text = self._visible_body_text(driver)
        labels_seen = [
            label for label in labels
            if label.lower() in body_text.lower()
        ]
        return {
            "clicked": clicked,
            "labels_seen": labels_seen,
            "dialog_count": self._displayed_dialog_count(driver),
            "current_url": driver.current_url,
        }

    def inspect_tools_entry(
        self,
        driver: WebDriver,
        *,
        labels: Iterable[str],
    ) -> dict[str, object]:
        opened = self.open_tools_menu(driver)
        time.sleep(0.8)
        body_text = self._visible_body_text(driver)
        labels_seen = [
            label for label in labels
            if label.lower() in body_text.lower()
        ]
        checked = False
        xpath = self._xpath_contains_text(labels)
        for candidate in self._find_elements_safe(driver, By.XPATH, xpath):
            try:
                if not candidate.is_displayed():
                    continue
                attrs = " ".join(
                    filter(
                        None,
                        [
                            candidate.get_attribute("aria-checked"),
                            candidate.get_attribute("aria-selected"),
                            candidate.get_attribute("class"),
                        ],
                    )
                ).lower()
                if "true" in attrs or "selected" in attrs or "checked" in attrs:
                    checked = True
                    break
            except Exception:
                continue
        return {
            "opened": opened,
            "labels_seen": labels_seen,
            "checked_or_selected": checked,
            "dialog_count": self._displayed_dialog_count(driver),
            "current_url": driver.current_url,
        }

    def wait_for_image_generation_complete(
        self,
        driver: WebDriver,
        *,
        timeout_s: float = 420,
    ) -> bool:
        pending_needles = [
            "Creating your image",
            "Creating your images",
            "Generating image",
            "\u6b63\u5728\u521b\u5efa\u56fe\u7247",
            "\u6b63\u5728\u751f\u6210\u56fe\u7247",
            "\u4e3a\u56fe\u7247\u9009\u62e9\u98ce\u683c",
        ]
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            body = self._visible_body_text(driver)
            if any(needle.lower() in body.lower() for needle in pending_needles):
                time.sleep(3)
                continue
            try:
                count = driver.execute_script(
                    """
                    const images = Array.from(document.querySelectorAll('model-response img, img'));
                    return images.filter((img) => {
                      const r = img.getBoundingClientRect();
                      const src = img.currentSrc || img.src || '';
                      if (!src || r.width < 120 || r.height < 120) return false;
                      if (src.startsWith('data:image/svg')) return false;
                      return true;
                    }).length;
                    """
                )
                if int(count or 0) > 0:
                    time.sleep(2)
                    return True
            except Exception:
                pass
            time.sleep(3)
        return False

    def send_rich_message(
        self,
        driver: WebDriver,
        *,
        message: str,
        file_paths: list[str] | None = None,
        image_paths: list[str] | None = None,
    ) -> bool:
        all_uploads: list[tuple[list[str], str]] = []
        if image_paths:
            all_uploads.append((list(image_paths), "image"))
        if file_paths:
            all_uploads.append((list(file_paths), "file"))

        for paths, kind in all_uploads:
            selector = self._upload_selector_for_kind(kind)
            if selector:
                if not self.upload_paths(driver, paths, kind=kind, selector=selector):
                    return False
            else:
                logger.info("[%s] No file input for %s — using clipboard paste", self.name, kind)
                if not self.upload_via_paste(driver, paths, kind=kind):
                    return False
            self.accept_upload_consent_if_present(driver)

        return self.send_message(driver, message=message)

    def click_regenerate(self, driver: WebDriver) -> bool:
        if self._click_first(
            driver,
            [
                'button[data-test-id="regenerate-button"]',
                'button[aria-label*="Regenerate" i]',
                'button[aria-label*="重新生成"]',
                'button[aria-label*="重做"]',
                'button[aria-label*="重试"]',
                'button[aria-label*="Retry" i]',
                'button[data-test-id*="regenerate"]',
                'button[mattooltip*="Regenerate" i]',
                'button[mattooltip*="Retry" i]',
                'button[mattooltip*="重做"]',
                'button[mattooltip*="重试"]',
            ],
        ):
            time.sleep(0.8)
            self._click_regenerate_menu_item_if_present(driver)
            return True

        refresh_xpath = (
            ".//button[.//mat-icon[@fonticon='refresh' "
            "or @data-mat-icon-name='refresh'] "
            "or contains(@aria-label, '重做') "
            "or contains(@aria-label, '重试') "
            "or contains(@aria-label, 'Retry') "
            "or contains(@mattooltip, '重做') "
            "or contains(@mattooltip, '重试') "
            "or contains(@mattooltip, 'Retry')]"
        )
        buttons = self._find_elements_safe(driver, By.XPATH, refresh_xpath)
        target = self._first_displayed(buttons)
        if target is not None and self._click_element(driver, target):
            time.sleep(0.8)
            self._click_regenerate_menu_item_if_present(driver)
            return True

        # Fallback: assistant "more options" dropdown
        turn = self.last_turn(driver, "assistant")
        if turn is None:
            return False
        self._hover(driver, turn)

        # Try "Modify response" or "Show drafts" in Gemini
        more_btn_sel = (
            'button[data-test-id="more-menu-button"], '
            'button[aria-label*="More" i], '
            'button[aria-label*="更多"], '
            'button[aria-label*="显示更多选项"], '
            'button[aria-label*="options" i]'
        )
        if not self._click_first(driver, [more_btn_sel]):
            return False
        time.sleep(0.3)

        xpath = self._xpath_contains_text(
            ["Regenerate", "Retry", "重做", "重试", "重新生成", "Modify response", "Show drafts"]
        )
        found = self._find_elements_safe(driver, By.XPATH, xpath)
        target = self._first_displayed(found)
        if target is not None and self._click_element(driver, target):
            time.sleep(0.8)
            self._click_regenerate_menu_item_if_present(driver)
            return True
        return False

    def _click_regenerate_menu_item_if_present(self, driver: WebDriver) -> bool:
        xpath = self._xpath_contains_text(["Retry", "Regenerate", "重试", "重新生成"])
        deadline = time.time() + 2.5
        while time.time() < deadline:
            found = self._find_elements_safe(driver, By.XPATH, xpath)
            target = self._first_displayed(found)
            if target is not None and self._click_element(driver, target):
                time.sleep(0.8)
                return True
            time.sleep(0.25)
        return False

    def flip_branch(self, driver: WebDriver, direction: str = "next") -> bool:
        """Gemini 'Show drafts' has < 1/2/3 > arrows to flip between drafts."""
        labels = [
            "Next",
            "Next draft",
            "next response",
            "下一个",
            "下一条",
            "下一分支",
            "下一版",
            "下一稿",
            "Draft",
            "草稿",
        ]
        icon_names = ["chevron_right", "keyboard_arrow_right", "arrow_forward_ios"]
        if direction == "prev":
            labels = [
                "Previous",
                "Previous draft",
                "previous response",
                "上一个",
                "上一条",
                "上一分支",
                "上一版",
                "上一稿",
            ]
            icon_names = ["chevron_left", "keyboard_arrow_left", "arrow_back_ios"]
        buttons = self._find_elements_safe(
            driver, By.XPATH, self._xpath_contains_aria(labels)
        )
        target = self._first_displayed(buttons)
        if target is not None and self._click_element(driver, target):
            time.sleep(1)
            return True

        icon_predicate = " or ".join(
            (
                f".//mat-icon[@fonticon={self._xpath_literal(icon)} "
                f"or @data-mat-icon-name={self._xpath_literal(icon)}]"
            )
            for icon in icon_names
        )
        buttons = self._find_elements_safe(
            driver,
            By.XPATH,
            f".//button[{icon_predicate}]",
        )
        target = self._first_displayed(buttons)
        if target is not None and self._click_element(driver, target):
            time.sleep(1)
            return True
        return False

    def navigate_to_settings(self, driver: WebDriver) -> bool:
        try:
            driver.get(self.base_url + "/app/settings")
            time.sleep(self.page_load_wait_s)
            return True
        except Exception:
            return False

    def navigate_to_activity(self, driver: WebDriver) -> bool:
        try:
            driver.get(self.base_url + "/app/activity")
            time.sleep(self.page_load_wait_s)
            return True
        except Exception:
            return False

    def navigate_to_gem(self, driver: WebDriver, gem_url: str | None = None) -> bool:
        try:
            if gem_url:
                driver.get(gem_url)
                time.sleep(self.page_load_wait_s)
                return True

            driver.get(self.base_url + "/gems/view")
            time.sleep(self.page_load_wait_s + 1)
            visible_links = []
            for link in self._find_elements(driver, 'a[href*="/gem/"]'):
                try:
                    if link.is_displayed() and link.get_attribute("href"):
                        visible_links.append(link)
                except Exception:
                    continue
            if not visible_links:
                return False
            preferred_slugs = [
                "/gem/coding-partner",
                "/gem/learning-coach",
                "/gem/writing-editor",
                "/gem/productivity-helper",
            ]
            target = None
            for slug in preferred_slugs:
                for link in visible_links:
                    href = link.get_attribute("href") or ""
                    if slug in href:
                        target = link
                        break
                if target is not None:
                    break
            if target is None:
                target = visible_links[0]
            href = target.get_attribute("href")
            if not href:
                return False
            driver.get(href)
            time.sleep(self.page_load_wait_s)
            return True
        except Exception:
            return False

    def navigate_to_shared(self, driver: WebDriver, share_url: str) -> bool:
        try:
            driver.get(share_url)
            time.sleep(self.page_load_wait_s)
            return "/share/" in driver.current_url.lower()
        except Exception:
            return False

    def create_share_url(self, driver: WebDriver, timeout_s: float = 8) -> str | None:
        deadline = time.time() + timeout_s
        clicked = False
        while time.time() < deadline:
            if not clicked:
                clicked = self._click_first(
                    driver,
                    [
                        'button[data-test-id="share-button"]',
                        'button[aria-label*="Share" i]',
                        'button[aria-label*="分享"]',
                    ],
                )
                if clicked:
                    time.sleep(1.0)

            anchors = self._find_elements_safe(driver, By.CSS_SELECTOR, 'a[href*="/share/"]')
            target = self._first_displayed(anchors)
            if target is not None:
                href = target.get_attribute("href")
                if href:
                    return href

            body = self._visible_body_text(driver)
            match = re.search(r"https://gemini\.google\.com/share/[a-z0-9]+", body, re.IGNORECASE)
            if match:
                return match.group(0)
            match = re.search(r"gemini\.google\.com/share/[a-z0-9]+", body, re.IGNORECASE)
            if match:
                return "https://" + match.group(0).lstrip("/")
            time.sleep(0.5)
        return None

    def switch_model(self, driver: WebDriver, labels: Iterable[str]) -> bool:
        """Switch between Gemini modes such as Fast / Thinking / Pro."""
        if not self._click_first(
            driver,
            [
                'button[data-test-id="bard-mode-menu-button"]',
                'button[aria-label*="模式选择器"]',
                'button[aria-label*="模式"]',
                'button[aria-label*="Mode" i]',
                'button[data-test-id*="model"]',
                'button[aria-label*="Model" i]',
                'button[aria-label*="模型" i]',
                '[class*="model-selector"] button',
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

    def trigger_deep_research(self, driver: WebDriver) -> bool:
        """Toggle the Deep Research mode before sending a prompt."""
        return self._click_first(
            driver,
            [
                'button[aria-label*="Deep Research" i]',
                'button[aria-label*="深度研究"]',
                '[data-test-id*="deep-research"]',
            ],
        )

    def trigger_canvas(self, driver: WebDriver) -> bool:
        """Toggle Canvas mode before sending a prompt."""
        return self._click_first(
            driver,
            [
                'button[aria-label*="Canvas" i]',
                'button[aria-label*="画布" i]',
                '[data-test-id*="canvas"]',
            ],
        )

    def force_error(self, driver: WebDriver, message: str) -> bool:
        if not self.find_input(driver):
            if not self.navigate(driver):
                return False
            if not self.find_input(driver):
                return False

        try:
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
            input_el.send_keys(message)
            time.sleep(0.5)

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
            if not self._click_send_button(driver):
                input_el.send_keys(Keys.ENTER)
                time.sleep(0.5)
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
            "Try again",
            "Retry",
            "offline",
            "failed",
            "出了点问题",
            "出了一点问题",
            "出现问题",
            "出错",
            "错误",
            "重试",
        ]
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.page_contains_any_text(driver, needles):
                return True
            time.sleep(0.5)
        return False

    def _send_button_is_clickable(self, element: WebElement) -> bool:
        try:
            if not element.is_displayed() or not element.is_enabled():
                return False
            if element.get_attribute("disabled") is not None:
                return False
            if (element.get_attribute("aria-disabled") or "").strip().lower() == "true":
                return False
            return True
        except Exception:
            return False

    def _click_send_button(self, driver: WebDriver) -> bool:
        ranked: list[tuple[float, float, WebElement]] = []
        seen_ids: set[str] = set()

        def _add_candidate(element: WebElement) -> None:
            if not self._send_button_is_clickable(element):
                return
            element_id = getattr(element, "id", None) or str(id(element))
            if element_id in seen_ids:
                return
            seen_ids.add(element_id)
            try:
                rect = element.rect or {}
                y = float(rect.get("y") or 0)
                area = float(rect.get("width") or 0) * float(rect.get("height") or 0)
            except Exception:
                y = 0.0
                area = 0.0
            ranked.append((y, area, element))

        selectors = [
            self.send_button_selector,
            'button[aria-label="发送"]',
            'button[aria-label="提交"]',
            'button[class*="send-button"]',
            'button[class*="submit"]',
        ]
        for selector in selectors:
            for candidate in self._find_elements_safe(driver, By.CSS_SELECTOR, selector):
                _add_candidate(candidate)

        xpath = (
            ".//button[.//mat-icon[@fonticon='send' or @data-mat-icon-name='send'] "
            "or contains(@aria-label, '发送') "
            "or contains(@aria-label, '提交') "
            "or contains(@aria-label, 'Send') "
            "or contains(@mattooltip, '发送') "
            "or contains(@mattooltip, '提交') "
            "or contains(@mattooltip, 'Send')]"
        )
        for target in self._find_elements_safe(driver, By.XPATH, xpath):
            _add_candidate(target)

        for _, _, target in sorted(ranked, key=lambda item: (item[0], item[1]), reverse=True):
            if self._click_element(driver, target):
                return True
        return False

    def send_message(self, driver: WebDriver, message: str = None) -> bool:
        msg = message or self.test_message
        try:
            input_el = self._best_input_element(driver, timeout=10)
            if not input_el:
                return False

            try:
                input_el.click()
            except Exception:
                driver.execute_script("arguments[0].focus();", input_el)
            time.sleep(0.3)

            input_el.send_keys(msg)
            time.sleep(0.5)

            user_turns_before = self.turn_count(driver, role="user")
            assistant_turns_before = self.turn_count(driver, role="assistant")
            conversation_id_before = self.current_conversation_id(driver)

            clicked = False
            deadline = time.time() + 20
            while time.time() < deadline and not clicked:
                clicked = self._click_send_button(driver)
                if not clicked:
                    time.sleep(0.5)
            if not clicked:
                input_el.send_keys(Keys.ENTER)
                time.sleep(0.5)

            if not self._wait_for_send_effect(
                driver,
                previous_user_count=user_turns_before,
                previous_assistant_count=assistant_turns_before,
                previous_conversation_id=conversation_id_before,
                message=msg,
                input_el=input_el,
                timeout_s=10,
            ):
                if self._click_send_button(driver):
                    if not self._wait_for_send_effect(
                        driver,
                        previous_user_count=user_turns_before,
                        previous_assistant_count=assistant_turns_before,
                        previous_conversation_id=conversation_id_before,
                        message=msg,
                        input_el=input_el,
                        timeout_s=10,
                    ):
                        return False
                else:
                    return False

            logger.info("[%s] Message sent: %s", self.name, msg[:80])
            time.sleep(self.post_send_settle_s)
            return True

        except Exception as e:
            logger.error("[%s] Send failed: %s", self.name, e)
            return False

    def _has_visible_stop_button(self, driver: WebDriver) -> bool:
        selectors = [
            'button[aria-label*="Stop" i]',
            'button[mattooltip*="Stop" i]',
        ]
        for selector in selectors:
            if self._first_displayed(self._find_elements_safe(driver, By.CSS_SELECTOR, selector)):
                return True
        xpath = (
            ".//button[.//mat-icon[@fonticon='stop' "
            "or @data-mat-icon-name='stop']]"
        )
        return self._first_displayed(self._find_elements_safe(driver, By.XPATH, xpath)) is not None

    def wait_for_stop_button_visible(
        self,
        driver: WebDriver,
        timeout_s: float = 12,
    ) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._has_visible_stop_button(driver):
                return True
            time.sleep(0.25)
        return False

    def last_turn_text(self, driver: WebDriver, role: str) -> str:
        turn = self.last_turn(driver, role)
        if turn is None:
            return ""
        try:
            text = (
                turn.get_attribute("innerText")
                or turn.get_attribute("textContent")
                or turn.text
                or ""
            )
        except Exception:
            return ""
        return text.strip()

    def wait_for_response(
        self,
        driver: WebDriver,
        *,
        previous_assistant_count: int | None = None,
        previous_assistant_text: str | None = None,
    ) -> bool:
        """Wait until Gemini has produced fresh assistant content."""
        deadline = time.time() + self.response_timeout_s
        last_text = previous_assistant_text or ""
        stable_since: float | None = None
        response_started = False

        while time.time() < deadline:
            current_count = self.turn_count(driver, role="assistant")
            current_text = self.last_turn_text(driver, "assistant")
            stop_visible = self._has_visible_stop_button(driver)

            if previous_assistant_count is None and current_text:
                response_started = True
            elif previous_assistant_count is not None and current_count > previous_assistant_count:
                response_started = True
            elif previous_assistant_text is not None and current_text and current_text != previous_assistant_text:
                response_started = True

            if response_started:
                if current_text != last_text or stop_visible:
                    last_text = current_text
                    stable_since = time.time()
                elif stable_since is None:
                    stable_since = time.time()
                elif not stop_visible and (time.time() - stable_since) >= 2.0:
                    logger.info("[%s] Response complete (fresh assistant content)", self.name)
                    return True

            time.sleep(0.5)

        return response_started and bool(last_text.strip())

    def _click_text_entry(
        self,
        driver: WebDriver,
        labels: Iterable[str],
        *,
        timeout_s: float = 6,
    ) -> bool:
        xpath = self._xpath_contains_text(labels)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            candidates = self._find_elements_safe(driver, By.XPATH, xpath)
            ranked_targets: list[tuple[int, float, WebElement]] = []
            seen_ids: set[str] = set()
            for candidate in candidates:
                try:
                    if not candidate.is_displayed():
                        continue
                    click_target = candidate
                    ancestors = self._find_elements_safe(
                        candidate,
                        By.XPATH,
                        (
                            "./ancestor-or-self::*[self::button or self::a "
                            "or @role='menuitem' or @role='button' "
                            "or contains(@class, 'chip') or contains(@class, 'suggestion')][1]"
                        ),
                    )
                    if ancestors:
                        click_target = ancestors[0]
                    element_id = getattr(click_target, "id", None) or str(id(click_target))
                    if element_id in seen_ids:
                        continue
                    seen_ids.add(element_id)
                    rect = click_target.rect or {}
                    y = float(rect.get("y") or 0)
                    text_blob = " ".join(
                        part
                        for part in [
                            (candidate.text or "").strip(),
                            (candidate.get_attribute("aria-label") or "").strip(),
                            (candidate.get_attribute("mattooltip") or "").strip(),
                        ]
                        if part
                    )
                    exact_match = any(label.lower() == text_blob.lower() for label in labels if label)
                    ranked_targets.append((1 if exact_match else 0, y, click_target))
                except StaleElementReferenceException:
                    continue
                except Exception:
                    continue
            for _, _, click_target in sorted(ranked_targets, key=lambda item: (item[0], item[1]), reverse=True):
                if self._click_element(driver, click_target):
                    time.sleep(0.8)
                    return True
            time.sleep(0.25)
        return False

    def select_tool_entry(
        self,
        driver: WebDriver,
        *,
        labels: Iterable[str],
    ) -> dict[str, object]:
        clicked = self.click_tool_menu_entry(driver, labels)
        time.sleep(1)
        body_text = self._visible_body_text(driver)
        labels_seen = [
            label for label in labels
            if label.lower() in body_text.lower()
        ]
        return {
            "clicked": clicked,
            "labels_seen": labels_seen,
            "dialog_count": self._displayed_dialog_count(driver),
            "current_url": driver.current_url,
        }

    def force_error(self, driver: WebDriver, message: str) -> bool:
        if not self.find_input(driver):
            if not self.navigate(driver):
                return False
            if not self.find_input(driver):
                return False

        try:
            input_el = self._best_input_element(driver, timeout=10)
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
            input_el.send_keys(message)
            time.sleep(0.5)

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
            if not self._click_send_button(driver):
                input_el.send_keys(Keys.ENTER)
                time.sleep(0.5)
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

    def click_edit_last_user_message(self, driver: WebDriver) -> bool:
        turn = self.last_turn(driver, "user")
        if turn is None:
            fallback = self._visible_turns_for_selectors(
                driver,
                ["user-query", "user-query-content", ".query-content"],
            )
            turn = fallback[-1] if fallback else None
        if turn is None:
            return False

        self._hover(driver, turn)
        try:
            turn_y = float((turn.rect or {}).get("y") or 0)
        except Exception:
            turn_y = 0.0

        candidates: list[tuple[float, WebElement]] = []
        selectors = [
            'button[data-test-id="prompt-edit-button"]',
            'button[aria-label*="修改"]',
            'button[mattooltip*="修改"]',
            'button[aria-label*="Edit" i]',
            'button[mattooltip*="edit" i]',
        ]
        for selector in selectors:
            for button in self._find_elements_safe(driver, By.CSS_SELECTOR, selector):
                try:
                    if not button.is_displayed():
                        continue
                    button_y = float((button.rect or {}).get("y") or 0)
                    candidates.append((abs(button_y - turn_y), button))
                except Exception:
                    continue

        xpath = self._xpath_contains_aria(["Edit", "编辑", "編輯", "Modify", "修改"])
        for button in self._find_elements_safe(driver, By.XPATH, xpath):
            try:
                if not button.is_displayed():
                    continue
                button_y = float((button.rect or {}).get("y") or 0)
                candidates.append((abs(button_y - turn_y), button))
            except Exception:
                continue

        for _, button in sorted(candidates, key=lambda item: item[0]):
            if self._click_element(driver, button):
                time.sleep(0.8)
                return True
        return False

    def edit_last_user_message(self, driver: WebDriver, new_message: str) -> bool:
        if not self.click_edit_last_user_message(driver):
            return False

        editor: WebElement | None = None
        try:
            active = driver.switch_to.active_element
            if active and active.is_displayed():
                tag = (active.tag_name or "").lower()
                if tag == "textarea" or active.get_attribute("contenteditable") == "true":
                    editor = active
        except Exception:
            editor = None

        if editor is None:
            candidates = self._visible_input_elements(driver)
            if candidates:
                editor = sorted(
                    candidates,
                    key=lambda el: float((el.rect or {}).get("y") or 0),
                )[0]
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

        selectors = [
            'button[aria-label*="Update" i]',
            'button[aria-label*="Save" i]',
            'button[aria-label*="提交"]',
            'button[aria-label*="发送"]',
            'button[class*="send-button"]',
        ]
        for selector in selectors:
            for button in self._find_elements_safe(driver, By.CSS_SELECTOR, selector):
                try:
                    if not self._send_button_is_clickable(button):
                        continue
                    if self._click_element(driver, button):
                        time.sleep(0.8)
                        return True
                except Exception:
                    continue

        try:
            editor.send_keys(Keys.ENTER)
            time.sleep(0.8)
            return True
        except Exception:
            return False

    def navigate_to_gem(self, driver: WebDriver, gem_url: str | None = None) -> bool:
        preferred_urls = []
        if gem_url:
            preferred_urls.append(gem_url)
        preferred_urls.extend(
            [
                self.base_url + "/gem/coding-partner",
                self.base_url + "/gem/learning-coach",
                self.base_url + "/gem/writing-editor",
                self.base_url + "/gem/productivity-helper",
            ]
        )

        for candidate_url in preferred_urls:
            try:
                driver.get(candidate_url)
                time.sleep(self.page_load_wait_s + 1)
                body = self._visible_body_text(driver)
                if self.find_input(driver) or self.page_contains_any_text(
                    driver,
                    ["创建者", "近期对话", "Gem", "Gems", "编码助手", "学习教练"],
                ):
                    return True
            except Exception:
                continue

        try:
            driver.get(self.base_url + "/gems/view")
            time.sleep(self.page_load_wait_s + 1)
            for link in self._find_elements(driver, 'a[href*="/gem/"]'):
                try:
                    href = link.get_attribute("href") or ""
                    if not href:
                        continue
                    driver.get(href)
                    time.sleep(self.page_load_wait_s)
                    if self.find_input(driver) or "/gem/" in driver.current_url:
                        return True
                except Exception:
                    continue
        except Exception:
            return False
        return False

    def detect_features(self, driver: WebDriver) -> dict[str, bool]:
        features = {
            "logged_in": False,
            "settings": False,
            "gems": False,
            "canvas": False,
            "deep_research": False,
            "imagen": False,
            "extensions": False,
        }
        try:
            if not self.navigate(driver):
                return features
            features["logged_in"] = self.find_input(driver)
            page_source = (driver.page_source or "").lower()
            features["settings"] = (
                "/settings" in page_source
                or self.page_contains_any_text(driver, ["Settings", "设置"])
            )
            features["gems"] = (
                "/gem" in page_source
                or self.page_contains_any_text(driver, ["Gems", "Gem"])
            )
            features["canvas"] = self.page_contains_any_text(
                driver, ["Canvas", "画布"]
            )
            features["deep_research"] = self.page_contains_any_text(
                driver, ["Deep Research", "深度研究"]
            )
            features["imagen"] = self.page_contains_any_text(
                driver, ["Imagen", "image", "图片"]
            )
            features["extensions"] = self.page_contains_any_text(
                driver, ["@Gmail", "@Drive", "@Docs", "@YouTube"]
            )
        except Exception:
            return features
        return features

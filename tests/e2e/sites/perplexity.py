# SPDX-License-Identifier: Apache-2.0
"""Perplexity (www.perplexity.ai) site adapter for full E2E coverage."""

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


_THREAD_RE = re.compile(r"/(?:search|thread)/([a-zA-Z0-9_-]+)")
_SPACE_THREAD_RE = re.compile(
    r"/(?:space|spaces)/([a-zA-Z0-9_-]+)/(?:search|thread)/([a-zA-Z0-9_-]+)"
)


class PerplexityAdapter(BaseSiteAdapter):
    name = "perplexity"
    provider = "perplexity"
    url = "https://www.perplexity.ai/"
    base_url = "https://www.perplexity.ai"

    input_selector = (
        'div[role="textbox"][contenteditable="true"], '
        '[contenteditable="true"][data-lexical-editor="true"], '
        '#ask-input, '
        'textarea[placeholder*="ask" i], '
        "textarea"
    )
    send_button_selector = (
        'button[aria-label*="Submit" i], '
        'button[aria-label*="Send" i], '
        'button[type="submit"], '
        'button[data-testid*="submit" i], '
        'button[class*="submit" i]'
    )
    stop_button_selector = (
        'button[aria-label*="Stop" i], '
        'button[aria-label*="Pause" i], '
        'button[aria-label*="Cancel" i], '
        'button[data-testid*="stop" i]'
    )
    response_container_selector = (
        '[class*="answer" i], '
        '[class*="response" i], '
        '[class*="prose" i], '
        '[data-testid*="answer" i], '
        ".markdown-body"
    )
    user_turn_selector = (
        '[class*="query" i], '
        '[class*="question" i], '
        '[data-testid*="query" i]'
    )
    assistant_turn_selector = response_container_selector
    file_input_selector = 'input[type="file"]'
    image_input_selector = 'input[type="file"]'
    upload_reveal_selector = (
        'button[aria-label*="Attach" i], '
        'button[aria-label*="Add file" i], '
        'button[aria-label*="Upload" i], '
        'button[data-testid*="attach" i], '
        'button[data-testid*="upload" i]'
    )
    supports_file_upload = True
    supports_image_upload = True

    page_load_wait_s = 3
    response_timeout_s = 75
    post_send_settle_s = 1

    # --- Generic DOM helpers ---------------------------------------------

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

    def _xpath_literal(self, value: str) -> str:
        if "'" not in value:
            return f"'{value}'"
        if '"' not in value:
            return f'"{value}"'
        parts = value.split("'")
        return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"

    def _xpath_contains_text(self, labels: Iterable[str]) -> str:
        checks = " or ".join(
            (
                f"contains(translate(normalize-space(.), "
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
                f"{self._xpath_literal(label.lower())}) "
                f"or contains(translate(@aria-label, "
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
                f"{self._xpath_literal(label.lower())})"
            )
            for label in labels
            if label
        )
        return (
            ".//*[(self::button or self::a or @role='button' or @role='menuitem' "
            "or contains(@class, 'chip') or contains(@class, 'option') "
            "or contains(@class, 'item'))"
            f" and ({checks})]"
        )

    def _click_text_entry(
        self,
        driver: WebDriver,
        labels: Iterable[str],
        *,
        timeout_s: float = 6,
    ) -> bool:
        labels = [label for label in labels if label]
        if not labels:
            return False
        xpath = self._xpath_contains_text(labels)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            candidates = self._find_elements_safe(driver, By.XPATH, xpath)
            ranked: list[tuple[int, float, WebElement]] = []
            seen: set[str] = set()
            for candidate in candidates:
                try:
                    if not candidate.is_displayed():
                        continue
                    text_blob = " ".join(
                        part
                        for part in [
                            candidate.text or "",
                            candidate.get_attribute("aria-label") or "",
                            candidate.get_attribute("title") or "",
                        ]
                        if part
                    ).strip()
                    exact = any(text_blob.lower() == label.lower() for label in labels)
                    rect = candidate.rect or {}
                    y = float(rect.get("y") or 0)
                    key = candidate.id
                    if key in seen:
                        continue
                    seen.add(key)
                    ranked.append((1 if exact else 0, y, candidate))
                except StaleElementReferenceException:
                    continue
                except Exception:
                    continue
            for _, _, target in sorted(ranked, key=lambda item: (item[0], item[1]), reverse=True):
                if self._click_element(driver, target):
                    time.sleep(0.8)
                    return True
            time.sleep(0.25)
        return False

    def _visible_body_text(self, driver: WebDriver) -> str:
        try:
            return driver.find_element(By.TAG_NAME, "body").text or ""
        except Exception:
            try:
                return driver.page_source or ""
            except Exception:
                return ""

    def page_contains_any_text(self, driver: WebDriver, needles: Iterable[str]) -> bool:
        body = self._visible_body_text(driver).lower()
        return any(needle.lower() in body for needle in needles if needle)

    def is_security_challenge(self, driver: WebDriver) -> bool:
        body = self._visible_body_text(driver).lower()
        return any(
            needle in body
            for needle in (
                "cloudflare",
                "checking your browser",
                "verify you are human",
                "security verification",
                "正在进行安全验证",
                "验证您不是自动程序",
                "防护恶意自动程序",
            )
        )

    def wait_for_page_text(
        self,
        driver: WebDriver,
        needles: Iterable[str],
        *,
        timeout_s: float = 12,
    ) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.page_contains_any_text(driver, needles):
                return True
            time.sleep(0.5)
        return False

    def _visible_inputs(self, driver: WebDriver) -> list[WebElement]:
        inputs = []
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

    def _best_input_element(self, driver: WebDriver, *, timeout: float = 10) -> WebElement | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            inputs = self._visible_inputs(driver)
            if inputs:
                return sorted(
                    inputs,
                    key=lambda el: float((el.rect or {}).get("y") or 0),
                    reverse=True,
                )[0]
            time.sleep(0.25)
        return None

    def _current_input_text(self, input_el: WebElement | None) -> str:
        if not input_el:
            return ""
        try:
            return (
                input_el.get_attribute("innerText")
                or input_el.get_attribute("textContent")
                or input_el.get_attribute("value")
                or input_el.text
                or ""
            ).strip()
        except Exception:
            return ""

    def _hover(self, driver: WebDriver, element: WebElement) -> None:
        try:
            ActionChains(driver).move_to_element(element).perform()
            time.sleep(0.2)
        except Exception:
            pass

    # --- Thread helpers ----------------------------------------------------

    def current_thread_id(self, driver: WebDriver) -> str | None:
        url = driver.current_url or ""
        space = _SPACE_THREAD_RE.search(url)
        if space:
            return f"space:{space.group(1)}:{space.group(2)}"
        match = _THREAD_RE.search(url)
        return match.group(1) if match else None

    def _is_noise_turn(self, el: WebElement) -> bool:
        try:
            text = (el.text or "").strip()
            cls = (el.get_attribute("class") or "").lower()
            rect = el.rect or {}
            if not text or len(text) < 2:
                return True
            if "citation" in cls or "source" in cls or "related" in cls:
                return True
            if float(rect.get("width") or 0) < 20 or float(rect.get("height") or 0) < 8:
                return True
        except Exception:
            return True
        return False

    def _visible_turns(self, driver: WebDriver, role: str) -> list[WebElement]:
        selector = self.user_turn_selector if role == "user" else self.assistant_turn_selector
        turns: list[WebElement] = []
        for el in self._find_elements_safe(driver, By.CSS_SELECTOR, selector):
            try:
                if el.is_displayed() and not self._is_noise_turn(el):
                    turns.append(el)
            except Exception:
                continue
        return turns

    def turn_count(self, driver: WebDriver, role: str | None = None) -> int:
        if role == "user":
            return len(self._visible_turns(driver, "user"))
        if role == "assistant":
            return len(self._visible_turns(driver, "assistant"))
        return self.turn_count(driver, "user") + self.turn_count(driver, "assistant")

    def last_turn_text(self, driver: WebDriver, role: str) -> str:
        turns = self._visible_turns(driver, role)
        if not turns:
            return ""
        try:
            return (turns[-1].text or "").strip()
        except Exception:
            return ""

    def _has_visible_stop_button(self, driver: WebDriver) -> bool:
        if self._first_displayed(self._find_elements_safe(driver, By.CSS_SELECTOR, self.stop_button_selector)):
            return True
        body = self._visible_body_text(driver).lower()
        return "stop generating" in body or "pause" in body or "cancel" in body

    def wait_for_stop_button_visible(self, driver: WebDriver, timeout_s: float = 10) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._first_displayed(self._find_elements_safe(driver, By.CSS_SELECTOR, self.stop_button_selector)):
                return True
            body = self._visible_body_text(driver).lower()
            if "stop generating" in body or "cancel" in body:
                return True
            time.sleep(0.25)
        return False

    # --- Core actions ------------------------------------------------------

    def navigate(self, driver: WebDriver) -> bool:
        try:
            driver.get(self.url)
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, self.input_selector))
            )
            time.sleep(1)
            logger.info("[%s] Navigated to %s", self.name, self.url)
            return True
        except TimeoutException:
            if self.is_security_challenge(driver):
                logger.warning("[%s] Perplexity security challenge after navigation", self.name)
                return False
            logger.warning("[%s] Input not found after navigation", self.name)
            return False
        except Exception as exc:
            logger.error("[%s] Navigation failed: %s", self.name, exc)
            return False

    def find_input(self, driver: WebDriver) -> bool:
        return self._best_input_element(driver, timeout=5) is not None

    def _click_send_button(self, driver: WebDriver) -> bool:
        candidates = []
        for el in self._find_elements_safe(driver, By.CSS_SELECTOR, self.send_button_selector):
            try:
                if el.is_displayed() and el.is_enabled():
                    y = float((el.rect or {}).get("y") or 0)
                    candidates.append((y, el))
            except Exception:
                continue
        for _, el in sorted(candidates, key=lambda item: item[0], reverse=True):
            if self._click_element(driver, el):
                return True
        return False

    def _wait_for_send_effect(
        self,
        driver: WebDriver,
        *,
        previous_user_count: int,
        previous_assistant_count: int,
        previous_thread_id: str | None,
        expected_input: str,
        input_el: WebElement | None,
        timeout_s: float = 12,
    ) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            current_thread_id = self.current_thread_id(driver)
            if self.turn_count(driver, "user") > previous_user_count:
                return True
            if self.turn_count(driver, "assistant") > previous_assistant_count:
                return True
            if current_thread_id and current_thread_id != previous_thread_id:
                return True
            if self._current_input_text(input_el) != expected_input:
                return True
            time.sleep(0.4)
        return False

    def send_message(self, driver: WebDriver, message: str = None) -> bool:
        msg = message or self.test_message
        try:
            input_el = self._best_input_element(driver, timeout=12)
            if not input_el:
                logger.error("[%s] Input element not found", self.name)
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
            input_el.send_keys(msg)
            time.sleep(0.4)

            user_before = self.turn_count(driver, "user")
            assistant_before = self.turn_count(driver, "assistant")
            thread_before = self.current_thread_id(driver)

            if not self._click_send_button(driver):
                input_el.send_keys(Keys.ENTER)
                time.sleep(0.5)

            if not self._wait_for_send_effect(
                driver,
                previous_user_count=user_before,
                previous_assistant_count=assistant_before,
                previous_thread_id=thread_before,
                expected_input=msg,
                input_el=input_el,
            ):
                return False
            logger.info("[%s] Message sent: %s", self.name, msg[:80])
            time.sleep(self.post_send_settle_s)
            return True
        except Exception as exc:
            logger.error("[%s] Send failed: %s", self.name, exc)
            return False

    def wait_for_response(
        self,
        driver: WebDriver,
        *,
        previous_assistant_count: int | None = None,
        previous_assistant_text: str | None = None,
    ) -> bool:
        deadline = time.time() + self.response_timeout_s
        last_text = previous_assistant_text or ""
        stable_since: float | None = None
        response_started = False

        while time.time() < deadline:
            current_count = self.turn_count(driver, "assistant")
            current_text = self.last_turn_text(driver, "assistant")
            stop_visible = self._first_displayed(
                self._find_elements_safe(driver, By.CSS_SELECTOR, self.stop_button_selector)
            ) is not None

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
                elif not stop_visible and (time.time() - stable_since) >= 2.5:
                    logger.info("[%s] Response complete", self.name)
                    return True
            time.sleep(0.5)

        return response_started and bool(last_text.strip())

    # --- Perplexity-specific navigation/actions ---------------------------

    def navigate_to_new_thread(self, driver: WebDriver) -> bool:
        if self._click_text_entry(driver, ["New Thread", "New", "Home"], timeout_s=3):
            time.sleep(1)
            return self.find_input(driver)
        return self.navigate(driver)

    def navigate_to_library(self, driver: WebDriver) -> bool:
        try:
            driver.get(self.base_url + "/library")
            time.sleep(self.page_load_wait_s)
            return True
        except Exception:
            return False

    def navigate_to_settings(self, driver: WebDriver) -> bool:
        for path in ("/settings", "/account", "/profile"):
            try:
                driver.get(self.base_url + path)
                time.sleep(self.page_load_wait_s)
                if self.page_contains_any_text(driver, ["Settings", "Account", "Profile", "Subscription"]):
                    return True
            except Exception:
                continue
        return False

    def navigate_to_spaces(self, driver: WebDriver) -> bool:
        for path in ("/spaces", "/space"):
            try:
                driver.get(self.base_url + path)
                time.sleep(self.page_load_wait_s)
                if "/space" in (driver.current_url or "") or self.page_contains_any_text(driver, ["Spaces"]):
                    return True
            except Exception:
                continue
        return False

    def navigate_to_shared(self, driver: WebDriver, share_url: str) -> bool:
        try:
            driver.get(share_url)
            time.sleep(self.page_load_wait_s + 1)
            return True
        except Exception:
            return False

    def first_space_url(self, driver: WebDriver) -> str | None:
        if not self.navigate_to_spaces(driver):
            return None
        links = self._find_elements_safe(driver, By.CSS_SELECTOR, 'a[href*="/space"]')
        for link in links:
            try:
                href = link.get_attribute("href") or ""
                if "/space" in href and href.rstrip("/") not in {self.base_url + "/spaces", self.base_url + "/space"}:
                    return href
            except Exception:
                continue
        return None

    def navigate_to_space_thread(self, driver: WebDriver, space_url: str | None = None) -> bool:
        target = space_url or self.first_space_url(driver)
        if not target:
            return False
        try:
            driver.get(target)
            time.sleep(self.page_load_wait_s + 1)
            return self.find_input(driver) or "/space" in (driver.current_url or "")
        except Exception:
            return False

    def select_mode(self, driver: WebDriver, labels: Iterable[str]) -> dict[str, object]:
        labels = [label for label in labels if label]
        before_text = self._visible_body_text(driver)
        clicked = self._click_text_entry(driver, labels, timeout_s=2)
        if not clicked:
            triggers = [
                'button[aria-label*="mode" i]',
                'button[aria-label*="focus" i]',
                'button[aria-label*="source" i]',
                'button[aria-label*="search" i]',
            ]
            for selector in triggers:
                target = self._first_displayed(self._find_elements_safe(driver, By.CSS_SELECTOR, selector))
                if target is not None and self._click_element(driver, target):
                    time.sleep(0.8)
                    clicked = self._click_text_entry(driver, labels, timeout_s=5)
                    break
        after_text = self._visible_body_text(driver)
        labels_seen = [label for label in labels if label.lower() in after_text.lower()]
        return {
            "clicked": clicked,
            "labels": labels,
            "labels_seen": labels_seen,
            "body_changed": before_text != after_text,
            "current_url": driver.current_url,
        }

    def switch_model(self, driver: WebDriver, labels: Iterable[str]) -> dict[str, object]:
        labels = [label for label in labels if label]
        triggers = [
            'button[aria-label*="model" i]',
            '[data-testid*="model" i] button',
            'button[class*="model" i]',
        ]
        opened = False
        for selector in triggers:
            target = self._first_displayed(self._find_elements_safe(driver, By.CSS_SELECTOR, selector))
            if target is not None and self._click_element(driver, target):
                opened = True
                time.sleep(0.8)
                break
        clicked = self._click_text_entry(driver, labels, timeout_s=6) if opened else False
        return {
            "opened": opened,
            "clicked": clicked,
            "labels": labels,
            "current_url": driver.current_url,
        }

    def click_related_question(self, driver: WebDriver) -> bool:
        selectors = [
            '[class*="related" i] button',
            '[class*="follow" i] button',
            'button[data-testid*="related" i]',
            'button[data-testid*="follow" i]',
        ]
        for selector in selectors:
            for candidate in self._find_elements_safe(driver, By.CSS_SELECTOR, selector):
                try:
                    text = (candidate.text or "").strip()
                    if len(text) < 8:
                        continue
                    if self._click_element(driver, candidate):
                        time.sleep(1)
                        return True
                except Exception:
                    continue
        return self._click_text_entry(driver, ["Related", "Ask follow-up"], timeout_s=3)

    def click_create_entry(self, driver: WebDriver, labels: Iterable[str]) -> dict[str, object]:
        details = self.select_mode(driver, labels)
        if not details["clicked"]:
            details = {
                **details,
                "clicked": self._click_text_entry(driver, labels, timeout_s=5),
            }
        details["current_url"] = driver.current_url
        return details

    def click_regenerate(self, driver: WebDriver) -> bool:
        return self._click_text_entry(
            driver,
            ["Try again", "Retry", "Regenerate", "Rewrite", "Rerun"],
            timeout_s=5,
        )

    def force_error(self, driver: WebDriver, message: str) -> bool:
        if not self.find_input(driver):
            if not self.navigate(driver):
                return False
        input_el = self._best_input_element(driver, timeout=8)
        if not input_el:
            return False
        try:
            input_el.click()
            input_el.send_keys(Keys.CONTROL, "a")
            input_el.send_keys(Keys.DELETE)
            input_el.send_keys(message)
            time.sleep(0.4)
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
            return self.wait_for_page_text(
                driver,
                ["offline", "network", "try again", "error", "failed"],
                timeout_s=15,
            )
        except Exception:
            return False
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

    def detect_features(self, driver: WebDriver) -> dict[str, bool]:
        features = {
            "logged_in": False,
            "pro_search": False,
            "research": False,
            "spaces": False,
            "model_selector": False,
            "file_upload": False,
            "create": False,
            "security_challenge": False,
        }
        try:
            if not self.navigate(driver):
                features["security_challenge"] = self.is_security_challenge(driver)
                return features
            body = self._visible_body_text(driver)
            features["security_challenge"] = self.is_security_challenge(driver)
            features["logged_in"] = self.find_input(driver)
            features["pro_search"] = any(word in body for word in ["Pro", "Quick"])
            features["research"] = "Research" in body or "Deep Research" in body
            features["spaces"] = "Spaces" in body or "/space" in (driver.page_source or "")
            features["model_selector"] = "model" in body.lower() or "Sonar" in body
            features["file_upload"] = bool(self._find_elements_safe(driver, By.CSS_SELECTOR, self.file_input_selector))
            features["create"] = "Create" in body or "image" in body.lower() or "video" in body.lower()
        except Exception:
            return features
        return features

# SPDX-License-Identifier: Apache-2.0
"""ChatGPT (chatgpt.com) site adapter."""

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


_CONVERSATION_RE = re.compile(r"/c/([a-z0-9-]+)", re.IGNORECASE)


class ChatGPTAdapter(BaseSiteAdapter):
    name = "chatgpt"
    provider = "openai"
    url = "https://chatgpt.com/"

    input_selector = "#prompt-textarea"
    send_button_selector = 'button[data-testid="send-button"]'
    stop_button_selector = (
        'button[data-testid="stop-button"], '
        'button[aria-label*="Stop"], '
        'button[aria-label*="停止"]'
    )
    response_container_selector = '[data-message-author-role="assistant"]'
    user_turn_selector = (
        'section[data-testid^="conversation-turn"][data-turn="user"], '
        '[data-testid^="conversation-turn"][data-turn="user"]'
    )
    assistant_turn_selector = (
        'section[data-testid^="conversation-turn"][data-turn="assistant"], '
        '[data-testid^="conversation-turn"][data-turn="assistant"]'
    )
    login_wall_selectors = [
        'button[data-testid="login-button"]',
        '[data-testid="auth-login-button"]',
        'a[href*="/auth/login"]',
    ]
    file_input_selector = 'input[type="file"]:not([accept*="image"])'
    image_input_selector = 'input[type="file"][accept*="image"]'
    supports_file_upload = True
    supports_image_upload = True

    page_load_wait_s = 4
    response_timeout_s = 60

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

    def _xpath_literal(self, value: str) -> str:
        if "'" not in value:
            return f"'{value}'"
        if '"' not in value:
            return f'"{value}"'
        parts = value.split("'")
        return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"

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
        return len(
            self._find_elements(
                driver,
                '[data-testid^="conversation-turn"]',
            )
        )

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

    def send_message(self, driver: WebDriver, message: str = None) -> bool:
        """ChatGPT uses a contenteditable div, not a plain textarea."""
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

            clicked = False
            deadline = time.time() + 20
            while time.time() < deadline and not clicked:
                for btn in self._find_elements(driver, self.send_button_selector):
                    try:
                        if not (btn.is_displayed() and btn.is_enabled()):
                            continue
                        try:
                            btn.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", btn)
                        clicked = True
                        break
                    except Exception:
                        continue
                if not clicked:
                    time.sleep(0.5)
            if not clicked:
                input_el.send_keys(Keys.ENTER)

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
                EC.presence_of_element_located((By.CSS_SELECTOR, self.stop_button_selector))
            )
            return True
        except TimeoutException:
            return False

    def wait_for_response(self, driver: WebDriver) -> bool:
        """Wait for streaming to finish; ChatGPT shows a stop button while generating."""
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
                'button[data-testid*="new-chat"]',
                'button[aria-label*="New chat"]',
                'button[aria-label*="新聊天"]',
                'button[aria-label*="新建聊天"]',
                'a[href="/"] button',
                'a[href="/"]',
            ],
        ):
            time.sleep(1)
            new_id = self.current_conversation_id(driver)
            if old_id and new_id == old_id:
                time.sleep(1)
            return True

        try:
            driver.get(self.url)
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
            self._xpath_contains_aria(["Edit", "编辑", "編輯"]),
        )
        target = self._first_displayed(buttons)
        if target is not None and self._click_element(driver, target):
            time.sleep(0.8)
            return True
        return False

    def edit_last_user_message(self, driver: WebDriver, new_message: str) -> bool:
        if not self.click_edit_last_user_message(driver):
            return False

        editor = self._wait_for_element(driver, self.input_selector, timeout=10)
        if editor is None:
            candidates = self._find_elements(driver, '[contenteditable="true"]')
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

        if self._click_first(driver, [self.send_button_selector]):
            time.sleep(0.8)
            return True

        try:
            editor.send_keys(Keys.ENTER)
            time.sleep(0.8)
            return True
        except Exception:
            return False

    def click_last_assistant_more_actions(self, driver: WebDriver) -> bool:
        turn = self.last_turn(driver, "assistant")
        if turn is None:
            return False
        self._hover(driver, turn)

        buttons = self._find_elements_safe(
            turn,
            By.XPATH,
            self._xpath_contains_aria(["More", "更多", "更多操作"]),
        )
        target = self._first_displayed(buttons)
        if target is not None and self._click_element(driver, target):
            time.sleep(0.8)
            return True
        return False

    def click_menu_item(self, driver: WebDriver, labels: Iterable[str]) -> bool:
        xpath = self._xpath_contains_text(labels)
        found = self._find_elements_safe(driver, By.XPATH, xpath)
        target = self._first_displayed(found)
        if target is not None and self._click_element(driver, target):
            time.sleep(0.8)
            return True
        return False

    def click_regenerate(self, driver: WebDriver) -> bool:
        if self._click_first(
            driver,
            [
                'button[data-testid*="regenerate"]',
                'button[aria-label*="Regenerate"]',
                'button[aria-label*="重新生成"]',
            ],
        ):
            time.sleep(0.8)
            return True

        if not self.click_last_assistant_more_actions(driver):
            return False
        return self.click_menu_item(
            driver,
            ["Regenerate", "重新生成", "重新回答", "Try again"],
        )

    def flip_branch(self, driver: WebDriver, direction: str = "next") -> bool:
        labels = ["Next", "下一个", "下一条", "下一分支"]
        if direction == "prev":
            labels = ["Previous", "上一个", "上一条", "上一分支"]
        buttons = self._find_elements_safe(driver, By.XPATH, self._xpath_contains_aria(labels))
        target = self._first_displayed(buttons)
        if target is not None and self._click_element(driver, target):
            time.sleep(1)
            return True
        return False

    def navigate_to_settings(self, driver: WebDriver) -> bool:
        try:
            driver.get(self.url + "settings")
            time.sleep(self.page_load_wait_s)
            return True
        except Exception:
            return False

    def navigate_to_gpt(self, driver: WebDriver, gpt_url: str | None = None) -> bool:
        try:
            if gpt_url:
                driver.get(gpt_url)
                time.sleep(self.page_load_wait_s)
                return True

            driver.get(self.url + "gpts")
            time.sleep(self.page_load_wait_s + 1)
            gpt_links = self._find_elements(driver, 'a[href*="/g/"]')
            target = self._first_displayed(gpt_links)
            if target is None:
                return False
            href = target.get_attribute("href")
            if not href:
                return False
            driver.get(href)
            time.sleep(self.page_load_wait_s)
            return True
        except Exception:
            return False

    def navigate_to_project(self, driver: WebDriver, project_url: str | None = None) -> bool:
        try:
            if project_url:
                driver.get(project_url)
                time.sleep(self.page_load_wait_s)
                return True

            driver.get(self.url + "projects")
            time.sleep(self.page_load_wait_s + 1)
            project_links = self._find_elements(driver, 'a[href*="/project/"], a[href*="/projects/"]')
            target = None
            for link in project_links:
                try:
                    href = link.get_attribute("href") or ""
                except Exception:
                    continue
                if href.rstrip("/").endswith("/projects"):
                    continue
                if link.is_displayed():
                    target = link
                    break
            if target is None:
                return False
            href = target.get_attribute("href")
            if not href:
                return False
            driver.get(href)
            time.sleep(self.page_load_wait_s)
            return True
        except Exception:
            return False

    def toggle_temporary(self, driver: WebDriver) -> bool:
        try:
            driver.get(self.url + "?temporary-chat=true")
            time.sleep(self.page_load_wait_s)
            if "temporary" in driver.current_url.lower():
                return True
            if self.page_contains_any_text(driver, ["Temporary", "临时"]):
                return True
        except Exception:
            pass

        return self._click_first(
            driver,
            [
                'button[aria-label*="Temporary"]',
                'button[aria-label*="临时"]',
                'a[aria-label*="Temporary"]',
                'a[aria-label*="临时"]',
            ],
        )

    def switch_model(self, driver: WebDriver, labels: Iterable[str]) -> bool:
        if not self._click_first(
            driver,
            [
                'button[data-testid*="model"]',
                'button[aria-label*="Model"]',
                'button[aria-label*="模型"]',
                'button[aria-label*="切换模型"]',
            ],
        ):
            return False
        return self.click_menu_item(driver, labels)

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
            "Try again",
            "Retry",
            "出现问题",
            "出错",
            "错误",
            "重试",
        ]
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.page_contains_any_text(driver, needles):
                return True
            if self._click_first(
                driver,
                ['button[data-testid*="retry"]', 'button[aria-label*="Retry"]'],
            ):
                return True
            time.sleep(0.5)
        return False

    def detect_features(self, driver: WebDriver) -> dict[str, bool]:
        features = {
            "logged_in": False,
            "settings": False,
            "gpts": False,
            "projects": False,
            "temporary": False,
            "canvas": False,
            "image_generation": False,
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
            features["gpts"] = (
                '/g/' in page_source
                or '/gpts' in page_source
                or self.page_contains_any_text(driver, ["GPTs", "GPT"])
            )
            features["projects"] = (
                "/projects" in page_source
                or self.page_contains_any_text(driver, ["Projects", "项目"])
            )
            features["temporary"] = self.page_contains_any_text(
                driver,
                ["Temporary", "临时"],
            )
            features["canvas"] = self.page_contains_any_text(driver, ["Canvas", "画布"])
            features["image_generation"] = self.page_contains_any_text(
                driver,
                ["image", "图片", "生成图像"],
            )
        except Exception:
            return features
        return features

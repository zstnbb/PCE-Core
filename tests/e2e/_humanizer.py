# SPDX-License-Identifier: Apache-2.0
"""Human-behavior simulation primitives for live-site E2E.

Why this module exists
----------------------
``_stealth.py`` covers JS-level fingerprint surfaces (webdriver,
plugins, WebGL, ...) but it does NOT change *behavior*. Cloudflare
Turnstile / Anthropic / xAI's bot scoring also feeds on:

- Session age (process uptime when first AI-site request fires)
- Navigation pattern (cold ``about:blank -> grok.com`` is suspect)
- Interaction telemetry density (``mousemove`` events / sec, scroll
  curves, focus-blur cadence)
- Click / keystroke timing distribution
- Fingerprint cluster repeat rate from the same IP

Even with perfect ``_stealth.py`` patches, an arrow-straight
sequence ``driver.get(grok)`` -> ``find_element.click()`` raises a
flag because it lacks the entropy a real human emits between page
load and first interaction.

This module supplies that entropy:

- :func:`read_pause` -- jittered post-navigation dwell so the page's
  Turnstile / first-paint metrics see realistic dwell distribution
- :func:`human_click` -- Bezier-curve mouse path + variable
  pre-click hover via Selenium ActionChains
- :func:`human_type` -- per-char delay drawn from a log-normal
  distribution + occasional typo+backspace
- :func:`gentle_scroll` -- multi-step scroll with overshoot
- :class:`MouseJiggler` -- daemon thread that emits low-frequency
  ``Input.dispatchMouseEvent`` via CDP so the page sees ambient
  interaction even when no test is actively driving
- :func:`warmup_browse` -- visits a non-AI site first so the
  browser's referrer / DNS / TCP-keepalive state looks like a
  real session before hitting any high-risk endpoint

All public functions tolerate Selenium / CDP errors and degrade to
no-ops -- never let humanization noise fail the test it's meant to
protect.

Skip via env: ``PCE_E2E_HUMANIZE=0``.
"""
from __future__ import annotations

import logging
import math
import os
import random
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

logger = logging.getLogger("pce.e2e.humanizer")


# A tiny set of low-risk warmup destinations. They are intentionally
# benign, well-cached, non-CF-gated, and span news/dev/encyclopaedia
# verticals so the resulting browsing-history vector looks like a
# normal evening session before someone clicks over to ChatGPT.
DEFAULT_WARMUP_SITES: list[str] = [
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://github.com/trending",
    "https://news.ycombinator.com/",
]


def _humanize_enabled() -> bool:
    """Master switch. Set ``PCE_E2E_HUMANIZE=0`` to disable everything
    here (debugging mode, or when running on a headed, manually-driven
    browser where you don't want background mousemoves cluttering the
    UI)."""
    return os.environ.get("PCE_E2E_HUMANIZE", "1").strip() != "0"


# ---------------------------------------------------------------------------
# Timing primitives
# ---------------------------------------------------------------------------

def _log_normal_delay(mean_ms: float, sigma: float = 0.45) -> float:
    """Draw a per-action delay (seconds) from a log-normal distribution
    centred on ``mean_ms``. Real human action timing follows log-normal
    much more faithfully than uniform; uniform-distributed delays are
    themselves a bot tell because their variance is too low at the
    extremes.
    """
    mu = math.log(max(mean_ms, 1.0) / 1000.0)
    return max(0.01, random.lognormvariate(mu, sigma))


def read_pause(min_s: float = 2.5, max_s: float = 7.0) -> float:
    """Sleep ``[min_s, max_s]`` seconds with log-normal jitter biased
    towards the lower bound (most users glance, few linger). Returns
    the actual sleep duration.

    Use this between ``driver.get(...)`` and any ``find_element``
    interaction. Bot scoring engines specifically fingerprint the
    "page load -> first input" gap; real users sit in the 2-8s band,
    bots are in the 0-200ms band.
    """
    if not _humanize_enabled():
        return 0.0
    span = max(0.01, max_s - min_s)
    bias = random.betavariate(2.0, 5.0)  # left-skewed in [0,1]
    secs = min_s + bias * span
    time.sleep(secs)
    return secs


def pace_between_sites(idx: int, total: int) -> float:
    """Inter-site dwell that grows as the run progresses. Opening 18
    tabs in 30 seconds is the textbook bot pattern; we stretch the
    interval so by tab 18 we're spending 30-90s between tabs rather
    than 2-5s.

    Returns the sleep duration. ``idx`` is 0-based.
    """
    if not _humanize_enabled():
        return 0.0
    progress = idx / max(total - 1, 1)
    base_min = 8.0 + 18.0 * progress    # 8s early -> 26s late
    base_max = 22.0 + 50.0 * progress   # 22s early -> 72s late
    return read_pause(base_min, base_max)


# ---------------------------------------------------------------------------
# Mouse path primitives
# ---------------------------------------------------------------------------

def _bezier_points(
    p0: tuple[float, float],
    p3: tuple[float, float],
    *,
    steps: int = 18,
    sag: float = 0.35,
) -> list[tuple[float, float]]:
    """Generate a cubic Bezier path between two points.

    ``sag`` controls how far the two handles deviate perpendicular to
    the straight line, expressed as a fraction of the segment length.
    A bit of asymmetric sag plus jitter on each handle gives a natural
    overshoot/under-shoot characteristic of a real hand.
    """
    x0, y0 = p0
    x3, y3 = p3
    dx, dy = x3 - x0, y3 - y0
    length = math.hypot(dx, dy) or 1.0
    # Perpendicular unit vector for the sag.
    px, py = -dy / length, dx / length
    bow1 = sag * length * (random.random() * 0.6 + 0.7) * (1 if random.random() > 0.5 else -1)
    bow2 = sag * length * (random.random() * 0.6 + 0.7) * (1 if random.random() > 0.5 else -1)
    # Control points at 1/3 and 2/3 along the chord, displaced by the
    # perpendicular.
    x1 = x0 + dx / 3.0 + px * bow1 + (random.random() - 0.5) * 6.0
    y1 = y0 + dy / 3.0 + py * bow1 + (random.random() - 0.5) * 6.0
    x2 = x0 + 2.0 * dx / 3.0 + px * bow2 + (random.random() - 0.5) * 6.0
    y2 = y0 + 2.0 * dy / 3.0 + py * bow2 + (random.random() - 0.5) * 6.0

    pts: list[tuple[float, float]] = []
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        b0 = u * u * u
        b1 = 3 * u * u * t
        b2 = 3 * u * t * t
        b3 = t * t * t
        x = b0 * x0 + b1 * x1 + b2 * x2 + b3 * x3
        y = b0 * y0 + b1 * y1 + b2 * y2 + b3 * y3
        pts.append((x, y))
    return pts


def _viewport_size(driver: Any) -> tuple[int, int]:
    try:
        rect = driver.execute_script(
            "return [window.innerWidth|0, window.innerHeight|0];"
        )
        return int(rect[0]), int(rect[1])
    except Exception:
        return 1280, 800


def _cdp_mouse(driver: Any, kind: str, x: float, y: float, button: str = "none") -> bool:
    """Dispatch a single CDP ``Input.dispatchMouseEvent``. Returns
    True on success. We use CDP rather than ``ActionChains`` because
    CDP-injected events are indistinguishable from OS-injected events
    at the renderer (``isTrusted=true``) which is the gold-standard
    signal for Cloudflare bot scoring.
    """
    try:
        driver.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {
                "type": kind,        # mouseMoved | mousePressed | mouseReleased
                "x": float(x),
                "y": float(y),
                "button": button,    # none | left | middle | right
                "clickCount": 1 if kind in ("mousePressed", "mouseReleased") else 0,
                "buttons": 1 if button == "left" and kind != "mouseReleased" else 0,
            },
        )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public mouse / scroll / typing helpers
# ---------------------------------------------------------------------------

def human_click(driver: Any, element: Any, *, hover_ms: int = 250) -> bool:
    """Move the cursor along a Bezier path to ``element``, hover, then
    click. Falls back to ``element.click()`` if anything in the
    humanized path fails.

    Returns True on success.
    """
    if not _humanize_enabled():
        try:
            element.click()
            return True
        except Exception:
            return False

    try:
        rect = element.rect  # {x, y, width, height} in viewport coords
        target_x = rect["x"] + rect["width"] * (0.3 + random.random() * 0.4)
        target_y = rect["y"] + rect["height"] * (0.3 + random.random() * 0.4)
    except Exception:
        try:
            element.click()
            return True
        except Exception:
            return False

    # Start the cursor somewhere near the current position. We track
    # last-known mouse pos in driver._pce_mouse_pos so consecutive
    # clicks form a continuous trajectory rather than teleporting.
    last = getattr(driver, "_pce_mouse_pos", None)
    if last is None:
        vw, vh = _viewport_size(driver)
        last = (vw * 0.5, vh * 0.5)

    pts = _bezier_points(last, (target_x, target_y), steps=random.randint(14, 24))
    for x, y in pts:
        _cdp_mouse(driver, "mouseMoved", x, y)
        time.sleep(random.uniform(0.008, 0.022))

    setattr(driver, "_pce_mouse_pos", (target_x, target_y))
    time.sleep(_log_normal_delay(hover_ms, sigma=0.35))
    _cdp_mouse(driver, "mousePressed", target_x, target_y, button="left")
    time.sleep(random.uniform(0.04, 0.12))
    _cdp_mouse(driver, "mouseReleased", target_x, target_y, button="left")
    return True


def human_type(driver: Any, element: Any, text: str, *, mean_cps: float = 6.5) -> None:
    """Type ``text`` into ``element`` with realistic per-character
    cadence. ``mean_cps`` is the target characters-per-second. Real
    typists average 4-8 cps with high variance; we draw each gap from
    a log-normal centred on ``1000 / mean_cps`` ms.

    Inserts an occasional typo (random adjacent key) followed by
    backspace + retype, at ~1.5% per char. This is what every real
    typist does and what no naive ``send_keys`` ever simulates.
    """
    if not _humanize_enabled() or not text:
        try:
            element.send_keys(text)
        except Exception:
            pass
        return

    mean_ms = 1000.0 / max(mean_cps, 0.5)
    try:
        element.click()
    except Exception:
        pass

    for ch in text:
        if random.random() < 0.015 and ch.isalpha():
            # Typo: send a random adjacent letter, pause, backspace, then real char.
            wrong = chr(((ord(ch) - 97 + random.choice([-1, 1])) % 26) + 97)
            try:
                element.send_keys(wrong)
                time.sleep(_log_normal_delay(mean_ms * 1.4))
                from selenium.webdriver.common.keys import Keys  # local import keeps optional dep
                element.send_keys(Keys.BACKSPACE)
                time.sleep(_log_normal_delay(mean_ms))
            except Exception:
                pass
        try:
            element.send_keys(ch)
        except Exception:
            return
        time.sleep(_log_normal_delay(mean_ms))
        if ch in (".", "?", "!", "\n"):
            # Mini-pause at sentence boundaries.
            time.sleep(random.uniform(0.25, 0.9))


def gentle_scroll(driver: Any, total_y: int = 600) -> None:
    """Scroll ``total_y`` pixels in 4-7 stepped chunks with mild
    overshoot+correction. A single ``window.scrollTo`` is cheap to
    fingerprint as automation; chunked scroll with variable wheel
    deltas matches the wheel pattern of a real mouse.
    """
    if not _humanize_enabled():
        return
    if total_y == 0:
        return
    sign = 1 if total_y > 0 else -1
    remaining = abs(total_y)
    overshoot = int(abs(total_y) * random.uniform(0.05, 0.18)) * sign
    steps = random.randint(4, 7)
    while remaining > 0 and steps > 0:
        chunk = max(40, int(remaining / steps + random.uniform(-25, 25)))
        chunk = min(chunk, remaining)
        try:
            driver.execute_script(f"window.scrollBy(0, {sign * chunk});")
        except Exception:
            return
        remaining -= chunk
        steps -= 1
        time.sleep(random.uniform(0.08, 0.22))
    # Mild overshoot then correction.
    if overshoot:
        try:
            driver.execute_script(f"window.scrollBy(0, {overshoot});")
            time.sleep(random.uniform(0.15, 0.35))
            driver.execute_script(f"window.scrollBy(0, {-overshoot // 2});")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# MouseJiggler -- ambient interaction
# ---------------------------------------------------------------------------

class MouseJiggler:
    """Background daemon that emits a low-rate ``mousemove`` stream so
    the page sees continuous interaction telemetry rather than long
    silences.

    Cloudflare's bot scoring uses temporal density of input events as
    a feature -- a tab with zero ``mousemove`` events for 30 seconds,
    even one with valid cookies, gets de-trusted. Real users
    constantly nudge the mouse without realising. ~one event every
    8-22 seconds is enough to stay above the dead-tab threshold without
    looking robotic.

    Use as a context manager (idempotent if disabled by env)::

        with MouseJiggler(driver):
            do_test_actions()
    """

    def __init__(self, driver: Any, *, min_interval: float = 8.0, max_interval: float = 22.0):
        self.driver = driver
        self.min_interval = min_interval
        self.max_interval = max_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "MouseJiggler":
        if not _humanize_enabled():
            return self
        if self._thread is not None:
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="pce-mouse-jiggler", daemon=True,
        )
        self._thread.start()
        logger.info("MouseJiggler started")
        return self

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        try:
            self._thread.join(timeout=2.0)
        except Exception:
            pass
        self._thread = None
        logger.info("MouseJiggler stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            wait = random.uniform(self.min_interval, self.max_interval)
            if self._stop.wait(wait):
                return
            try:
                vw, vh = _viewport_size(self.driver)
            except Exception:
                vw, vh = 1280, 800
            last = getattr(self.driver, "_pce_mouse_pos", None) or (vw * 0.5, vh * 0.5)
            # Drift the cursor 8-80 px in a random direction.
            dx = random.uniform(-80, 80)
            dy = random.uniform(-80, 80)
            new_x = max(2, min(vw - 2, last[0] + dx))
            new_y = max(2, min(vh - 2, last[1] + dy))
            # Move along a short bezier so it doesn't teleport.
            for px, py in _bezier_points(last, (new_x, new_y), steps=random.randint(4, 8)):
                if self._stop.is_set():
                    return
                _cdp_mouse(self.driver, "mouseMoved", px, py)
                time.sleep(random.uniform(0.012, 0.030))
            try:
                setattr(self.driver, "_pce_mouse_pos", (new_x, new_y))
            except Exception:
                pass

    # Context-manager sugar
    def __enter__(self) -> "MouseJiggler":
        return self.start()

    def __exit__(self, *_: Any) -> None:
        self.stop()


@contextmanager
def jiggling(driver: Any, **kwargs: Any) -> Iterator[MouseJiggler]:
    """Functional alias around :class:`MouseJiggler`."""
    j = MouseJiggler(driver, **kwargs).start()
    try:
        yield j
    finally:
        j.stop()


# ---------------------------------------------------------------------------
# Warmup browse
# ---------------------------------------------------------------------------

def warmup_browse(
    driver: Any,
    urls: Sequence[str] | None = None,
    *,
    dwell_min: float = 6.0,
    dwell_max: float = 14.0,
) -> int:
    """Visit a few benign sites BEFORE any AI-site navigation, dwell
    on each with humanlike scroll. Two effects:

    1. The session's ``document.referrer`` becomes a real URL on the
       eventual AI-site request, instead of the empty string a fresh
       cold-launch produces.
    2. The browsing-history fingerprint vector picks up a couple of
       neutral domains, diluting the "this session has only ever
       loaded grok.com and chatgpt.com" outlier signal.

    Returns the count of warmup pages successfully visited.
    """
    if not _humanize_enabled():
        return 0
    targets = list(urls or DEFAULT_WARMUP_SITES)
    visited = 0
    for url in targets:
        try:
            driver.switch_to.new_window("tab")
            driver.get(url)
        except Exception as exc:
            logger.warning("warmup_browse: %s -> %s", url, exc)
            continue
        # Dwell + scroll.
        time.sleep(random.uniform(2.0, 4.0))
        try:
            gentle_scroll(driver, random.randint(300, 900))
        except Exception:
            pass
        time.sleep(random.uniform(dwell_min, dwell_max))
        visited += 1
    return visited

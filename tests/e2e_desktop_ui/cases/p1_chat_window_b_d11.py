# SPDX-License-Identifier: Apache-2.0
"""Claude Desktop chat — Window B: D11 long-context survival.

Drives 50 turns in a single conversation. Each turn asks Claude for a
~120-token fact on a varied topic, so cumulative ``token_estimate``
across the conversation easily clears the 8K-token D11 threshold.

D11 verdict criteria (per ``DESKTOP-PRODUCT-MATRIX.md`` §5):

  "A 50-turn conversation with cumulative >=8K tokens captures every
   turn, no message dropped"

So we check post-run:
  1. Distinct ``session_id`` count == 1 (Bug 1 D03 fix must hold across
     50 turns, not just 5).
  2. ``messages`` rows with role='user' for our pair_ids == 50.
  3. ``messages`` rows with role='assistant' for our pair_ids == 50.
  4. Sum of ``token_estimate`` across the 100 messages >= 8000.
  5. ``turn_index`` strictly monotonic within the session.

Run cost: ~50 turns x ~4s/turn = ~3.5 minutes. Each request body
grows because Claude Desktop sends the full conversation transcript
each turn (hence "long-context").
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from ..drivers.claude_desktop import ClaudeDesktopDriver
from ..utils import baseline_ts as _baseline_ts


# 50 distinct topics — we vary so Claude doesn't refuse repetition or
# auto-shorten boilerplate replies.
TOPICS = [
    "the Roman aqueduct system", "octopus problem-solving",
    "the discovery of penicillin", "Mongolian throat singing",
    "the Antikythera mechanism", "tardigrade extremophile biology",
    "the Voynich manuscript", "Polynesian wayfinding",
    "the Fermi paradox", "Indus Valley script",
    "Greek fire weapon", "the Phaistos disc",
    "Easter Island Rongorongo", "permafrost methane release",
    "deep-sea hydrothermal vents", "Norman conquest cuisine",
    "the Library of Ashurbanipal", "Hagia Sophia engineering",
    "Khmer hydraulic city of Angkor", "the Inca quipu accounting",
    "Etruscan haruspicy", "Mongol postal yam relay",
    "Phoenician purple dye production", "Carthaginian war elephants",
    "Byzantine Greek fire formula loss", "Silk Road musical exchange",
    "Persian qanat irrigation", "Mesopotamian beer brewing",
    "Olmec colossal heads", "the Sea Peoples mystery",
    "Roman concrete (opus caementicium)", "early Andean potato cultivars",
    "Polynesian sweet potato puzzle", "Yamnaya horse domestication",
    "Indo-European laryngeals", "the Toba volcanic winter",
    "Cretaceous-Paleogene boundary", "Mariana Trench microbiology",
    "lunar regolith engineering", "Voyager Golden Record curation",
    "MIT Whirlwind core memory", "the ENIAC programming team",
    "Norwegian black metal scene origins", "Andalusian flamenco roots",
    "Tuvan overtone singing technique", "Congolese rumba evolution",
    "Pygmy polyphony", "Inuit throat song duels",
    "Sardinian quartet a tenore", "Bulgarian women's choir style",
]


def main() -> int:
    if len(TOPICS) < 50:
        raise RuntimeError(f"Need 50 topics, got {len(TOPICS)}")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("p1_chat_window_b_d11")

    base = _baseline_ts()
    Path("_baseline_ts.txt").write_text(str(base), encoding="ascii")
    log.info("baseline_ts = %.3f (saved)", base)

    driver = ClaudeDesktopDriver()

    log.info("=== Window B — D11 long-context, 50 turns ===")
    log.info("intro turn: tell Claude what to expect")

    pair_ids: list[str] = []

    # Turn 1 — set expectation for short replies, kicks off the session
    intro = (
        "I'll ask you 50 short questions. For EACH, reply with ONE "
        "paragraph of ~120 words. Don't add headers or extra commentary. "
        "Question 1: tell me a fact about " + TOPICS[0]
    )
    log.info("--- Turn 1/50 ---")
    pid = driver.send_message(intro, wait_done=True, wait_timeout=60.0)
    if not pid:
        log.error("Turn 1 failed; aborting Window B")
        return 1
    pair_ids.append(pid)
    time.sleep(1.5)

    # Turns 2..50 — succinct topic prompts
    for i, topic in enumerate(TOPICS[1:], start=2):
        log.info("--- Turn %d/50 (%s) ---", i, topic[:30])
        prompt = f"Question {i}: tell me a fact about {topic}"
        # Allow up to 90s per turn — context grows so latency creeps up.
        pid = driver.send_message(prompt, wait_done=True, wait_timeout=90.0)
        if not pid:
            log.error("Turn %d failed; D11 cannot complete (skip-ahead breaks "
                      "the no-drop assertion)", i)
            return 1
        pair_ids.append(pid)
        # Small inter-turn pause so we don't race Claude Desktop's
        # composer-clear animation on slow machines.
        time.sleep(1.0)

    log.info("\n=== Window B complete ===")
    log.info("turns sent: %d / 50", len(pair_ids))
    log.info("pair_id prefixes: %s ...", ",".join(p[:8] for p in pair_ids[:5]))
    log.info("                  ... %s", ",".join(p[:8] for p in pair_ids[-5:]))

    return 0


if __name__ == "__main__":
    sys.exit(main())

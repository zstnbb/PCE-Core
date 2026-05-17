# SPDX-License-Identifier: Apache-2.0
"""pce_core.standards -- machine-loadable Web target standards.

Per ADR-006 Wave 4r §5: each P0 site has a markdown standard that declares
the contract ("send a prompt, see a response stream back") in a form the
maintenance agent reads as ground truth, paired with the verification
scripts that prove a leg is V-GREEN.

Authority: each <target_id>.md is canonical. The Python model below is the
parsed form; bugs in this loader must never silently widen the meaning of
a standard file.
"""

from pce_core.standards.loader import (
    Standard,
    Leg,
    load_standard,
    list_standards,
    StandardsError,
)

__all__ = [
    "Standard",
    "Leg",
    "load_standard",
    "list_standards",
    "StandardsError",
]

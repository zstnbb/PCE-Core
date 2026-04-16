"""PCE Core – Normalizer package.

Transforms raw_captures (Tier 0) into sessions + messages (Tier 1).
Each provider has its own normalizer that understands the specific
request/response format.
"""

from .base import BaseNormalizer, normalize_pair
from .registry import get_all_normalizers, get_normalizer, register_normalizer

__all__ = [
    "BaseNormalizer",
    "normalize_pair",
    "get_all_normalizers",
    "get_normalizer",
    "register_normalizer",
]

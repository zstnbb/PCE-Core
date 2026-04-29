# SPDX-License-Identifier: Apache-2.0
"""Adapter / case contract conformance tests for the probe E2E matrix.

These tests run **at pytest collect time** \u2014 no Chrome, no probe server,
no network. Their job is to catch "scaffolded a new adapter but forgot
to fill in the required selectors" before the matrix tries to run that
adapter against a real browser and emits a confusing
``selector_not_found`` for an adapter that was never configured at all.

Failures in this file are always **bugs in the adapter / case scaffold**,
not site issues. They block the matrix from being run by mistake.

Specifically, we assert:

  * Every ``ALL_SITES`` entry subclasses ``BaseProbeSiteAdapter`` and
    fills the four mandatory class attributes: ``name`` / ``provider``
    / ``url`` / ``input_selectors``.
  * ``name`` values are unique across adapters (otherwise pytest
    parametrize ids collide silently).
  * ``url`` looks like a real https URL on a real host (not the empty
    string default).
  * Every ``ALL_CASES`` entry subclasses ``BaseCase`` and fills
    ``id`` / ``name`` (with the same uniqueness guarantee for ``id``).

If the matrix grows a sixth verb-shape mandatory selector (e.g.
``send_button_selectors`` becomes mandatory for some new case), add the
assertion here.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Site adapter contract
# ---------------------------------------------------------------------------

# Imported lazily inside the fixture so a broken sites/__init__.py fails
# in this conformance test (clearly named) instead of at module-import
# time of every other test that imports tests.e2e_probe.
def _load_sites():
    from tests.e2e_probe.sites import ALL_SITES, BaseProbeSiteAdapter

    return ALL_SITES, BaseProbeSiteAdapter


def _load_cases():
    from tests.e2e_probe.cases import ALL_CASES, BaseCase

    return ALL_CASES, BaseCase


class TestSiteAdapterContract:
    def test_all_sites_is_nonempty(self) -> None:
        sites, _ = _load_sites()
        assert len(sites) > 0, (
            "ALL_SITES is empty; the matrix would generate zero cells. "
            "Add adapter classes to "
            "tests/e2e_probe/sites/__init__.py."
        )

    def test_every_adapter_subclasses_base(self) -> None:
        sites, base = _load_sites()
        for cls in sites:
            assert issubclass(cls, base), (
                f"{cls.__name__} in ALL_SITES does not subclass "
                "BaseProbeSiteAdapter; the matrix runner relies on the "
                "open / check_logged_in / send_prompt / wait_for_done "
                "contract from the base class."
            )

    @pytest.mark.parametrize(
        "field",
        ["name", "provider", "url"],
    )
    def test_every_adapter_fills_required_string_field(
        self, field: str
    ) -> None:
        sites, _ = _load_sites()
        for cls in sites:
            value = getattr(cls, field, None)
            assert isinstance(value, str) and value, (
                f"{cls.__name__}.{field} is empty / non-string. "
                "Every adapter must declare this in its class body. "
                "See tests/e2e_probe/sites/base.py for the contract."
            )

    def test_every_adapter_has_at_least_one_input_selector(self) -> None:
        sites, _ = _load_sites()
        for cls in sites:
            sels = getattr(cls, "input_selectors", None)
            assert sels and len(sels) > 0, (
                f"{cls.__name__}.input_selectors is empty. The matrix "
                "needs at least one CSS selector to find the chat input "
                "(probe will try them in order). If you genuinely don't "
                "know the selector yet, set it to a permissive fallback "
                "like ``('textarea', '[contenteditable=true]')`` and "
                "add a TODO row in tests/e2e_probe/sites/_inventory.md."
            )

    def test_adapter_url_is_https(self) -> None:
        sites, _ = _load_sites()
        for cls in sites:
            url = cls.url
            assert url.startswith("https://"), (
                f"{cls.__name__}.url={url!r} is not https://. The "
                "matrix opens this URL fresh; AI sites that don't "
                "support https are not in scope."
            )

    def test_adapter_names_are_unique(self) -> None:
        sites, _ = _load_sites()
        names = [cls.name for cls in sites]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, (
            f"Duplicate adapter ``name`` values: {sorted(dupes)}. "
            "The matrix uses the name as the pytest parametrize id; "
            "duplicates make ``-k`` filters ambiguous and overwrite "
            "rows in summary.json."
        )

    def test_adapter_names_are_lowercase_identifiers(self) -> None:
        # Pytest parametrize ids accept arbitrary strings, but
        # ``-k <expr>`` matches by substring. To keep ``-k chatgpt-T01``
        # unambiguous, we constrain names to lowercase + a-z0-9_- only.
        import re

        sites, _ = _load_sites()
        pat = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
        for cls in sites:
            assert pat.match(cls.name), (
                f"{cls.__name__}.name={cls.name!r} contains uppercase / "
                "punctuation outside [a-z0-9_-]. Use the same casing "
                "as the file (e.g. 'google_ai_studio')."
            )


# ---------------------------------------------------------------------------
# Case contract
# ---------------------------------------------------------------------------


class TestCaseContract:
    def test_all_cases_is_nonempty(self) -> None:
        cases, _ = _load_cases()
        assert len(cases) > 0, (
            "ALL_CASES is empty; the matrix would generate zero cells."
        )

    def test_every_case_subclasses_base(self) -> None:
        cases, base = _load_cases()
        for cls in cases:
            assert issubclass(cls, base), (
                f"{cls.__name__} in ALL_CASES does not subclass BaseCase."
            )

    def test_every_case_has_id_and_name(self) -> None:
        cases, _ = _load_cases()
        for cls in cases:
            assert isinstance(cls.id, str) and cls.id and cls.id != "T??", (
                f"{cls.__name__}.id is unset (or still the placeholder "
                "'T??'). Set it to a stable identifier like 'T03'."
            )
            assert (
                isinstance(cls.name, str)
                and cls.name
                and cls.name != "unnamed"
            ), (
                f"{cls.__name__}.name is unset (or still the placeholder "
                "'unnamed'). Set a short, snake_case label."
            )

    def test_case_ids_are_unique(self) -> None:
        cases, _ = _load_cases()
        ids = [cls.id for cls in cases]
        dupes = {i for i in ids if ids.count(i) > 1}
        assert not dupes, (
            f"Duplicate case ``id`` values: {sorted(dupes)}. "
            "Two cases with the same id collide in pytest parametrize "
            "ids \u2014 ``-k T01`` becomes ambiguous and summary.json "
            "rows overwrite each other."
        )

    def test_case_run_method_is_overridden(self) -> None:
        cases, base = _load_cases()
        for cls in cases:
            # If a case forgot to implement ``run``, ``cls.run is base.run``
            # which raises NotImplementedError on first invocation \u2014
            # something the matrix would only discover at runtime, after
            # paying the cost of opening a tab. Catch it here.
            assert cls.run is not base.run, (
                f"{cls.__name__}.run is the abstract base implementation. "
                "Override ``run(self, probe, pce_core, adapter)`` in the "
                "subclass."
            )


# ---------------------------------------------------------------------------
# Cross-product sanity (matrix runner won't NameError on collect)
# ---------------------------------------------------------------------------


class TestMatrixCollect:
    """Catches ``test_matrix.py`` import bugs at conformance time.

    If the matrix module itself can't import (e.g. a typo in a new
    case's ``run`` signature), every ``pytest tests/e2e_probe/`` run
    fails with a collection error that doesn't tell you WHICH cell is
    busted. This test imports ``test_matrix`` directly so the failure
    surfaces clearly, with a stack trace.
    """

    def test_matrix_module_imports(self) -> None:
        # Just importing is the assertion.
        from tests.e2e_probe import test_matrix  # noqa: F401

    def test_matrix_cross_product_size_matches_inventories(self) -> None:
        from tests.e2e_probe.test_matrix import _MATRIX

        sites, _ = _load_sites()
        cases, _ = _load_cases()
        assert len(_MATRIX) == len(sites) * len(cases), (
            f"_MATRIX has {len(_MATRIX)} cells but ALL_SITES \u00d7 ALL_CASES "
            f"= {len(sites)} \u00d7 {len(cases)} = {len(sites) * len(cases)}. "
            "Either the cross-product list-comp drifted, or one of the "
            "registries is mutated at import time."
        )

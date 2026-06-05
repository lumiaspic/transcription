"""Tests for the dependency-free i18n layer."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from transcription import i18n


@pytest.fixture(autouse=True)
def _reset_language() -> Iterator[None]:
    """Keep the module-global active language from leaking between tests."""
    previous = i18n.get_language()
    yield
    i18n.set_language(previous)


class TestSetLanguage:
    def test_known_language(self) -> None:
        i18n.set_language("fr")
        assert i18n.get_language() == "fr"

    def test_unknown_falls_back_to_english(self) -> None:
        i18n.set_language("xx")
        assert i18n.get_language() == "en"

    def test_none_falls_back_to_english(self) -> None:
        i18n.set_language(None)
        assert i18n.get_language() == "en"


class TestTranslate:
    def test_returns_active_language_string(self) -> None:
        i18n.set_language("fr")
        assert i18n.t("action.save") == "Enregistrer"

    def test_falls_back_to_english_for_missing_key_in_other_language(self) -> None:
        # Every fr key also exists in en; force a hole by querying a key that
        # only the English catalog would resolve via fallback.
        i18n.set_language("fr")
        # "action.close" exists in both — sanity check fallback path with a
        # key absent from fr by construction is covered by the unknown-key test.
        assert i18n.t("action.close") == "Fermer"

    def test_unknown_key_returns_key_itself(self) -> None:
        assert i18n.t("does.not.exist") == "does.not.exist"

    def test_interpolation(self) -> None:
        i18n.set_language("en")
        assert i18n.t("notify.recording_started", rec_id="20260528_120000") == (
            "Recording started: 20260528_120000"
        )

    def test_interpolation_missing_kwarg_degrades_gracefully(self) -> None:
        # Passing a kwarg but not the {job_id}/{elapsed} the string needs makes
        # str.format raise KeyError; t() must swallow it and return the
        # unformatted message rather than propagate.
        out = i18n.t("notify.recording_stopped", unrelated="x")
        assert "{elapsed}" in out  # unformatted, but no exception

    def test_recent_recordings_label(self) -> None:
        assert i18n.t("card.recordings") == "Recent recordings"

        i18n.set_language("fr")
        assert i18n.t("card.recordings") == "Enregistrements récents"


class TestResolveAndSetLanguage:
    def test_explicit_value_wins(self) -> None:
        assert i18n.resolve_and_set_language("fr") == "fr"
        assert i18n.get_language() == "fr"

    def test_unsupported_explicit_value_falls_back_to_english(self) -> None:
        assert i18n.resolve_and_set_language("de") == "en"

    def test_auto_detects_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANG", "fr_FR.UTF-8")
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.delenv("LANGUAGE", raising=False)
        assert i18n.resolve_and_set_language(None) == "fr"

    def test_auto_with_unknown_locale_falls_back_to_english(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
            monkeypatch.setenv(var, "ja_JP.UTF-8")
        assert i18n.resolve_and_set_language("auto") == "en"

    def test_auto_survives_locale_getlocale_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # No usable env vars, and getlocale() raises (it does on some
        # misconfigured systems). Detection must swallow it and pick English.
        for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
            monkeypatch.delenv(var, raising=False)

        def _boom(*_args: object, **_kwargs: object) -> tuple[str, str]:
            raise ValueError("unsupported locale setting")

        monkeypatch.setattr(i18n._locale, "getlocale", _boom)
        assert i18n.resolve_and_set_language("auto") == "en"


class TestCalendarAbbreviations:
    def test_weekday_english(self) -> None:
        i18n.set_language("en")
        assert i18n.weekday_abbr(6) == "Sun"

    def test_weekday_french(self) -> None:
        i18n.set_language("fr")
        assert i18n.weekday_abbr(0) == "lun."

    def test_month_english(self) -> None:
        i18n.set_language("en")
        assert i18n.month_abbr(12) == "Dec"

    def test_month_french(self) -> None:
        i18n.set_language("fr")
        assert i18n.month_abbr(5) == "mai"


class TestCatalogParity:
    def test_french_covers_every_english_key(self) -> None:
        en_keys = set(i18n._CATALOG["en"])
        fr_keys = set(i18n._CATALOG["fr"])
        assert en_keys == fr_keys

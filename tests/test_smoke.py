"""Smoke tests : vérifient juste que le package s'importe et expose sa version.

Au fur et à mesure que tu ajoutes des tests réels, ce fichier peut disparaître.
"""

from __future__ import annotations

import transcription


def test_package_has_version() -> None:
    assert isinstance(transcription.__version__, str)
    assert transcription.__version__ != ""

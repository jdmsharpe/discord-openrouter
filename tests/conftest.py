import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from discord_openrouter.util import ModelInfo, parse_model_info

try:
    import httpx  # noqa: F401
except ModuleNotFoundError:
    fake_httpx = ModuleType("httpx")

    class Timeout:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class AsyncClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            raise RuntimeError("httpx AsyncClient.get was called without a test stub.")

        def stream(self, *args, **kwargs):
            raise RuntimeError("httpx AsyncClient.stream was called without a test stub.")

    fake_httpx.AsyncClient = AsyncClient
    fake_httpx.Timeout = Timeout
    sys.modules["httpx"] = fake_httpx


MIXED_MODALITY_CATALOG_PATH = Path(__file__).parent / "fixtures" / "openrouter_models_mixed.json"


@pytest.fixture(scope="session")
def mixed_modality_catalog() -> list[dict]:
    """Raw catalog entries, one real entry per output-modality shape OpenRouter serves.

    Captured from the keyless ``GET /api/v1/models?output_modalities=all`` on
    2026-08-28 (see ``_source`` in the file), so the parser and every modality
    guard run against the real entry shapes -- zero-priced video, per-image
    pricing keys, ``context_length: 0`` transcription models, the ``overrides``
    array -- rather than hand-written stand-ins.
    """
    return json.loads(MIXED_MODALITY_CATALOG_PATH.read_text(encoding="utf-8"))["data"]


@pytest.fixture(scope="session")
def mixed_modality_models(mixed_modality_catalog) -> dict[str, ModelInfo]:
    """The same entries run through ``parse_model_info``, keyed by model id."""
    return {entry["id"]: parse_model_info(entry) for entry in mixed_modality_catalog}

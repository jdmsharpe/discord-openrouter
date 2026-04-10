import pytest

pytest.importorskip("discord")

from discord_openrouter.cogs.openrouter.image import _validate_image_model_modalities
from discord_openrouter.util import ModelInfo


def test_validate_image_model_modalities_blocks_missing_image_output():
    model_info = ModelInfo(
        id="openai/text-only",
        name="Text Only",
        input_modalities=["text", "image"],
        output_modalities=["text"],
    )

    error = _validate_image_model_modalities(model_info, requires_image_input=False)

    assert error is not None
    assert "image output" in error


def test_validate_image_model_modalities_blocks_missing_image_input_for_edits():
    model_info = ModelInfo(
        id="openai/image-output-only",
        name="Image Output Only",
        input_modalities=["text"],
        output_modalities=["image"],
    )

    error = _validate_image_model_modalities(model_info, requires_image_input=True)

    assert error is not None
    assert "image input" in error


def test_validate_image_model_modalities_allows_image_editing_models():
    model_info = ModelInfo(
        id="openai/image-editor",
        name="Image Editor",
        input_modalities=["text", "image"],
        output_modalities=["image", "text"],
    )

    assert _validate_image_model_modalities(model_info, requires_image_input=True) is None

import pytest

pytest.importorskip("discord")

from discord_openrouter.cogs.openrouter.video import _validate_video_model_modalities
from discord_openrouter.util import ModelInfo


def test_validate_video_model_modalities_blocks_missing_video_output():
    model_info = ModelInfo(
        id="openai/text-only",
        name="Text Only",
        input_modalities=["text", "image"],
        output_modalities=["text"],
    )

    error = _validate_video_model_modalities(model_info, requires_image_input=False)

    assert error is not None
    assert "video output" in error


def test_validate_video_model_modalities_blocks_missing_image_input_for_reference_images():
    model_info = ModelInfo(
        id="openai/video-output-only",
        name="Video Output Only",
        input_modalities=["text"],
        output_modalities=["video"],
    )

    error = _validate_video_model_modalities(model_info, requires_image_input=True)

    assert error is not None
    assert "image input" in error


def test_validate_video_model_modalities_allows_reference_image_models():
    model_info = ModelInfo(
        id="openai/video-generator",
        name="Video Generator",
        input_modalities=["text", "image"],
        output_modalities=["video"],
    )

    assert _validate_video_model_modalities(model_info, requires_image_input=True) is None

from discord.commands import OptionChoice

REASONING_EFFORT_CHOICES = [
    OptionChoice(name="Minimal", value="minimal"),
    OptionChoice(name="Low", value="low"),
    OptionChoice(name="Medium", value="medium"),
    OptionChoice(name="High", value="high"),
    OptionChoice(name="Extra High", value="xhigh"),
    OptionChoice(name="None", value="none"),
]

MODEL_SCOPE_CHOICES = [
    OptionChoice(name="Conversation only", value="conversation"),
    OptionChoice(name="Channel default only", value="channel"),
    OptionChoice(name="Both conversation and channel", value="both"),
]

IMAGE_ASPECT_RATIO_CHOICES = [
    OptionChoice(name="1:1 (Square)", value="1:1"),
    OptionChoice(name="1:4", value="1:4"),
    OptionChoice(name="1:8", value="1:8"),
    OptionChoice(name="4:1", value="4:1"),
    OptionChoice(name="16:9 (Landscape)", value="16:9"),
    OptionChoice(name="9:16 (Portrait)", value="9:16"),
    OptionChoice(name="4:3", value="4:3"),
    OptionChoice(name="3:4", value="3:4"),
    OptionChoice(name="3:2", value="3:2"),
    OptionChoice(name="2:3", value="2:3"),
    OptionChoice(name="4:5", value="4:5"),
    OptionChoice(name="5:4", value="5:4"),
    OptionChoice(name="8:1", value="8:1"),
    OptionChoice(name="21:9", value="21:9"),
]

IMAGE_SIZE_CHOICES = [
    OptionChoice(name="0.5K", value="0.5K"),
    OptionChoice(name="1K", value="1K"),
    OptionChoice(name="2K", value="2K"),
    OptionChoice(name="4K", value="4K"),
]

MODEL_INPUT_MODALITY_CHOICES = [
    OptionChoice(name="Text Input", value="text"),
    OptionChoice(name="Image Input", value="image"),
    OptionChoice(name="Audio Input", value="audio"),
    OptionChoice(name="Video Input", value="video"),
    OptionChoice(name="File Input", value="file"),
]

MODEL_OUTPUT_MODALITY_CHOICES = [
    OptionChoice(name="Text Output", value="text"),
    OptionChoice(name="Image Output", value="image"),
    OptionChoice(name="Audio Output", value="audio"),
    OptionChoice(name="Embeddings Output", value="embeddings"),
]

PDF_ENGINE_CHOICES = [
    OptionChoice(name="Cloudflare AI", value="cloudflare-ai"),
    OptionChoice(name="Mistral OCR", value="mistral-ocr"),
    OptionChoice(name="Native", value="native"),
]

PROMPT_CACHE_TTL_CHOICES = [
    OptionChoice(name="5 Minutes", value="5m"),
    OptionChoice(name="1 Hour", value="1h"),
]

TTS_FORMAT_CHOICES = [
    OptionChoice(name="MP3", value="mp3"),
    OptionChoice(name="WAV", value="wav"),
    OptionChoice(name="FLAC", value="flac"),
    OptionChoice(name="Opus", value="opus"),
]

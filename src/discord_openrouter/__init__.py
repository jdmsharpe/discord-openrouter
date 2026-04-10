from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bot import build_bot, main
    from .cogs.openrouter.cog import OpenRouterCog

__all__ = ["OpenRouterCog", "build_bot", "main"]


def __getattr__(name: str) -> Any:
    if name in {"build_bot", "main"}:
        from .bot import build_bot, main

        return {"build_bot": build_bot, "main": main}[name]

    if name == "OpenRouterCog":
        from .cogs.openrouter.cog import OpenRouterCog

        return OpenRouterCog

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

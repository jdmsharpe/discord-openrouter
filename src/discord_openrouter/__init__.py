__all__ = ["OpenRouterCog", "build_bot", "main"]


def __getattr__(name: str):
    if name in {"build_bot", "main"}:
        from .bot import build_bot, main

        return {"build_bot": build_bot, "main": main}[name]

    if name == "OpenRouterCog":
        from .cogs.openrouter.cog import OpenRouterCog

        return OpenRouterCog

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

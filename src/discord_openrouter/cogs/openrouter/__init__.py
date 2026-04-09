__all__ = ["OpenRouterCog"]


def __getattr__(name: str):
    if name == "OpenRouterCog":
        from .cog import OpenRouterCog

        return OpenRouterCog

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

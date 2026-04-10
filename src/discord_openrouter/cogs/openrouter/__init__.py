from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cog import OpenRouterCog

__all__ = ["OpenRouterCog"]


def __getattr__(name: str) -> Any:
    if name == "OpenRouterCog":
        from .cog import OpenRouterCog

        return OpenRouterCog

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

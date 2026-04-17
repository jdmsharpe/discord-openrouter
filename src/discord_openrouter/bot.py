"""Thin launcher for the discord-openrouter bot."""

from discord import Bot, Intents

from .cogs.openrouter.cog import OpenRouterCog
from .config import BOT_TOKEN
from .config.auth import validate_required_config
from .logging_setup import configure_logging


def build_bot() -> Bot:
    intents = Intents.default()
    intents.presences = False
    intents.members = True
    intents.message_content = True
    intents.guilds = True
    bot = Bot(intents=intents)
    bot.add_cog(OpenRouterCog(bot=bot))
    return bot


def main() -> None:
    validate_required_config()
    configure_logging()
    bot = build_bot()
    bot.run(BOT_TOKEN)


if __name__ == "__main__":
    main()

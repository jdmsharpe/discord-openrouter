"""Thin repo-local launcher retained for `python src/bot.py`."""

import logging

from discord_openrouter.bot import main

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()

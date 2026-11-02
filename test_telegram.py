
import os
import asyncio
from dotenv import load_dotenv
from telegram import Bot

load_dotenv(".env")
token = os.getenv("BOT_TOKEN")

async def main():
    if not token:
        print("XATO: BOT_TOKEN topilmadi")
        return

    try:
        bot = Bot(token=token)
        me = await bot.get_me()
        print("Telegram ulanishi OK")
        print("Bot username:", me.username)
        await bot.shutdown()
    except Exception as e:
        print("XATO:", type(e).__name__, str(e))

asyncio.run(main())

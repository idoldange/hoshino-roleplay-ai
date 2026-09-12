import discord
from openai import AsyncOpenAI

import config
from database import Database

client_ai = AsyncOpenAI(base_url=config.LM_BASE_URL, api_key=config.LM_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

db = Database(config.DB_PATH)

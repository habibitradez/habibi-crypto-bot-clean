# --- Fallback for environments missing micropip or standard modules ---
try:
    import discord
    from discord.ext import commands, tasks
except ModuleNotFoundError as e:
    print("⚠️ Discord module not found. This code must be run in a Python environment where 'discord.py' is installed.")
    print("Run: pip install discord.py")
    raise e

import requests
import openai
import os
import logging
from dotenv import load_dotenv

# --- LOAD .env CONFIG ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")

openai.api_key = OPENAI_API_KEY

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

logging.basicConfig(level=logging.INFO)

# --- HELPER FUNCTIONS ---
def get_trending_news():
    url = f"https://newsapi.org/v2/top-headlines?category=business&q=crypto&apiKey={NEWSAPI_KEY}"
    res = requests.get(url).json()
    articles = res.get("articles", [])
    if len(articles) >= 2:
        second_news = articles[1]
        return f"**{second_news['title']}**\n{second_news['url']}"
    return "No trending news found."

def get_crypto_memes():
    reddit_url = "https://www.reddit.com/r/cryptomemes/top.json?limit=5&t=day"
    headers = {"User-agent": "HabibiBot"}
    res = requests.get(reddit_url, headers=headers).json()
    memes = res["data"]["children"]
    meme = memes[0]["data"]
    return f"{meme['title']}\nhttps://reddit.com{meme['permalink']}"

def ask_habibi(prompt):
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are Habibi, the Crypto Trading God. Answer with swagger, wisdom, and memes if needed."},
            {"role": "user", "content": prompt},
        ]
    )
    return response["choices"][0]["message"]["content"]

# --- COMMANDS ---
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

@bot.command()
async def news(ctx):
    await ctx.send(get_trending_news())

@bot.command()
async def meme(ctx):
    await ctx.send(get_crypto_memes())

@bot.command()
async def ask(ctx, *, question):
    reply = ask_habibi(question)
    await ctx.send(reply)

# --- RUN BOT ---
bot.run(DISCORD_TOKEN)

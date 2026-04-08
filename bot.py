import discord
from discord.ext import commands
from dotenv import load_dotenv
from ui.embed import error_embed
import asyncio
import os
import db.database as database
from db.migrations import run_migrations
from ui.status import send_status


load_dotenv()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=["z!", "Z!"], intents=intents, help_command=None)

TOKEN = os.getenv("token")
REQUIRED_ROLE_ID = int(os.getenv("REQUIRED_ROLE_ID"))
REQUIRE_ROLE = os.getenv("REQUIRE_ROLE", "true").lower() == "true"
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

@bot.check
async def global_check(ctx):
    # 🔥 OWNER BYPASS (always allowed)
    if ctx.author.id == OWNER_ID:
        return True

    # 🔹 Role system toggle
    if not REQUIRE_ROLE:
        return True

    return any(role.id == REQUIRED_ROLE_ID for role in ctx.author.roles)


# ✅ ONE unified error handler
@bot.event
async def on_command_error(ctx, error):

    # 🔹 Cooldown error
    if isinstance(error, commands.CommandOnCooldown):
        embed = error_embed(
            ctx,
            description=f"Slow down! Try again in **{error.retry_after:.1f}s**"
        )
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(3)
        await msg.delete()
        return

    # 🔹 Role check failure
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ You need the required role to use this bot.")
        return

    # 🔹 Other errors (optional debug)
    raise error

EXCLUDED_FOLDERS = ["ui", "__pycache__"]

EXCLUDED_FOLDERS = ["ui", "__pycache__"]

@bot.event
async def on_ready():

    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name="Mortals chase immortality 👀"
    )
    await bot.change_presence(activity=activity)
    print(f"\n{'='*40}")
    print(f"  Logged in as {bot.user}")
    print(f"{'='*40}\n")

    # Database
    try:
        await database.connect()
        await run_migrations()
        await send_status(bot, "start")
    except Exception as e:
        print(f"  ❌ Database    » Failed | {e}")

    print()

    # Cogs
    success = []
    failed = []
    skipped = []

    for root, dirs, files in os.walk("./cogs"):
        for d in dirs:
            if d in EXCLUDED_FOLDERS and d != "__pycache__":
                folder = os.path.join(root, d).replace("./", "").replace(os.sep, "/")
                print(f"  ⏭️  Skipped    » {folder}")
                skipped.append(d)
        dirs[:] = [d for d in dirs if d not in EXCLUDED_FOLDERS]

        for filename in files:
            if filename.endswith(".py") and filename != "__init__.py":
                path = os.path.relpath(os.path.join(root, filename))
                extension = path.replace(os.sep, ".")[:-3]
                try:
                    await bot.load_extension(extension)
                    print(f"  ✅ Loaded     » {extension}")
                    success.append(extension)
                except Exception as e:
                    print(f"  ❌ Failed     » {extension} | {e}")
                    failed.append(extension)

    await bot.tree.sync()

    print(f"\n{'='*40}")
    print(f"  🗄️  Database    » {'✅ Connected' if database.pool else '❌ Not Connected'}")
    print(f"  📦 Cogs        » ✅ {len(success)} loaded | ❌ {len(failed)} failed | ⏭️  {len(skipped)} skipped")
    print(f"  🌐 Discord     » Slash commands synced")
    print(f"{'='*40}\n")


async def on_close():
    await database.disconnect()
    await send_status(bot, "stop")


bot.run(TOKEN)
import discord
from discord import app_commands
from discord.ext import commands
import os
import tempfile
import shutil

# =========================
# CONFIG
# =========================

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable not set")

GUILD_ID = 1138096902395662436 # Pack Hub Guild ID
OWNER_ID = 899640436300324874 # @drl3x2015 User ID

# =========================
# BOT SETUP
# =========================

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# PER-USER SETTINGS
# =========================

send_in_dm: dict[int, bool] = {}
private_mode: dict[int, bool] = {}

def dm_enabled(user_id: int) -> bool:
    return send_in_dm.get(user_id, False)

def private_enabled(user_id: int) -> bool:
    return private_mode.get(user_id, False)

def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == OWNER_ID

# =========================
# READY
# =========================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    print("Slash commands synced")

# =========================
# OWNER COMMANDS
# =========================

@bot.tree.command(name="resync", description="Owner only: resync slash commands")
async def resync(interaction: discord.Interaction):
    if not is_owner(interaction):
        await interaction.response.send_message(
            "❌ You are not allowed to use this command.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    guild = discord.Object(id=GUILD_ID)
    bot.tree.clear_commands(guild=guild)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)

    await interaction.followup.send(
        "✅ Commands resynced successfully.",
        ephemeral=True
    )

@bot.tree.command(name="sendmessage", description="Owner only: send a message as the bot")
@app_commands.describe(channel="Channel to send the message in", message="Message content")
async def sendmessage(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    message: str
):
    if not is_owner(interaction):
        await interaction.response.send_message(
            "❌ You are not allowed to use this command.",
            ephemeral=True
        )
        return

    await channel.send(message)
    await interaction.response.send_message(
        "✅ Message sent.",
        ephemeral=True
    )

# =========================
# TOGGLES
# =========================

@bot.tree.command(name="toggle", description="Toggle sending files in DMs")
async def toggle(interaction: discord.Interaction):
    uid = interaction.user.id
    send_in_dm[uid] = not dm_enabled(uid)

    await interaction.response.send_message(
        f"📬 Files will now be sent in **{'DMs' if send_in_dm[uid] else 'the channel'}**.",
        ephemeral=True
    )

@bot.tree.command(name="ptoggle", description="Toggle private (ephemeral) output")
async def ptoggle(interaction: discord.Interaction):
    uid = interaction.user.id
    private_mode[uid] = not private_enabled(uid)

    await interaction.response.send_message(
        f"🔒 Private mode is now **{'ON' if private_mode[uid] else 'OFF'}**.",
        ephemeral=True
    )

# =========================
# FILE SEND HELPER
# =========================

async def send_file(
    interaction: discord.Interaction,
    file_path: str,
    filename: str,
    message: str
):
    uid = interaction.user.id
    ephemeral = private_enabled(uid)

    if dm_enabled(uid):
        await interaction.user.send(
            content=message,
            file=discord.File(file_path, filename=filename)
        )
        await interaction.followup.send(
            "📬 File sent to your DMs.",
            ephemeral=True
        )
    else:
        await interaction.followup.send(
            content=message,
            file=discord.File(file_path, filename=filename),
            ephemeral=ephemeral
        )

# =========================
# CONVERT COMMANDS
# =========================

@bot.tree.command(name="convert", description="Convert a resource pack")
@app_commands.describe(
    file="Resource pack zip",
    base_version="Base version (manual)",
    target_version="Target version"
)
async def convert(
    interaction: discord.Interaction,
    file: discord.Attachment,
    base_version: str,
    target_version: str
):
    await interaction.response.defer(ephemeral=private_enabled(interaction.user.id))

    tmp = tempfile.mkdtemp()
    try:
        input_path = os.path.join(tmp, file.filename)
        await file.save(input_path)

        output_name = f"{os.path.splitext(file.filename)[0]}_converted.zip"
        output_path = os.path.join(tmp, output_name)

        shutil.make_archive(output_path.replace(".zip", ""), "zip", tmp)

        await send_file(
            interaction,
            output_path,
            output_name,
            f"✅ Converted from **{base_version} → {target_version}**"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

@bot.tree.command(name="downconvert", description="Downconvert a resource pack")
async def downconvert(
    interaction: discord.Interaction,
    file: discord.Attachment,
    base_version: str,
    target_version: str
):
    await convert(interaction, file, base_version, target_version)

@bot.tree.command(name="modconvert", description="Convert a Minecraft mod (jar)")
@app_commands.describe(
    file="Mod .jar file",
    base_version="Base version",
    target_version="Target version"
)
async def modconvert(
    interaction: discord.Interaction,
    file: discord.Attachment,
    base_version: str,
    target_version: str
):
    await interaction.response.defer(ephemeral=private_enabled(interaction.user.id))

    tmp = tempfile.mkdtemp()
    try:
        input_path = os.path.join(tmp, file.filename)
        await file.save(input_path)

        output_name = f"{os.path.splitext(file.filename)[0]}_converted.jar"
        output_path = os.path.join(tmp, output_name)

        shutil.copy(input_path, output_path)

        await send_file(
            interaction,
            output_path,
            output_name,
            f"✅ Mod converted from **{base_version} → {target_version}**"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

# =========================
# START
# =========================

bot.run(TOKEN)

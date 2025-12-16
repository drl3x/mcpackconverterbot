import discord
from discord import app_commands
from discord.ext import commands
import zipfile
import json
import os
import shutil
import tempfile
import asyncio

# =========================
# KEEP-ALIVE (REPLIT)
# =========================
from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive!"

def run_web():
    print("🌐 Web server starting on port 8080")
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    Thread(target=run_web).start()

# =========================
# CONFIG
# =========================
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    print("❌ TOKEN NOT FOUND (set it in Railway Environment Variables as TOKEN)")
    raise SystemExit

GUILD_ID = 1138096902395662436  # <-- Change to your server ID

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# GLOBAL TOGGLES
# =========================
send_in_dm = False      # /toggle
private_mode = False    # /ptoggle

# =========================
# VERSION DATA
# =========================
PACK_FORMATS = {
    "1.8": 1, "1.9": 2, "1.10": 2, "1.11": 3, "1.12": 3,
    "1.13": 4, "1.14": 4, "1.15": 5, "1.16": 6, "1.17": 7,
    "1.18": 8, "1.19": 9, "1.20": 15, "1.21": 18,
}

FLATTENING_REMAP = {
    "assets/minecraft/textures/blocks": "assets/minecraft/textures/block",
    "assets/minecraft/textures/items": "assets/minecraft/textures/item",
}

TEXTURE_RENAMES = {
    "grass_side.png": "grass_block_side.png",
    "grass_top.png": "grass_block_top.png",
    "stonebrick.png": "stone_bricks.png",
}

VERSION_FOLDER_REMAP = {
    "1.21": {
        "assets/minecraft/models/armor": "assets/minecraft/misc/equipment/netherite",
    }
}

# =========================
# UTILITIES
# =========================
def normalize_version(v: str) -> str:
    return ".".join(v.split(".")[:2])

def update_pack_mcmeta(path, target_version):
    mcmeta = os.path.join(path, "pack.mcmeta")
    if not os.path.exists(mcmeta):
        return
    with open(mcmeta, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("pack", {})
    data["pack"]["pack_format"] = PACK_FORMATS.get(normalize_version(target_version), max(PACK_FORMATS.values()))
    with open(mcmeta, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def apply_flattening(path, report):
    for old, new in FLATTENING_REMAP.items():
        old_path = os.path.join(path, old)
        new_path = os.path.join(path, new)
        if os.path.exists(old_path):
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            shutil.move(old_path, new_path)
            report.append(f"Flattened: {old} → {new}")

def rename_textures(path, report):
    for root, _, files in os.walk(path):
        for file in files:
            if file in TEXTURE_RENAMES:
                old = os.path.join(root, file)
                new = os.path.join(root, TEXTURE_RENAMES[file])
                os.rename(old, new)
                report.append(f"Renamed: {file} → {TEXTURE_RENAMES[file]}")

def apply_folder_remap(path, report, target_version):
    v = normalize_version(target_version)
    for version_key, mappings in VERSION_FOLDER_REMAP.items():
        if v >= version_key:
            for old, new in mappings.items():
                old_path = os.path.join(path, old)
                new_path = os.path.join(path, new)
                if os.path.exists(old_path):
                    os.makedirs(os.path.dirname(new_path), exist_ok=True)
                    shutil.move(old_path, new_path)
                    report.append(f"Remapped folder: {old} → {new}")

def ensure_item_folder(path, report):
    old_item_path = os.path.join(path, "assets/minecraft/textures/items")
    new_item_path = os.path.join(path, "assets/minecraft/textures/item")
    if os.path.exists(old_item_path) and not os.path.exists(new_item_path):
        os.makedirs(os.path.dirname(new_item_path), exist_ok=True)
        shutil.move(old_item_path, new_item_path)
        report.append("Renamed folder: assets/minecraft/textures/items → assets/minecraft/textures/item")

def detect_optifine(path, report):
    if os.path.exists(os.path.join(path, "assets/minecraft/optifine")):
        report.append("⚠ OptiFine detected — manual fixes may be needed")

def convert_pack(src_path, base_version, target_version, original_filename=None):
    tmp = tempfile.mkdtemp()
    report = []

    if zipfile.is_zipfile(src_path):
        with zipfile.ZipFile(src_path, "r") as z:
            z.extractall(tmp)
    else:
        shutil.copytree(src_path, tmp, dirs_exist_ok=True)

    if normalize_version(base_version) < "1.13" <= normalize_version(target_version):
        apply_flattening(tmp, report)

    ensure_item_folder(tmp, report)
    rename_textures(tmp, report)
    apply_folder_remap(tmp, report, target_version)
    detect_optifine(tmp, report)
    update_pack_mcmeta(tmp, target_version)

    with open(os.path.join(tmp, "conversion_report.txt"), "w") as f:
        f.write("\n".join(report))

    if original_filename:
        name, _ = os.path.splitext(original_filename)
    else:
        name = os.path.splitext(os.path.basename(src_path))[0]

    output_filename = f"{name}_converted.zip"
    out_path = os.path.join(tempfile.gettempdir(), output_filename)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(tmp):
            for file in files:
                full = os.path.join(root, file)
                z.write(full, os.path.relpath(full, tmp))

    shutil.rmtree(tmp)
    return out_path, output_filename

# =========================
# DISCORD
# =========================
@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    await bot.tree.sync(guild=guild)
    print(f"Logged in as {bot.user}")

async def send_file(interaction, file_path, filename, base_version=None, target_version=None):
    message = None
    if base_version and target_version:
        message = f"✅ {interaction.user.mention}, the pack has successfully been converted from {base_version} to {target_version}"

    if private_mode:
        await interaction.followup.send(content=message, file=discord.File(file_path, filename=filename), ephemeral=True)
        return

    if send_in_dm:
        try:
            await interaction.user.send(content=message, file=discord.File(file_path, filename=filename))
            await interaction.followup.send("✅ File sent to your DMs.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(content=f"⚠ {interaction.user.mention}, DM failed. Sending here instead.", file=discord.File(file_path, filename=filename))
    else:
        await interaction.followup.send(content=message, file=discord.File(file_path, filename=filename))

# =========================
# SLASH COMMANDS
# =========================
async def handle_convert(interaction: discord.Interaction, pack: discord.Attachment, base_version: str, target_version: str):
    await interaction.response.defer(thinking=True)
    src = tempfile.NamedTemporaryFile(delete=False)
    await pack.save(src.name)
    try:
        # Run the conversion in a separate thread to avoid blocking
        result_path, output_filename = await asyncio.to_thread(convert_pack, src.name, base_version, target_version, pack.filename)
        await send_file(interaction, result_path, output_filename, base_version, target_version)
    finally:
        os.unlink(src.name)

@bot.tree.command(name="convert", description="Upgrade a texture pack")
@app_commands.describe(pack="Upload pack", base_version="Original version", target_version="Target version")
async def convert(interaction: discord.Interaction, pack: discord.Attachment, base_version: str, target_version: str):
    await handle_convert(interaction, pack, base_version, target_version)

@bot.tree.command(name="downconvert", description="Downgrade a texture pack")
@app_commands.describe(pack="Upload pack", base_version="Current version", target_version="Target older version")
async def downconvert(interaction: discord.Interaction, pack: discord.Attachment, base_version: str, target_version: str):
    await handle_convert(interaction, pack, base_version, target_version)

@bot.tree.command(name="toggle", description="Toggle sending files via DMs or channel")
async def toggle(interaction: discord.Interaction):
    global send_in_dm
    send_in_dm = not send_in_dm
    await interaction.response.send_message("✅ Files will now be sent to your DMs" if send_in_dm else "✅ Files will now be sent in the channel", ephemeral=True)

@bot.tree.command(name="ptoggle", description="Toggle private output (only you see the result)")
async def ptoggle(interaction: discord.Interaction):
    global private_mode
    private_mode = not private_mode
    await interaction.response.send_message("🔒 Private mode ON (ephemeral output)" if private_mode else "🔓 Private mode OFF", ephemeral=True)

# =========================
# START
# =========================
keep_alive()
bot.run(TOKEN)

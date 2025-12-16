import discord
from discord import app_commands
from discord.ext import commands
import zipfile
import json
import os
import shutil
import tempfile

# =========================
# CONFIG
# =========================
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    print("❌ TOKEN NOT FOUND (set TOKEN in Railway Variables)")
    raise SystemExit

GUILD_ID = 1138096902395662436

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# GLOBAL TOGGLES
# =========================
send_in_dm = False
private_mode = False

# =========================
# VERSION DATA
# =========================
PACK_FORMATS = {
    "1.8": 1,
    "1.9": 2,
    "1.10": 2,
    "1.11": 3,
    "1.12": 3,
    "1.13": 4,
    "1.14": 4,
    "1.15": 5,
    "1.16": 6,
    "1.17": 7,
    "1.18": 8,
    "1.19": 9,
    "1.20": 15,
    "1.21": 18,
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
    data["pack"]["pack_format"] = PACK_FORMATS.get(
        normalize_version(target_version),
        max(PACK_FORMATS.values())
    )
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
                os.rename(
                    os.path.join(root, file),
                    os.path.join(root, TEXTURE_RENAMES[file])
                )
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
    old_item = os.path.join(path, "assets/minecraft/textures/items")
    new_item = os.path.join(path, "assets/minecraft/textures/item")
    if os.path.exists(old_item) and not os.path.exists(new_item):
        shutil.move(old_item, new_item)
        report.append("Renamed folder: items → item")

def detect_optifine(path, report):
    if os.path.exists(os.path.join(path, "assets/minecraft/optifine")):
        report.append("⚠ OptiFine detected — manual fixes may be required")

def convert_pack(src_path, base_version, target_version, original_filename):
    tmp = tempfile.mkdtemp()
    report = []

    if zipfile.is_zipfile(src_path):
        with zipfile.ZipFile(src_path, "r") as z:
            z.extractall(tmp)

    if normalize_version(base_version) < "1.13" <= normalize_version(target_version):
        apply_flattening(tmp, report)

    ensure_item_folder(tmp, report)
    rename_textures(tmp, report)
    apply_folder_remap(tmp, report, target_version)
    detect_optifine(tmp, report)
    update_pack_mcmeta(tmp, target_version)

    name = os.path.splitext(original_filename)[0]
    output_zip = os.path.join(tempfile.gettempdir(), f"{name}_converted.zip")

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(tmp):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, tmp))

    shutil.rmtree(tmp)
    return output_zip, f"{name}_converted.zip"

# =========================
# DISCORD EVENTS
# =========================
@bot.event
async def on_ready():
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"✅ Logged in as {bot.user}")

async def send_file(interaction, path, filename, base, target):
    message = (
        f"✅ {interaction.user.mention}, the pack has successfully been converted "
        f"from {base} to {target}"
    )

    if private_mode:
        await interaction.followup.send(
            content=message,
            file=discord.File(path, filename),
            ephemeral=True
        )
        return

    if send_in_dm:
        await interaction.user.send(content=message, file=discord.File(path, filename))
        await interaction.followup.send("✅ Sent to your DMs.", ephemeral=True)
    else:
        await interaction.followup.send(content=message, file=discord.File(path, filename))

# =========================
# COMMANDS
# =========================
@bot.tree.command(name="convert")
async def convert(interaction: discord.Interaction, pack: discord.Attachment, base_version: str, target_version: str):
    await interaction.response.defer()
    tmp = tempfile.NamedTemporaryFile(delete=False)
    await pack.save(tmp.name)
    path, name = convert_pack(tmp.name, base_version, target_version, pack.filename)
    await send_file(interaction, path, name, base_version, target_version)

@bot.tree.command(name="downconvert")
async def downconvert(interaction: discord.Interaction, pack: discord.Attachment, base_version: str, target_version: str):
    await convert(interaction, pack, base_version, target_version)

@bot.tree.command(name="toggle")
async def toggle(interaction: discord.Interaction):
    global send_in_dm
    send_in_dm = not send_in_dm
    await interaction.response.send_message(
        f"📬 DM mode {'ON' if send_in_dm else 'OFF'}",
        ephemeral=True
    )

@bot.tree.command(name="ptoggle")
async def ptoggle(interaction: discord.Interaction):
    global private_mode
    private_mode = not private_mode
    await interaction.response.send_message(
        f"🔒 Private mode {'ON' if private_mode else 'OFF'}",
        ephemeral=True
    )

# =========================
# START
# =========================
bot.run(TOKEN)

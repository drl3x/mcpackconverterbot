import discord
from discord import app_commands
from discord.ext import commands
import zipfile
import json
import os
import shutil
import tempfile
import re

# =========================
# CONFIG
# =========================

TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN env var not set")

GUILD_ID = 1138096902395662436

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# PER-USER SETTINGS
# =========================

user_settings = {}  # user_id -> {"dm": bool, "private": bool}

def get_settings(user_id: int):
    if user_id not in user_settings:
        user_settings[user_id] = {"dm": False, "private": False}
    return user_settings[user_id]

# =========================
# VERSION DATA
# =========================

PACK_FORMATS = {
    "1.8": 1, "1.9": 2, "1.10": 2, "1.11": 3, "1.12": 3,
    "1.13": 4, "1.14": 4, "1.15": 5, "1.16": 6,
    "1.17": 7, "1.18": 8, "1.19": 9, "1.20": 15, "1.21": 18
}

def norm(v: str) -> str:
    return ".".join(v.split(".")[:2])

# =========================
# RESOURCE PACK FIXES
# =========================

def update_mcmeta(path, target):
    mcmeta = os.path.join(path, "pack.mcmeta")
    if not os.path.exists(mcmeta):
        return
    with open(mcmeta, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("pack", {})
    data["pack"]["pack_format"] = PACK_FORMATS.get(norm(target), max(PACK_FORMATS.values()))
    with open(mcmeta, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def items_to_item(path, target, report):
    if norm(target) >= "1.19":
        old = os.path.join(path, "assets/minecraft/textures/items")
        new = os.path.join(path, "assets/minecraft/textures/item")
        if os.path.exists(old):
            os.makedirs(new, exist_ok=True)
            for f in os.listdir(old):
                shutil.move(os.path.join(old, f), new)
            shutil.rmtree(old)
            report.append("Converted textures/items → textures/item")

def rewrite_json_paths(path, report):
    for root, _, files in os.walk(path):
        for file in files:
            if not file.endswith(".json"):
                continue
            p = os.path.join(root, file)
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                txt = f.read()

            new_txt = txt
            new_txt = re.sub(
                r"textures/models/armor/",
                "textures/entity/equipment/humanoid/",
                new_txt
            )
            new_txt = re.sub(
                r"textures/gui/",
                "textures/gui/widgets/",
                new_txt
            )

            if new_txt != txt:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(new_txt)
                report.append(f"Rewrote JSON paths: {file}")

def convert_pack(src, base, target, filename):
    tmp = tempfile.mkdtemp()
    report = []

    with zipfile.ZipFile(src) as z:
        z.extractall(tmp)

    items_to_item(tmp, target, report)
    rewrite_json_paths(tmp, report)
    update_mcmeta(tmp, target)

    report_path = os.path.join(tmp, "conversion_report.txt")
    with open(report_path, "w") as f:
        f.write("\n".join(report))

    name, _ = os.path.splitext(filename)
    out_name = f"{name}_converted.zip"
    out_path = os.path.join(tempfile.gettempdir(), out_name)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(tmp):
            for file in files:
                full = os.path.join(root, file)
                z.write(full, os.path.relpath(full, tmp))

    shutil.rmtree(tmp)
    return out_path, out_name, report

# =========================
# MOD LOADER DETECTION
# =========================

def detect_mod_loader(jar_path):
    with zipfile.ZipFile(jar_path) as z:
        names = z.namelist()
        if "fabric.mod.json" in names:
            return "Fabric"
        if "META-INF/mods.toml" in names:
            return "Forge"
        if "META-INF/neoforge.mods.toml" in names:
            return "NeoForge"
    return "Unknown"

# =========================
# FILE DELIVERY
# =========================

async def deliver(interaction, path, name, message):
    settings = get_settings(interaction.user.id)

    if settings["private"]:
        await interaction.followup.send(
            content=message,
            file=discord.File(path, name),
            ephemeral=True
        )
        return

    if settings["dm"]:
        try:
            await interaction.user.send(
                content=message,
                file=discord.File(path, name)
            )
            await interaction.followup.send("✅ Sent to DMs", ephemeral=True)
            return
        except discord.Forbidden:
            pass

    await interaction.followup.send(
        content=message,
        file=discord.File(path, name)
    )

# =========================
# COMMANDS
# =========================

@bot.tree.command(name="convert")
async def convert(interaction, pack: discord.Attachment, base_version: str, target_version: str):
    await interaction.response.defer(thinking=True)
    tmp = tempfile.NamedTemporaryFile(delete=False)
    await pack.save(tmp.name)

    out, name, _ = convert_pack(tmp.name, base_version, target_version, pack.filename)
    msg = f"✅ {interaction.user.mention} converted {base_version} → {target_version}"
    await deliver(interaction, out, name, msg)
    os.unlink(tmp.name)

@bot.tree.command(name="downconvert")
async def downconvert(interaction, pack: discord.Attachment, base_version: str, target_version: str):
    await interaction.response.defer(thinking=True)
    tmp = tempfile.NamedTemporaryFile(delete=False)
    await pack.save(tmp.name)

    out, name, _ = convert_pack(tmp.name, base_version, target_version, pack.filename)
    msg = f"✅ {interaction.user.mention} downgraded {base_version} → {target_version}"
    await deliver(interaction, out, name, msg)
    os.unlink(tmp.name)

@bot.tree.command(name="modconvert")
async def modconvert(interaction, mod: discord.Attachment, base_version: str, target_version: str):
    await interaction.response.defer(thinking=True)
    tmp = tempfile.NamedTemporaryFile(delete=False)
    await mod.save(tmp.name)

    loader = detect_mod_loader(tmp.name)
    name, ext = os.path.splitext(mod.filename)
    out_name = f"{name}_converted{ext}"
    out_path = os.path.join(tempfile.gettempdir(), out_name)
    shutil.copy(tmp.name, out_path)

    msg = f"✅ {interaction.user.mention} | {loader} mod prepared {base_version} → {target_version}"
    await deliver(interaction, out_path, out_name, msg)
    os.unlink(tmp.name)

@bot.tree.command(name="toggle")
async def toggle(interaction):
    s = get_settings(interaction.user.id)
    s["dm"] = not s["dm"]
    await interaction.response.send_message(
        "📩 DM delivery ON" if s["dm"] else "📢 Channel delivery ON",
        ephemeral=True
    )

@bot.tree.command(name="ptoggle")
async def ptoggle(interaction):
    s = get_settings(interaction.user.id)
    s["private"] = not s["private"]
    await interaction.response.send_message(
        "🔒 Private output ON" if s["private"] else "🔓 Private output OFF",
        ephemeral=True
    )

# =========================
# READY
# =========================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    guild = discord.Object(id=GUILD_ID)
    bot.tree.clear_commands(guild=guild)
    await bot.tree.sync(guild=guild)
    print("✅ Commands synced")

bot.run(TOKEN)

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
# CONFIG
# =========================
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    print("❌ TOKEN NOT FOUND (set it in Railway Secrets as TOKEN)")
    raise SystemExit

GUILD_ID = 1138096902395662436  # Your server ID

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
    "1.8": 1, "1.9": 2, "1.10": 2, "1.11": 3, "1.12": 3,
    "1.13": 4, "1.14": 4, "1.15": 5, "1.16": 6, "1.17": 7,
    "1.18": 8, "1.19": 9, "1.20": 15, "1.21": 18,
}

VALID_VERSIONS = list(PACK_FORMATS.keys())

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
        "assets/minecraft/models/armor": "assets/minecraft/textures/entity/equipment/humanoid",
        "assets/minecraft/textures/gui": "assets/minecraft/textures/gui/widgets",
        "assets/minecraft/textures/icons": "assets/minecraft/textures/gui/sprites/hud"
    }
}

# =========================
# UTILITIES
# =========================
def normalize_version(v: str) -> str:
    return ".".join(v.split(".")[:2])

def detect_pack_version(path):
    mcmeta_path = os.path.join(path, "pack.mcmeta")
    if not os.path.exists(mcmeta_path):
        return None
    with open(mcmeta_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            pack_format = data.get("pack", {}).get("pack_format")
            for version, fmt in PACK_FORMATS.items():
                if fmt == pack_format:
                    return version
        except Exception:
            return None
    return None

def auto_target_for_downconvert(base_version):
    versions = sorted(PACK_FORMATS.keys(), key=lambda v: PACK_FORMATS[v])
    try:
        idx = versions.index(normalize_version(base_version))
        if idx > 0:
            return versions[idx - 1]
    except ValueError:
        pass
    return versions[0]

def detect_nested_root(path):
    items = os.listdir(path)
    if len(items) == 1:
        candidate = os.path.join(path, items[0])
        if os.path.isdir(candidate) and (os.path.exists(os.path.join(candidate, "assets")) or os.path.exists(os.path.join(candidate, "fabric.mod.json"))):
            return candidate
    return path

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

def ensure_item_folder(path, report, target_version):
    old_item_path = os.path.join(path, "assets/minecraft/textures/items")
    new_item_path = os.path.join(path, "assets/minecraft/textures/item")
    if os.path.exists(old_item_path):
        os.makedirs(os.path.dirname(new_item_path), exist_ok=True)
        for file in os.listdir(old_item_path):
            src_file = os.path.join(old_item_path, file)
            dst_file = os.path.join(new_item_path, file)
            if os.path.exists(dst_file):
                base, ext = os.path.splitext(file)
                dst_file = os.path.join(new_item_path, f"{base}_converted{ext}")
            shutil.move(src_file, dst_file)
            report.append(f"Moved: {src_file} → {dst_file}")
        try:
            os.rmdir(old_item_path)
        except OSError:
            pass
        report.append("Renamed folder: assets/minecraft/textures/items → assets/minecraft/textures/item")

def detect_optifine(path, report):
    if os.path.exists(os.path.join(path, "assets/minecraft/optifine")):
        report.append("⚠ OptiFine detected — manual fixes may be needed")

def update_json_for_1211(path, report):
    for root, _, files in os.walk(path):
        for file in files:
            if not file.endswith(".json"):
                continue
            full = os.path.join(root, file)
            with open(full, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except Exception:
                    continue
            changed = False
            if "textures" in data:
                for k, v in data["textures"].items():
                    new_val = v.replace("models/armor/", "entity/equipment/humanoid/").replace("gui/", "gui/widgets/").replace("icons/", "gui/sprites/hud/")
                    if new_val != v:
                        data["textures"][k] = new_val
                        changed = True
            if changed:
                with open(full, "w", encoding="utf-8") as out:
                    json.dump(data, out, indent=4)
                report.append(f"Updated JSON refs in {full}")

# =========================
# CONVERSION FUNCTIONS
# =========================
def convert_pack(src_path, base_version, target_version, original_filename=None):
    tmp = tempfile.mkdtemp()
    report = []

    if zipfile.is_zipfile(src_path):
        with zipfile.ZipFile(src_path, "r") as z:
            z.extractall(tmp)
    else:
        shutil.copytree(src_path, tmp, dirs_exist_ok=True)

    root_path = detect_nested_root(tmp)

    if not base_version:
        base_version = detect_pack_version(root_path) or "1.8"

    if normalize_version(base_version) < "1.13" <= normalize_version(target_version):
        apply_flattening(root_path, report)
    ensure_item_folder(root_path, report, target_version)
    rename_textures(root_path, report)
    apply_folder_remap(root_path, report, target_version)
    if normalize_version(target_version) >= "1.21":
        update_json_for_1211(root_path, report)
    detect_optifine(root_path, report)

    output_filename = (os.path.splitext(original_filename)[0] + "_converted.zip") if original_filename else "converted_pack.zip"
    out_path = os.path.join(tempfile.gettempdir(), output_filename)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(root_path):
            for file in files:
                full = os.path.join(root, file)
                z.write(full, os.path.relpath(full, root_path))

    shutil.rmtree(tmp)
    return out_path, output_filename, report

def convert_fabric_mod(jar_path, target_version, original_filename=None):
    tmp = tempfile.mkdtemp()
    report = []

    with zipfile.ZipFile(jar_path, "r") as z:
        z.extractall(tmp)

    root_path = detect_nested_root(tmp)
    fabric_json_path = os.path.join(root_path, "fabric.mod.json")
    if not os.path.exists(fabric_json_path):
        shutil.rmtree(tmp)
        raise ValueError("Not a Fabric mod (fabric.mod.json not found).")

    # Update version
    with open(fabric_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    old_version = data.get("depends", {}).get("minecraft", "unknown")
    data.setdefault("depends", {})["minecraft"] = target_version
    with open(fabric_json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    report.append(f"Updated Fabric mod version: {old_version} → {target_version}")

    # Apply folder renames
    apply_flattening(root_path, report)
    rename_textures(root_path, report)
    apply_folder_remap(root_path, report, target_version)
    ensure_item_folder(root_path, report, target_version)
    detect_optifine(root_path, report)

    output_filename = (os.path.splitext(original_filename)[0] + "_converted.jar") if original_filename else "converted_mod.jar"
    out_path = os.path.join(tempfile.gettempdir(), output_filename)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(root_path):
            for file in files:
                full = os.path.join(root, file)
                z.write(full, os.path.relpath(full, root_path))

    shutil.rmtree(tmp)
    return out_path, output_filename, report

# =========================
# DISCORD HELPERS
# =========================
async def send_file(interaction, file_path, filename, report=None, base_version=None, target_version=None):
    message = f"✅ {interaction.user.mention}, conversion completed"
    if base_version and target_version:
        message += f": {base_version} → {target_version}"

    if private_mode:
        await interaction.followup.send(content=message, file=discord.File(file_path, filename=filename), ephemeral=True)
        if report:
            await interaction.followup.send(content="📄 **Conversion Report:**\n" + "\n".join(report), ephemeral=True)
        return

    if send_in_dm:
        try:
            await interaction.user.send(content=message, file=discord.File(file_path, filename=filename))
            await interaction.followup.send("✅ File sent to your DMs.", ephemeral=True)
            if report:
                await interaction.user.send(content="📄 **Conversion Report:**\n" + "\n".join(report))
        except discord.Forbidden:
            await interaction.followup.send(content=f"⚠ {interaction.user.mention}, DM failed. Sending here instead.", file=discord.File(file_path, filename=filename))
            if report:
                await interaction.followup.send(content="📄 **Conversion Report:**\n" + "\n".join(report), ephemeral=True)
    else:
        await interaction.followup.send(content=message, file=discord.File(file_path, filename=filename))
        if report:
            await interaction.followup.send(content="📄 **Conversion Report:**\n" + "\n".join(report), ephemeral=True)

# =========================
# CONVERSION HANDLER
# =========================
async def handle_conversion(interaction, attachment, target_version=None, base_version=None, downconvert=False, modconvert=False):
    await interaction.response.defer(thinking=True)
    src = tempfile.NamedTemporaryFile(delete=False)
    await attachment.save(src.name)
    try:
        if modconvert:
            result_path, output_filename, report = await asyncio.to_thread(convert_fabric_mod, src.name, target_version, attachment.filename)
            await send_file(interaction, result_path, output_filename, report, base_version="auto-detected", target_version=target_version)
        else:
            if downconvert:
                tmp_dir = tempfile.mkdtemp()
                if zipfile.is_zipfile(src.name):
                    with zipfile.ZipFile(src.name, "r") as z:
                        z.extractall(tmp_dir)
                else:
                    shutil.copytree(src.name, tmp_dir, dirs_exist_ok=True)
                if not base_version:
                    base_version = detect_pack_version(tmp_dir) or "1.8"
                if not target_version:
                    target_version = auto_target_for_downconvert(base_version)
            result_path, output_filename, report = await asyncio.to_thread(convert_pack, src.name, base_version, target_version, attachment.filename)
            await send_file(interaction, result_path, output_filename, report, base_version, target_version)
    finally:
        os.unlink(src.name)

# =========================
# DISCORD EVENTS & COMMANDS
# =========================
@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    await bot.tree.sync(guild=guild)
    print(f"Logged in as {bot.user}")

# Autocomplete for version arguments
async def version_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=v, value=v) for v in VALID_VERSIONS if current in v]

@bot.tree.command(name="convert", description="Upgrade a texture pack")
@app_commands.describe(pack="Upload pack", target_version="Target version", base_version="Original version (optional)")
@app_commands.autocomplete(target_version=version_autocomplete)
async def convert(interaction: discord.Interaction, pack: discord.Attachment, target_version: str, base_version: str = None):
    await handle_conversion(interaction, pack, target_version, base_version, downconvert=False, modconvert=False)

@bot.tree.command(name="downconvert", description="Downgrade a texture pack")
@app_commands.describe(pack="Upload pack", target_version="Target older version (optional)", base_version="Current version (optional)")
@app_commands.autocomplete(target_version=version_autocomplete)
async def downconvert(interaction: discord.Interaction, pack: discord.Attachment, target_version: str = None, base_version: str = None):
    await handle_conversion(interaction, pack, target_version, base_version, downconvert=True, modconvert=False)

@bot.tree.command(name="modconvert", description="Convert Fabric mods to target Minecraft version")
@app_commands.describe(mod="Upload Fabric mod .jar", target_version="Target Minecraft version")
@app_commands.autocomplete(target_version=version_autocomplete)
async def modconvert(interaction: discord.Interaction, mod: discord.Attachment, target_version: str):
    await handle_conversion(interaction, mod, target_version=target_version, modconvert=True)

@bot.tree.command(name="toggle", description="Toggle sending files via DMs or channel")
async def toggle(interaction: discord.Interaction):
    global send_in_dm
    send_in_dm = not send_in_dm
    await interaction.response.send_message("✅ Files will now be sent to your DMs" if send_in_dm else "✅ Files will now be sent in the channel", ephemeral=True)

@bot.tree.command(name="ptoggle", description="Toggle private output (ephemeral) only for you")
async def ptoggle(interaction: discord.Interaction):
    global private_mode
    private_mode = not private_mode
    await interaction.response.send_message("🔒 Private mode ON (ephemeral output)" if private_mode else "🔓 Private mode OFF", ephemeral=True)

# =========================
# START BOT
# =========================
bot.run(TOKEN)

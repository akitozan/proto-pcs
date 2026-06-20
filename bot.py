import discord
from discord.ext import commands
from discord import app_commands
import random, re, asyncio
from config import *
from storage import *
from storage import fetch_npc_sheet

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ── Helpers ────────────────────────────────────────────────────────────────

def is_prts(member: discord.Member) -> bool:
    return any(r.name == PRTS_ROLE for r in member.roles)

def roll_dice(notation: str) -> tuple[list[int], int]:
    """Parse XdY+Z, return (rolls, total)"""
    notation = notation.strip().lower().replace(" ", "")
    m = re.match(r"(\d*)d(\d+)([+-]\d+)?", notation)
    if not m:
        raise ValueError(f"Invalid dice: {notation}")
    count = int(m.group(1)) if m.group(1) else 1
    sides = int(m.group(2))
    bonus = int(m.group(3)) if m.group(3) else 0
    rolls = [random.randint(1, sides) for _ in range(count)]
    return rolls, sum(rolls) + bonus

def parse_bonus(s: str) -> int | None:
    if not s or not s.strip(): return 0
    s = s.replace(" ", "")
    if not re.match(r"^[+\-]?\d+([+\-]\d+)*$", s): return None
    try: return eval(s, {"__builtins__": {}})
    except: return None

def resolve_char(user_id: int, char_query: str | None):
    """Returns (char_name, char_data, error_embed)"""
    if char_query:
        result, data = find_char(user_id, char_query)
        if result is None:
            return None, None, discord.Embed(
                description=f"[ ERROR ] — ไม่พบตัวละครที่ตรงกับ `{char_query}` ในระบบ",
                color=COLOR_ERROR)
        if isinstance(result, list):
            names = "\n".join(f"`{i+1}.` {n}" for i,n in enumerate(result))
            return None, None, discord.Embed(
                description=f"[ AMBIGUOUS ] — พบตัวละครที่ตรงกันหลายตัว\n{names}\nกรุณาระบุชื่อให้ชัดขึ้น",
                color=COLOR_WARN)
        return result, data, None
    else:
        name, data = get_default_char(user_id)
        if not name:
            return None, None, discord.Embed(
                description="[ ERROR ] — ไม่มีตัวละคร Default ในระบบ\nกรุณาใช้ `/switch char:ชื่อ` เพื่อตั้ง Default ก่อนใช้คำสั่งนี้",
                color=COLOR_ERROR)
        return name, data, None

# initiative state (in-memory)
initiative_active = {}  # guild_id -> {char_name: roll}

# ── /set-char ──────────────────────────────────────────────────────────────

@tree.command(name="set-char", description="ลงทะเบียนตัวละครจาก Google Sheets")
@app_commands.describe(
    char="ชื่อ Sheet tab ของตัวละคร (partial match ได้)",
    force="true = ลงทะเบียนใหม่ทับ รีเซต HP/AP ด้วย"
)
async def set_char(interaction: discord.Interaction, char: str, force: bool = False):
    await interaction.response.defer(ephemeral=True)

    # Fetch from Sheets FIRST to get real tab name
    sheets_offline = False
    char_data = None
    from_npc_sheet = False

    if is_prts(interaction.user):
        # PRTS: ลอง Player Sheet ก่อน ถ้าไม่เจอค่อยลอง NPC Sheet
        char_data = fetch_sheet(char)
        if char_data is None:
            npc_data = fetch_npc_sheet(char)
            if npc_data:
                char_data = npc_data
                from_npc_sheet = True
    else:
        # ไม่ใช่ PRTS: ดึงแค่ Player Sheet
        char_data = fetch_sheet(char)

    # ถ้า PRTS และยังหาไม่เจอ → ลอง list tabs เพื่อเช็ค ambiguous
    # แต่ทำเฉพาะเมื่อรู้ว่า Sheets ยัง online (fetch_sheet ล้มเพราะหาไม่เจอ ไม่ใช่ล่ม)
    if is_prts(interaction.user) and char_data is None:
        try:
            gc = get_gc()
            q = char.lower()
            sh_p = gc.open_by_key(SHEET_ID)
            player_tabs = [ws.title for ws in sh_p.worksheets() if ws.title.lower().startswith(q)]
            npc_tabs = []
            if NPC_SHEET_ID:
                sh_n = gc.open_by_key(NPC_SHEET_ID)
                npc_tabs = [ws.title for ws in sh_n.worksheets() if ws.title.lower().startswith(q)]
            all_tabs = player_tabs + npc_tabs
            if len(all_tabs) > 1:
                names = "\n".join(f"`{i+1}.` {n}" for i,n in enumerate(all_tabs))
                embed = discord.Embed(
                    description=f"[ AMBIGUOUS ] — พบตัวละครที่ตรงกันหลายตัว\n{names}\nกรุณาระบุชื่อให้ชัดขึ้น",
                    color=COLOR_WARN)
                return await interaction.followup.send(embed=embed, ephemeral=True)
        except:
            # Sheets ล่ม → ข้ามไป fallback offline เลย
            pass

    if char_data is None:
        # Fallback offline
        existing_result, existing_data = find_char(interaction.user.id, char)
        if existing_result and not isinstance(existing_result, list):
            char_data = existing_data
            sheets_offline = True
        else:
            embed = discord.Embed(
                description=f"[ ERROR ] — ไม่พบ Sheet tab ชื่อ `{char}` ในระบบ หรือ Google Sheets ไม่ตอบสนอง",
                color=COLOR_ERROR)
            return await interaction.followup.send(embed=embed, ephemeral=True)

    # Now check if char already exists (by real name from sheets)
    real_name = char_data["name"]
    existing_char = get_char(interaction.user.id, real_name)

    if existing_char and not force and not sheets_offline:
        embed = discord.Embed(
            description=(
                f"[ WARNING ] — **{real_name}** มีข้อมูลในระบบอยู่แล้ว\n"
                f"หากต้องการอัปเดตข้อมูลจากชีท ใช้ `/sync char:{char}`\n"
                f"หากต้องการลงทะเบียนใหม่ทับ ใช้ `/set-char char:{char} force:true`\n"
                f"⚠️ `force:true` จะรีเซต HP/AP ทั้งหมดด้วย"
            ),
            color=COLOR_WARN
        )
        return await interaction.followup.send(embed=embed, ephemeral=True)

    # Preserve HP/AP if not force and char exists
    if existing_char and not force:
        char_data["current_hp"] = existing_char["current_hp"]
        char_data["current_ap"] = existing_char["current_ap"]

    update_char(interaction.user.id, real_name, char_data)

    # Set default if first char
    user = get_user(interaction.user.id)
    if not user.get("default"):
        set_default(interaction.user.id, char_data["name"])

    chars = list_chars(interaction.user.id)
    embed = discord.Embed(title="[ UNIT CONFIG COMPLETE ]",
                          description="บันทึกข้อมูลโอเปอเรเตอร์เรียบร้อยแล้ว",
                          color=COLOR_INFO)
    if sheets_offline:
        embed.description += "\n⚠️ Google Sheets ไม่ตอบสนอง ใช้ข้อมูลที่ sync ไว้ล่าสุด ข้อมูลอาจไม่เป็นปัจจุบัน"
    embed.add_field(name="โอเปอเรเตอร์", value=char_data["name"], inline=True)
    embed.add_field(name="Class", value=char_data["class"], inline=True)
    embed.add_field(name="Race", value=char_data["race"], inline=True)
    embed.add_field(name="❤️ HP", value=f"{char_data['current_hp']} / {char_data['max_hp']}", inline=True)
    embed.add_field(name="✨ AP", value=f"{char_data['current_ap']} / {char_data['max_ap']}", inline=True)
    embed.add_field(name="🛡️ AC", value=str(char_data["ac"]), inline=True)
    embed.set_footer(text=f"Operator: {interaction.user.display_name}")

    if len(chars) > 1:
        embed.add_field(name="\u200b",
                        value="ℹ️ ถ้ามีหลายตัวละคร ใช้ `/switch char:ชื่อ` เพื่อเปลี่ยน default",
                        inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)

    # ถ้าเป็น PRTS + มาจาก Player Sheet → ถามว่า NPC หรือ Player
    # ถ้ามาจาก NPC Sheet → is_npc:true อัตโนมัติ ไม่ถาม
    if is_prts(interaction.user) and not from_npc_sheet and not sheets_offline:
        npc_embed = discord.Embed(
            description=f"[ PRTS DETECTED ] — **{char_data['name']}** เป็น NPC หรือ Player?",
            color=COLOR_WARN)
        view = NPCSelectView(interaction.user.id, char_data["name"])
        await interaction.followup.send(embed=npc_embed, view=view, ephemeral=True)

# ── NPC / Player Select View ──────────────────────────────────────────────

class NPCSelectView(discord.ui.View):
    def __init__(self, user_id: int, char_name: str):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.char_name = char_name

    @discord.ui.button(label="👤 Player", style=discord.ButtonStyle.primary)
    async def is_player(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        cd = get_char(self.user_id, self.char_name)
        if "is_npc" in cd: del cd["is_npc"]
        update_char(self.user_id, self.char_name, cd)
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(description=f"[ OK ] — **{self.char_name}** บันทึกเป็น **Player** แล้ว", color=COLOR_INFO),
            view=None)

    @discord.ui.button(label="🤖 NPC", style=discord.ButtonStyle.secondary)
    async def is_npc(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        cd = get_char(self.user_id, self.char_name)
        cd["is_npc"] = True
        update_char(self.user_id, self.char_name, cd)
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(description=f"[ OK ] — **{self.char_name}** บันทึกเป็น **NPC** แล้ว", color=COLOR_INFO),
            view=None)


# ── /sync ──────────────────────────────────────────────────────────────────

@tree.command(name="sync", description="อัปเดตข้อมูลตัวละครจาก Google Sheets (ไม่แตะ HP/AP)")
@app_commands.describe(char="ชื่อตัวละคร (partial match ได้)")
async def sync_cmd(interaction: discord.Interaction, char: str = None):
    await interaction.response.defer(ephemeral=True)
    char_name, char_data, err = resolve_char(interaction.user.id, char)
    if err: return await interaction.followup.send(embed=err, ephemeral=True)

    sheet_tab = char_data.get("sheet_tab") or ""
    is_npc = char_data.get("is_npc", False)
    fetch_fn = fetch_npc_sheet if is_npc else fetch_sheet
    sheet_label = "NPC Sheet" if is_npc else "Player Sheet"
    fail_reason = None

    # ถ้า sheet_tab เป็น partial query เก่า ให้ลอง fetch ด้วยชื่อตัวละครแทน
    new_data = None
    if sheet_tab:
        new_data = fetch_fn(sheet_tab)
        if new_data is None:
            fail_reason = f"หาแท็บ `{sheet_tab}` ไม่เจอใน {sheet_label} หรือ Google Sheets ไม่ตอบสนอง"

    if new_data is None:
        # ลอง fetch ด้วยชื่อตัวละครจริง
        new_data = fetch_fn(char_name)
        if new_data is None and fail_reason is None:
            fail_reason = f"หาแท็บ `{char_name}` ไม่เจอใน {sheet_label} หรือ Google Sheets ไม่ตอบสนอง"

    offline = False
    if new_data is None:
        offline = True
        new_data = char_data

    # Preserve current HP/AP และข้อมูลที่ไม่ควรถูก sync ทับ
    new_data["current_hp"] = char_data["current_hp"]
    new_data["current_ap"] = char_data["current_ap"]
    new_data["sheet_tab"] = sheet_tab
    # preserve flags and runtime data
    if char_data.get("is_npc"): new_data["is_npc"] = True
    if char_data.get("temp_hp"): new_data["temp_hp"] = char_data["temp_hp"]
    if char_data.get("ranged"): new_data["ranged"] = char_data["ranged"]

    # If max changed, adjust current proportionally (don't reset)
    if new_data["max_hp"] != char_data["max_hp"]:
        new_data["current_hp"] = min(char_data["current_hp"], new_data["max_hp"])
    if new_data["max_ap"] != char_data["max_ap"]:
        new_data["current_ap"] = min(char_data["current_ap"], new_data["max_ap"])

    update_char(interaction.user.id, char_name, new_data)

    embed = discord.Embed(title="[ SYNC COMPLETE ]", color=COLOR_INFO)
    if offline:
        embed.description = f"⚠️ Sync ไม่สำเร็จ ใช้ข้อมูลที่ sync ไว้ล่าสุดแทน\n**สาเหตุ:** {fail_reason}"
    else:
        embed.description = f"อัปเดตข้อมูล **{char_name}** จาก Google Sheets แล้ว"
    embed.add_field(name="💪 STR", value=fmt_stat(new_data["str"]), inline=True)
    embed.add_field(name="🏃 DEX", value=fmt_stat(new_data["dex"]), inline=True)
    embed.add_field(name="🧠 INT", value=fmt_stat(new_data["int"]), inline=True)
    embed.add_field(name="🌿 WIS", value=fmt_stat(new_data["wis"]), inline=True)
    embed.add_field(name="HP/AP", value=f"ไม่เปลี่ยนแปลง ({new_data['current_hp']}/{new_data['max_hp']} | {new_data['current_ap']}/{new_data['max_ap']})", inline=False)
    embed.set_footer(text=f"Operator: {interaction.user.display_name}")
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /switch ────────────────────────────────────────────────────────────────

@tree.command(name="switch", description="เปลี่ยนตัวละคร default")
@app_commands.describe(char="ชื่อตัวละคร (partial match ได้)")
async def switch_cmd(interaction: discord.Interaction, char: str):
    result, data = find_char(interaction.user.id, char)
    if result is None:
        embed = discord.Embed(
            description=f"[ ERROR ] — ไม่พบตัวละครที่ตรงกับ `{char}` ในระบบ",
            color=COLOR_ERROR)
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    if isinstance(result, list):
        names = "\n".join(f"`{i+1}.` {n}" for i,n in enumerate(result))
        embed = discord.Embed(
            description=f"[ AMBIGUOUS ] — พบตัวละครที่ตรงกันหลายตัว\n{names}\nกรุณาระบุชื่อให้ชัดขึ้น",
            color=COLOR_WARN)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    old_name, _ = get_default_char(interaction.user.id)
    set_default(interaction.user.id, result)

    embed = discord.Embed(title="[ DEFAULT UPDATED ]", color=COLOR_INFO)
    embed.add_field(name="ก่อนหน้า", value=old_name or "—", inline=True)
    embed.add_field(name="ปัจจุบัน", value=result, inline=True)
    embed.set_footer(text=f"Operator: {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── /delete-char ───────────────────────────────────────────────────────────

class DeleteConfirmView(discord.ui.View):
    def __init__(self, user_id: int, char_name: str, target_user: discord.Member = None):
        super().__init__(timeout=30)
        self.user_id = user_id
        self.char_name = char_name
        self.target_user = target_user

    @discord.ui.button(label="✅ ยืนยัน", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = get_user(self.user_id)
        was_default = user.get("default") == self.char_name
        delete_char(self.user_id, self.char_name)
        self.stop()

        desc = f"ลบ **{self.char_name}** ออกจากระบบแล้ว"
        if self.target_user:
            desc += f"\n[ PRTS ACTION ] — ดำเนินการโดย: {interaction.user.mention}"
        embed = discord.Embed(title="[ COMPLETE ]", description=desc, color=COLOR_INFO)
        if was_default:
            embed.add_field(
                name="\u200b",
                value="[ WARNING ] — ไม่มีตัวละคร Default อยู่ในระบบแล้ว\nกรุณาใช้ `/switch char:ชื่อ` เพื่อตั้ง Default ใหม่",
                inline=False)
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="❌ ยกเลิก", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(description="[ CANCELLED ] — ยกเลิกการลบแล้ว", color=COLOR_INFO),
            view=None)

@tree.command(name="delete-char", description="ลบตัวละครออกจากระบบ")
@app_commands.describe(char="ชื่อตัวละคร (partial match ได้)", user="(PRTS) ระบุถ้าจะลบของคนอื่น")
async def delete_char_cmd(interaction: discord.Interaction, char: str, user: discord.Member = None):
    target_user = user if user and is_prts(interaction.user) else None
    target_id = target_user.id if target_user else interaction.user.id

    result, _ = find_char(target_id, char)
    if result is None:
        embed = discord.Embed(description=f"[ ERROR ] — ไม่พบตัวละครที่ตรงกับ `{char}`", color=COLOR_ERROR)
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    if isinstance(result, list):
        names = "\n".join(f"`{i+1}.` {n}" for i,n in enumerate(result))
        embed = discord.Embed(description=f"[ AMBIGUOUS ]\n{names}\nกรุณาระบุชื่อให้ชัดขึ้น", color=COLOR_WARN)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    desc = f"ลบ **{result}** ออกจากระบบ\nข้อมูล HP/AP และ default จะหายถาวร"
    if target_user:
        desc = f"[ WARNING ] — **{result}** ไม่ใช่ตัวละครของคุณ\nกำลังจะลบ **{result}** (เจ้าของ: {target_user.mention}) ออกจากระบบ"

    embed = discord.Embed(title="[ WARNING ] — กำลังจะลบตัวละคร", description=desc, color=COLOR_FUMBLE)
    view = DeleteConfirmView(target_id, result, target_user)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# ── /mystats ───────────────────────────────────────────────────────────────

@tree.command(name="mystats", description="ดู stat ทั้งหมดของตัวละคร")
@app_commands.describe(char="ชื่อตัวละคร (ไม่ใส่ = default)")
async def mystats(interaction: discord.Interaction, char: str = None):
    char_name, cd, err = resolve_char(interaction.user.id, char)
    if err: return await interaction.response.send_message(embed=err, ephemeral=True)

    embed = discord.Embed(title=f"[ OPERATOR STATUS ] — {cd['name']}", color=COLOR_INFO)
    embed.add_field(name="Class", value=cd["class"], inline=True)
    embed.add_field(name="Race", value=cd["race"], inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name="❤️ HP", value=f"{cd['current_hp']} / {cd['max_hp']}", inline=True)
    embed.add_field(name="✨ AP", value=f"{cd['current_ap']} / {cd['max_ap']}", inline=True)
    embed.add_field(name="🛡️ AC", value=str(cd["ac"]), inline=True)
    embed.add_field(name="💨 SPD", value=str(cd["spd"]), inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    stats_line = f"💪 STR {fmt_stat(cd['str'])}  🏃 DEX {fmt_stat(cd['dex'])}  🧠 INT {fmt_stat(cd['int'])}  🌿 WIS {fmt_stat(cd['wis'])}"
    embed.add_field(name="[ CORE STATS ]", value=stats_line, inline=False)

    # Weapons
    wpn_lines = []
    for w in cd.get("weapons", []):
        icon = "⚔️" if w.get("primary") else "🗡️"
        label = "(Primary)" if w.get("primary") else "(Sub)"
        line = f"{icon} {w['slot']}. {w['name']} {label}"
        if w.get("atk_bonus"): line += f"  ATK Bonus: {fmt_stat(w['atk_bonus'])}"
        if not w.get("primary") and w.get("damage"): line += f"  {w['damage']}"
        wpn_lines.append(line)
    if not wpn_lines: wpn_lines = ["—"]
    embed.add_field(name="[ WEAPONS ]", value="\n".join(wpn_lines), inline=False)

    # Accessories
    acc_lines = [f"◈ {a['slot']}. {a['name']} — {a['effect']}" for a in cd.get("accessories", [])] or ["—"]
    embed.add_field(name="[ ACCESSORIES ]", value="\n".join(acc_lines), inline=False)

    # Modifiers
    mod_lines = [f"• {m['name']} → {m['note']}" for m in cd.get("modifiers", [])] or ["—"]
    embed.add_field(name="[ MODIFIER ]", value="\n".join(mod_lines), inline=False)

    embed.set_footer(text=f"Operator: {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

# ── /stat-update ───────────────────────────────────────────────────────────

class AddHPModal(discord.ui.Modal, title="Add HP"):
    amount = discord.ui.TextInput(label="จำนวนที่ต้องการเพิ่ม", placeholder="เช่น 3", required=True)
    def __init__(self, user_id, char_name, view):
        super().__init__(); self.user_id=user_id; self.char_name=char_name; self.sv=view
    async def on_submit(self, interaction):
        try:
            amt = int(self.amount.value)
            if amt <= 0: raise ValueError
        except:
            return await interaction.response.send_message(embed=discord.Embed(description="[ ERROR ] — กรุณากรอกตัวเลขที่มากกว่า 0", color=COLOR_ERROR), ephemeral=True)
        cd = get_char(self.user_id, self.char_name)
        cd["current_hp"] = min(cd["current_hp"] + amt, cd["max_hp"])
        update_char(self.user_id, self.char_name, cd)
        embed = build_stat_embed(self.char_name, cd)
        await interaction.response.edit_message(embeds=[embed, build_panel_embed(interaction.user)], view=self.sv)

class CutHPModal(discord.ui.Modal, title="Cut HP"):
    amount = discord.ui.TextInput(label="จำนวนที่ต้องการตัด", placeholder="เช่น 3", required=True)
    def __init__(self, user_id, char_name, view):
        super().__init__(); self.user_id=user_id; self.char_name=char_name; self.sv=view
    async def on_submit(self, interaction):
        try:
            amt = int(self.amount.value)
            if amt <= 0: raise ValueError
        except:
            return await interaction.response.send_message(embed=discord.Embed(description="[ ERROR ] — กรุณากรอกตัวเลขที่มากกว่า 0", color=COLOR_ERROR), ephemeral=True)
        cd = get_char(self.user_id, self.char_name)
        # หัก Temp HP ก่อน แล้วค่อยหัก HP จริง
        temp = cd.get("temp_hp", 0)
        if temp > 0:
            if amt <= temp:
                cd["temp_hp"] = temp - amt
                amt = 0
            else:
                amt -= temp
                cd["temp_hp"] = 0
        if amt > 0:
            cd["current_hp"] = max(cd["current_hp"] - amt, 0)
        update_char(self.user_id, self.char_name, cd)
        embed = build_stat_embed(self.char_name, cd)
        await interaction.response.edit_message(embeds=[embed, build_panel_embed(interaction.user)], view=self.sv)

class AddAPModal(discord.ui.Modal, title="Add AP"):
    amount = discord.ui.TextInput(label="จำนวนที่ต้องการเพิ่ม", placeholder="เช่น 5", required=True)
    def __init__(self, user_id, char_name, view):
        super().__init__(); self.user_id=user_id; self.char_name=char_name; self.sv=view
    async def on_submit(self, interaction):
        try:
            amt = int(self.amount.value)
            if amt <= 0: raise ValueError
        except:
            return await interaction.response.send_message(embed=discord.Embed(description="[ ERROR ] — กรุณากรอกตัวเลขที่มากกว่า 0", color=COLOR_ERROR), ephemeral=True)
        cd = get_char(self.user_id, self.char_name)
        cd["current_ap"] = min(cd["current_ap"] + amt, cd["max_ap"])
        update_char(self.user_id, self.char_name, cd)
        embed = build_stat_embed(self.char_name, cd)
        await interaction.response.edit_message(embeds=[embed, build_panel_embed(interaction.user)], view=self.sv)

class CutAPModal(discord.ui.Modal, title="Cut AP"):
    amount = discord.ui.TextInput(label="จำนวนที่ต้องการตัด", placeholder="เช่น 5", required=True)
    def __init__(self, user_id, char_name, view):
        super().__init__(); self.user_id=user_id; self.char_name=char_name; self.sv=view
    async def on_submit(self, interaction):
        try:
            amt = int(self.amount.value)
            if amt <= 0: raise ValueError
        except:
            return await interaction.response.send_message(embed=discord.Embed(description="[ ERROR ] — กรุณากรอกตัวเลขที่มากกว่า 0", color=COLOR_ERROR), ephemeral=True)
        cd = get_char(self.user_id, self.char_name)
        cd["current_ap"] = max(cd["current_ap"] - amt, 0)
        update_char(self.user_id, self.char_name, cd)
        embed = build_stat_embed(self.char_name, cd)
        await interaction.response.edit_message(embeds=[embed, build_panel_embed(interaction.user)], view=self.sv)

def build_stat_embed(char_name, cd):
    embed = discord.Embed(title=f"[ UNIT STATUS ] — {cd['name']}", color=COLOR_INFO)
    embed.add_field(name="🛡️ AC", value=f"[ {cd['ac']} ]", inline=False)
    embed.add_field(name="❤️ Health Point", value=f"{cd['current_hp']} / {cd['max_hp']}", inline=True)
    embed.add_field(name="✨ Arts Points", value=f"{cd['current_ap']} / {cd['max_ap']}", inline=True)
    temp_hp = cd.get("temp_hp", 0)
    if temp_hp and temp_hp > 0:
        embed.add_field(name="💛 Temp HP", value=str(temp_hp), inline=True)
        embed.set_footer(text="มี Temp HP อยู่ — ใช้ /temp-hp เพื่อแก้ไข หรือ /temp-hp clear:true เพื่อลบ")
    return embed

def build_panel_embed(user):
    return discord.Embed(description=f"[ CONTROL PANEL ] — Operator: {user.mention}", color=COLOR_INFO)

class StatView(discord.ui.View):
    def __init__(self, owner_id, char_name):
        super().__init__(timeout=3600)  # 1 ชั่วโมง
        self.owner_id = owner_id
        self.char_name = char_name

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        await interaction.response.send_message(
            embed=discord.Embed(
                description="[ ERROR ] — แผงนี้หมดอายุแล้ว กรุณาใช้ `/stat-update` ใหม่อีกครั้ง",
                color=COLOR_ERROR),
            ephemeral=True)

    async def check_owner(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=discord.Embed(description=f"[ ACCESS DENIED ] — {interaction.user.mention} : แผงควบคุมนี้เป็นของโอเปอเรเตอร์ท่านอื่น ไม่อนุญาตให้เข้าถึง", color=COLOR_ERROR),
                ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Add HP", style=discord.ButtonStyle.success, emoji="⬆️", row=0)
    async def add_hp(self, interaction, button):
        if not await self.check_owner(interaction): return
        await interaction.response.send_modal(AddHPModal(self.owner_id, self.char_name, self))

    @discord.ui.button(label="Cut HP", style=discord.ButtonStyle.danger, emoji="⬇️", row=0)
    async def cut_hp(self, interaction, button):
        if not await self.check_owner(interaction): return
        await interaction.response.send_modal(CutHPModal(self.owner_id, self.char_name, self))

    @discord.ui.button(label="Reset HP", style=discord.ButtonStyle.primary, emoji="🔄", row=0)
    async def reset_hp(self, interaction, button):
        if not await self.check_owner(interaction): return
        cd = get_char(self.owner_id, self.char_name)
        cd["current_hp"] = cd["max_hp"]
        update_char(self.owner_id, self.char_name, cd)
        await interaction.response.edit_message(embeds=[build_stat_embed(self.char_name, cd), build_panel_embed(interaction.user)], view=self)

    @discord.ui.button(label="Add AP", style=discord.ButtonStyle.success, emoji="⬆️", row=1)
    async def add_ap(self, interaction, button):
        if not await self.check_owner(interaction): return
        await interaction.response.send_modal(AddAPModal(self.owner_id, self.char_name, self))

    @discord.ui.button(label="Cut AP", style=discord.ButtonStyle.danger, emoji="⬇️", row=1)
    async def cut_ap(self, interaction, button):
        if not await self.check_owner(interaction): return
        await interaction.response.send_modal(CutAPModal(self.owner_id, self.char_name, self))

    @discord.ui.button(label="Reset AP", style=discord.ButtonStyle.primary, emoji="🔄", row=1)
    async def reset_ap(self, interaction, button):
        if not await self.check_owner(interaction): return
        cd = get_char(self.owner_id, self.char_name)
        cd["current_ap"] = cd["max_ap"]
        update_char(self.owner_id, self.char_name, cd)
        await interaction.response.edit_message(embeds=[build_stat_embed(self.char_name, cd), build_panel_embed(interaction.user)], view=self)

@tree.command(name="stat-update", description="เปิดแผงควบคุม HP / AP")
@app_commands.describe(char="ชื่อตัวละคร (ไม่ใส่ = default)")
async def stat_update(interaction: discord.Interaction, char: str = None):
    char_name, cd, err = resolve_char(interaction.user.id, char)
    if err: return await interaction.response.send_message(embed=err, ephemeral=True)
    view = StatView(interaction.user.id, char_name)
    await interaction.response.send_message(embeds=[build_stat_embed(char_name, cd), build_panel_embed(interaction.user)], view=view)

# ── /temp-hp ───────────────────────────────────────────────────────────────

class TempHPReplaceView(discord.ui.View):
    def __init__(self, user_id: int, char_name: str, old_val: int, new_val: int):
        super().__init__(timeout=30)
        self.user_id = user_id
        self.char_name = char_name
        self.old_val = old_val
        self.new_val = new_val

    @discord.ui.button(label="🛡️ เก็บอันเดิม", style=discord.ButtonStyle.secondary)
    async def keep_old(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ACCESS DENIED ]", color=COLOR_ERROR), ephemeral=True)
        self.stop()
        embed = discord.Embed(
            description=f"[ TEMP HP ] — เก็บ Temp HP เดิมไว้ที่ **{self.old_val}**",
            color=COLOR_INFO)
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="🔄 เปลี่ยนเป็นใหม่", style=discord.ButtonStyle.primary)
    async def use_new(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ACCESS DENIED ]", color=COLOR_ERROR), ephemeral=True)
        cd = get_char(self.user_id, self.char_name)
        cd["temp_hp"] = self.new_val
        update_char(self.user_id, self.char_name, cd)
        self.stop()
        embed = discord.Embed(
            description=f"[ TEMP HP ] — เปลี่ยน Temp HP เป็น **{self.new_val}** แล้ว",
            color=COLOR_INFO)
        embed.set_footer(text="ตรวจสอบ Temp HP ได้ที่ /stat-update | ใช้ /temp-hp clear:true เพื่อลบออก")
        await interaction.response.edit_message(embed=embed, view=None)


@tree.command(name="temp-hp", description="จัดการ Temporary HP")
@app_commands.describe(
    amount="จำนวน Temp HP แบบ fixed เช่น 6",
    dice="ทอย Temp HP เช่น 1d6+1",
    clear="ใส่ true เพื่อลบ Temp HP ออก",
    char="ชื่อตัวละคร (ไม่ใส่ = default)"
)
async def temp_hp_cmd(interaction: discord.Interaction,
                      amount: int = None, dice: str = None,
                      clear: bool = False, char: str = None):
    char_name, cd, err = resolve_char(interaction.user.id, char)
    if err: return await interaction.response.send_message(embed=err, ephemeral=True)

    # Clear
    if clear:
        old = cd.get("temp_hp", 0)
        if not old:
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ERROR ] — ไม่มี Temp HP อยู่ในระบบ", color=COLOR_ERROR),
                ephemeral=True)
        cd["temp_hp"] = 0
        update_char(interaction.user.id, char_name, cd)
        embed = discord.Embed(
            description=f"[ TEMP HP ] — ลบ Temp HP ออกแล้ว (เดิมมี **{old}**)",
            color=COLOR_INFO)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    # Calculate new temp hp
    new_val = 0
    roll_info = ""

    if amount is not None:
        if amount <= 0:
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ERROR ] — Temp HP ต้องมากกว่า 0", color=COLOR_ERROR),
                ephemeral=True)
        new_val = amount
        roll_info = f"fixed **{amount}**"
    elif dice:
        try:
            rolls, total = roll_dice(dice)
            new_val = total
            roll_info = f"`{dice}` → **{total}**"
        except ValueError as e:
            return await interaction.response.send_message(
                embed=discord.Embed(description=f"[ ERROR ] — {e}", color=COLOR_ERROR),
                ephemeral=True)
    else:
        return await interaction.response.send_message(
            embed=discord.Embed(
                description="[ ERROR ] — ระบุ `amount` หรือ `dice` หรือ `clear:true`",
                color=COLOR_ERROR),
            ephemeral=True)

    old_val = cd.get("temp_hp", 0)

    # ถ้ามี Temp HP อยู่แล้ว → ถามว่าจะเปลี่ยนไหม
    if old_val and old_val > 0:
        embed = discord.Embed(
            title="[ TEMP HP CONFLICT ]",
            description=(
                f"ปัจจุบันมี Temp HP อยู่ **{old_val}**\n"
                f"Temp HP ใหม่ได้ {roll_info} = **{new_val}**\n"
                "เก็บอันเดิมหรือเปลี่ยนเป็นอันใหม่?"
            ),
            color=COLOR_WARN)
        view = TempHPReplaceView(interaction.user.id, char_name, old_val, new_val)
        return await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ไม่มีอยู่แล้ว → เซตได้เลย
    cd["temp_hp"] = new_val
    update_char(interaction.user.id, char_name, cd)
    embed = discord.Embed(
        description=f"[ TEMP HP ] — {cd['name']} ได้รับ Temp HP {roll_info} = **{new_val}**",
        color=COLOR_INFO)
    embed.set_footer(text="ตรวจสอบ Temp HP ได้ที่ /stat-update | ใช้ /temp-hp clear:true เพื่อลบออก")
    await interaction.response.send_message(embed=embed)


# ── /roll ──────────────────────────────────────────────────────────────────

@tree.command(name="roll", description="ทอยเต๋า d20 + stat หรือ custom dice")
@app_commands.describe(
    stat="เลือก stat ที่จะใช้ทอย (ถ้าใช้ dice ไม่ต้องใส่)",
    dice="custom dice เช่น 2d6, 1d20 (ถ้าใช้ stat ไม่ต้องใส่)",
    modifiers="Modifiers เพิ่มเติม เช่น +1+2-3",
    adv_dis="Advantage หรือ Disadvantage",
    comment="ข้อความแสดงผล",
    char="ชื่อตัวละคร (ไม่ใส่ = default)"
)
@app_commands.choices(
    stat=[
        app_commands.Choice(name="STR", value="str"),
        app_commands.Choice(name="DEX", value="dex"),
        app_commands.Choice(name="INT", value="int"),
        app_commands.Choice(name="WIS", value="wis"),
    ],
    adv_dis=[
        app_commands.Choice(name="Advantage", value="adv"),
        app_commands.Choice(name="Disadvantage", value="dis"),
    ]
)
async def roll_cmd(interaction: discord.Interaction, stat: str = None, dice: str = None,
                   modifiers: str = None, adv_dis: str = None, comment: str = None, char: str = None):

    bonus_total = parse_bonus(modifiers)
    if bonus_total is None:
        return await interaction.response.send_message(
            embed=discord.Embed(description="[ ERROR ] — รูปแบบ modifier ไม่ถูกต้อง ตัวอย่าง: `+1+2-3`", color=COLOR_ERROR),
            ephemeral=True)

    # Custom dice mode
    if dice and not stat:
        try:
            rolls, raw = roll_dice(dice + (f"+{bonus_total}" if bonus_total else ""))
        except ValueError as e:
            return await interaction.response.send_message(
                embed=discord.Embed(description=f"[ ERROR ] — {e}", color=COLOR_ERROR), ephemeral=True)

        embed = discord.Embed(color=COLOR_NORMAL)
        if comment:
            embed.description = f"*💬 {comment}*"
        embed.add_field(name="🎲 Dice", value=dice, inline=True)
        roll_str = " + ".join(str(r) for r in rolls) + f" = **{sum(rolls)}**" if len(rolls) > 1 else f"**{rolls[0]}**"
        embed.add_field(name="🎲 Roll", value=roll_str, inline=True)
        embed.add_field(name="📊 Total", value=f"**{raw}**\n`{dice}({sum(rolls)}){fmt(bonus_total) if bonus_total else ''}`", inline=False)
        embed.set_footer(text=f"Operator: {interaction.user.display_name}")
        return await interaction.response.send_message(embed=embed)

    # Stat roll mode
    if not stat:
        return await interaction.response.send_message(
            embed=discord.Embed(description="[ ERROR ] — ระบุ stat หรือ dice อย่างน้อยหนึ่งอย่าง", color=COLOR_ERROR),
            ephemeral=True)

    char_name, cd, err = resolve_char(interaction.user.id, char)
    if err: return await interaction.response.send_message(embed=err, ephemeral=True)

    stat_val = cd[stat]
    r1 = random.randint(1, 20)
    final_roll = r1
    roll_display = f"**{r1}**"

    if adv_dis == "adv":
        r2 = random.randint(1, 20)
        final_roll = max(r1, r2)
        roll_display = f"~~{min(r1,r2)}~~ → **{final_roll}** 🎲🎲 Advantage"
    elif adv_dis == "dis":
        r2 = random.randint(1, 20)
        final_roll = min(r1, r2)
        roll_display = f"~~{max(r1,r2)}~~ → **{final_roll}** 🎲🎲 Disadvantage"

    total = final_roll + stat_val + bonus_total
    bonus_str = f" + {bonus_total} (Modifiers)" if bonus_total > 0 else (f" - {abs(bonus_total)} (Modifiers)" if bonus_total < 0 else "")

    if final_roll == 1:
        color, special = COLOR_FUMBLE, "💀 **[ FUMBLE ]** — ตรวจพบความล้มเหลวขั้นวิกฤต"
    elif final_roll == 20:
        color, special = COLOR_CRITICAL, "⭐ **[ CRITICAL ]** — ขอแสดงความยินดี"
    else:
        color, special = COLOR_NORMAL, None

    embed = discord.Embed(color=color)
    if comment: embed.description = f"*💬 {comment}*"
    if special:
        embed.description = (embed.description + "\n" if embed.description else "") + special
    embed.add_field(name="🎯 Stat", value=f"{stat.upper()} ({fmt_stat(stat_val)})", inline=True)
    embed.add_field(name="🎲 Roll", value=roll_display, inline=True)
    embed.add_field(name="📊 Total", value=f"**{total}**\n`d20({final_roll}) + {stat.upper()}({stat_val}){bonus_str}`", inline=False)
    embed.set_footer(text=f"Operator: {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

# ── Stat Conflict View ────────────────────────────────────────────────────

STAT_ICONS = {"str": "💪", "dex": "🏃", "int": "🧠", "wis": "🌿"}
STAT_COLORS = {
    "str": discord.ButtonStyle.danger,
    "dex": discord.ButtonStyle.primary,
    "int": discord.ButtonStyle.primary,
    "wis": discord.ButtonStyle.success,
}

class StatConflictView(discord.ui.View):
    def __init__(self, owner_id: int, top_stats: list, cd: dict,
                 wpn, adv_dis: str, comment: str, char_name: str = None):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.cd = cd
        self.wpn = wpn
        self.adv_dis = adv_dis
        self.comment = comment
        self.char_name = char_name

        for stat in top_stats:
            stat_val = cd[stat]
            label = f"{STAT_ICONS.get(stat, '')} {stat.upper()} ({fmt_stat(stat_val)})"
            btn = discord.ui.Button(
                label=label,
                style=STAT_COLORS.get(stat, discord.ButtonStyle.secondary),
                custom_id=stat
            )
            btn.callback = self.make_callback(stat)
            self.add_item(btn)

    def make_callback(self, stat: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                return await interaction.response.send_message(
                    embed=discord.Embed(
                        description=f"[ ACCESS DENIED ] — {interaction.user.mention} : ปุ่มนี้เป็นของโอเปอเรเตอร์ท่านอื่น",
                        color=COLOR_ERROR),
                    ephemeral=True)
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            await do_atk_roll(interaction, self.cd, stat, self.wpn, self.adv_dis, self.comment, followup=True, user_id=self.owner_id, char_name=self.char_name)
        return callback


async def do_atk_roll(interaction: discord.Interaction, cd: dict, stat: str,
                      wpn, adv_dis: str, comment: str, followup: bool = False,
                      user_id: int = None, char_name: str = None):
    stat_val = cd[stat]
    atk_bonus = wpn["atk_bonus"] if wpn else 0

    # ── ตรวจสอบกระสุนก่อนทอย ──
    slot_key = str(wpn["slot"]) if wpn else None
    ranged = cd.get("ranged", {}) if wpn else {}
    r = ranged.get(slot_key) if slot_key else None

    if r:
        unit = r["unit"]
        mag_size = r["mag_size"]
        per_shot = r["per_shot"]
        if mag_size > 0 and r["current_in_mag"] <= 0:
            # ซองหมดอยู่แล้ว → บล็อก
            embed = discord.Embed(
                title=f"⚠️ [ ซองหมด ] — {wpn['name']}",
                description=f"ซองหมดแล้ว (0/{mag_size} {unit})\nกรุณา Reload ก่อนใช้งานครั้งถัดไป\nจำนวนที่เหลือในคลัง {r['total']} {unit}",
                color=COLOR_WARN)
            reload_view = None
            if r["total"] > 0:
                class QuickReloadView(discord.ui.View):
                    def __init__(self): super().__init__(timeout=60)
                    @discord.ui.button(label="🔄 Reload", style=discord.ButtonStyle.primary)
                    async def reload(self, inter, button):
                        if inter.user.id != (user_id or interaction.user.id): return
                        await do_ammo_reload(inter, user_id or interaction.user.id, char_name, slot_key)
                reload_view = QuickReloadView()
            if followup: await interaction.followup.send(embed=embed, view=reload_view if reload_view else discord.utils.MISSING)
            else: await interaction.response.send_message(embed=embed, view=reload_view if reload_view else discord.utils.MISSING)
            return
        total_all = r["current_in_mag"] + r["total"]
        if total_all <= 0:
            # ทั้งซองและคลังหมด → บล็อก
            embed = discord.Embed(
                title=f"💀 [ หมดคลัง ] — {wpn['name']}",
                description=f"หมดคลังแล้ว (0 {unit})\nไม่สามารถ Reload ได้อีก",
                color=COLOR_FUMBLE)
            if followup: await interaction.followup.send(embed=embed)
            else: await interaction.response.send_message(embed=embed)
            return

    r1 = random.randint(1, 20)
    final_roll = r1
    roll_display = f"**{r1}**"

    if adv_dis == "adv":
        r2 = random.randint(1, 20)
        final_roll = max(r1, r2)
        roll_display = f"~~{min(r1,r2)}~~ → **{final_roll}** 🎲🎲 Advantage"
    elif adv_dis == "dis":
        r2 = random.randint(1, 20)
        final_roll = min(r1, r2)
        roll_display = f"~~{max(r1,r2)}~~ → **{final_roll}** 🎲🎲 Disadvantage"

    total = final_roll + stat_val + atk_bonus

    # ── หักกระสุนหลังทอย ──
    ammo_note = None
    show_reload_btn = False
    if r and user_id and char_name:
        cd_fresh = get_char(user_id, char_name)
        r_fresh = cd_fresh["ranged"][slot_key]
        unit = r_fresh["unit"]
        mag_size = r_fresh["mag_size"]
        per_shot = r_fresh["per_shot"]
        was_last_in_stock = r_fresh["total"] <= per_shot

        if mag_size > 0:
            # ยิง → หักจากซองเท่านั้น คลังไม่เปลี่ยน
            # per_shot อาจเป็นตัวเลขหรือ dice
            import re as _re
            ps = r_fresh["per_shot"]
            if isinstance(ps, str) and _re.match(r"^\d+d\d+$", str(ps)):
                _m = _re.match(r"(\d+)d(\d+)", str(ps))
                _rolls = [random.randint(1, int(_m.group(2))) for _ in range(int(_m.group(1)))]
                actual_per_shot = sum(_rolls)
            else:
                actual_per_shot = int(ps)
            # หักแค่เท่าที่มีในซอง
            actual_per_shot = min(actual_per_shot, r_fresh["current_in_mag"])
            r_fresh["current_in_mag"] = max(0, r_fresh["current_in_mag"] - actual_per_shot)
            just_empty_mag = r_fresh["current_in_mag"] <= 0
            just_empty_stock = r_fresh["total"] <= 0 and just_empty_mag
            total_remaining = r_fresh["current_in_mag"] + r_fresh["total"]
            if just_empty_stock:
                ammo_note = f"💀 คุณเพิ่งใช้ของหมดคลังไป! ไม่สามารถ Reload ได้อีก\n🎯 ซอง: 0/{mag_size} {unit} — คลัง: 0 {unit} — รวม: 0 {unit}"
            elif just_empty_mag:
                ammo_note = f"⚡ คุณเพิ่งใช้กระสุนหมดซองไป! (ใช้ไป {actual_per_shot} {unit}) กรุณา Reload ก่อนยิงครั้งถัดไป\n🎯 ซอง: 0/{mag_size} {unit} — คลัง: {r_fresh['total']} {unit} — รวม: {total_remaining} {unit}"
                show_reload_btn = r_fresh["total"] > 0
            else:
                ammo_note = f"🎯 ซอง: {r_fresh['current_in_mag']}/{mag_size} {unit} (ใช้ไป {actual_per_shot}) — คลัง: {r_fresh['total']} {unit} — รวม: {total_remaining} {unit}"
        else:
            # ไม่มีซอง → หักจากคลังโดยตรง
            import re as _re
            ps = r_fresh["per_shot"]
            if isinstance(ps, str) and _re.match(r"^\d+d\d+$", str(ps)):
                _m = _re.match(r"(\d+)d(\d+)", str(ps))
                _rolls = [random.randint(1, int(_m.group(2))) for _ in range(int(_m.group(1)))]
                actual_per_shot = sum(_rolls)
            else:
                actual_per_shot = int(ps)
            actual_per_shot = min(actual_per_shot, r_fresh["total"])
            r_fresh["total"] = max(0, r_fresh["total"] - actual_per_shot)
            if r_fresh["total"] <= 0:
                ammo_note = f"💀 คุณเพิ่งใช้ของหมดคลังไป! ไม่สามารถ Reload ได้อีก\n🎯 จำนวนที่เหลือ: 0 {unit}"
            else:
                ammo_note = f"🎯 จำนวนที่เหลือ: {r_fresh['total']} {unit} (ใช้ไป {actual_per_shot})"
        update_char(user_id, char_name, cd_fresh)

    if final_roll == 1:
        color, special = COLOR_FUMBLE, "💀 **[ FUMBLE ]** — ตรวจพบความล้มเหลวขั้นวิกฤต"
    elif final_roll == 20:
        color, special = COLOR_CRITICAL, "⭐ **[ CRITICAL ]** — ขอแสดงความยินดี"
    else:
        color, special = COLOR_NORMAL, None

    is_primary = wpn["slot"] == 1 if wpn else True
    wpn_label = wpn["name"] if wpn else "—"
    role_label = "(Primary)" if is_primary else "(Sub)"

    embed = discord.Embed(color=color)
    if comment: embed.description = f"*💬 {comment}*"
    if special:
        embed.description = (embed.description + "\n" if embed.description else "") + special

    atk_str = f"`d20({final_roll}) + {stat.upper()}({stat_val})"
    if atk_bonus: atk_str += f" + ATK({atk_bonus})"
    atk_str += "`"

    embed.add_field(
        name=f"🎯 Attack Roll — {wpn_label} {role_label}",
        value=f"**{total}** — {atk_str}",
        inline=False)

    dmg_label = "⚔️ Primary Weapon" if is_primary else f"🗡️ Sub Weapon — {wpn_label}"
    dmg_dice = wpn["damage"] if wpn else "—"
    embed.add_field(name=dmg_label, value=dmg_dice, inline=False)

    if ammo_note:
        embed.add_field(name="\u200b", value=ammo_note, inline=False)

    embed.set_footer(text=f"Operator: {interaction.user.display_name}")

    dmg_view = None
    if wpn and wpn.get("damage") and wpn["damage"] not in ("—", ""):
        dmg_view = DamageView(interaction.user.id, wpn["damage"], is_crit=(final_roll == 20))
        if show_reload_btn:
            uid = user_id or interaction.user.id
            cn = char_name
            sk = slot_key
            reload_btn = discord.ui.Button(label="🔄 Reload", style=discord.ButtonStyle.primary)
            async def reload_callback(inter, _uid=uid, _cn=cn, _sk=sk):
                if inter.user.id != _uid: return
                await do_ammo_reload(inter, _uid, _cn, _sk)
            reload_btn.callback = reload_callback
            dmg_view.add_item(reload_btn)

    if followup:
        await interaction.followup.send(embed=embed, view=dmg_view)
    else:
        await interaction.response.send_message(embed=embed, view=dmg_view)


# ── /atk-roll ──────────────────────────────────────────────────────────────

@tree.command(name="atk-roll", description="ทอย Attack Roll พร้อมแจ้ง Damage Dice")
@app_commands.describe(
    weapon="slot อาวุธ 1, 2 หรือ 3",
    adv_dis="Advantage หรือ Disadvantage",
    comment="ข้อความแสดงผล",
    char="ชื่อตัวละคร (ไม่ใส่ = default)"
)
@app_commands.choices(
    weapon=[
        app_commands.Choice(name="Slot 1 (Primary)", value=1),
        app_commands.Choice(name="Slot 2 (Sub)", value=2),
        app_commands.Choice(name="Slot 3 (Sub)", value=3),
    ],
    adv_dis=[
        app_commands.Choice(name="Advantage", value="adv"),
        app_commands.Choice(name="Disadvantage", value="dis"),
    ]
)
async def atk_roll(interaction: discord.Interaction, weapon: int = 1,
                   adv_dis: str = None, comment: str = None, char: str = None):
    char_name, cd, err = resolve_char(interaction.user.id, char)
    if err: return await interaction.response.send_message(embed=err, ephemeral=True)

    weapons = cd.get("weapons", [])
    wpn = next((w for w in weapons if w["slot"] == weapon), None)

    # หา stat สูงสุด
    stats = {"str": cd["str"], "dex": cd["dex"], "int": cd["int"], "wis": cd["wis"]}
    max_val = max(stats.values())
    top_stats = [k for k, v in stats.items() if v == max_val]

    # ถ้ามี stat เท่ากันหลายตัว → ให้เลือกก่อน
    if len(top_stats) > 1:
        embed = discord.Embed(
            title=f"⚖️ [ STAT CONFLICT ] — {wpn['name'] if wpn else f'Slot {weapon}'}",
            description=(
                f"ตรวจพบ stat เท่ากันหลายตัว — "
                + " และ ".join(f"**{s.upper()} ({fmt_stat(cd[s])})**" for s in top_stats)
                + "\nเลือก stat ที่จะใช้ทอย Attack Roll ได้เลย"
            ),
            color=COLOR_INFO)
        embed.set_footer(text=f"Operator: {interaction.user.display_name}")
        view = StatConflictView(interaction.user.id, top_stats, cd, wpn, adv_dis, comment, char_name=char_name)
        return await interaction.response.send_message(embed=embed, view=view)

    # stat ชัดเจน → ทอยเลย
    await do_atk_roll(interaction, cd, top_stats[0], wpn, adv_dis, comment, user_id=interaction.user.id, char_name=char_name)

# ── Damage View (after atk-roll) ──────────────────────────────────────────

class DamageModModal(discord.ui.Modal, title="Damage Modifier"):
    mod = discord.ui.TextInput(
        label="ใส่ modifier เช่น +2 หรือ -1",
        placeholder="+2 หรือ -1 (ต้องมี + หรือ - นำหน้า)",
        required=True
    )
    def __init__(self, owner_id, dmg_dice, dmg_view):
        super().__init__()
        self.owner_id = owner_id
        self.dmg_dice = dmg_dice
        self.dmg_view = dmg_view

    async def on_submit(self, interaction: discord.Interaction):
        val = self.mod.value.strip()
        if not re.match(r"^[+\-]\d+$", val):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    description="[ ERROR ] — รูปแบบไม่ถูกต้อง กรุณาใส่ `+2` หรือ `-1` (ต้องมี + หรือ - นำหน้าเสมอ)",
                    color=COLOR_ERROR),
                ephemeral=True)
        mod_val = int(val)
        await roll_damage(interaction, self.dmg_dice, mod_val, self.dmg_view, is_crit=self.dmg_view.is_crit)

class DamageView(discord.ui.View):
    def __init__(self, owner_id: int, dmg_dice: str, is_crit: bool = False):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.dmg_dice = dmg_dice
        self.is_crit = is_crit
        self.used = False

    async def check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=f"[ ACCESS DENIED ] — {interaction.user.mention} : ปุ่มนี้เป็นของโอเปอเรเตอร์ท่านอื่น",
                    color=COLOR_ERROR),
                ephemeral=True)
            return False
        return True

    def disable_buttons(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="⚔️ Damage", style=discord.ButtonStyle.danger)
    async def damage_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_owner(interaction): return
        if self.used: return
        self.used = True
        self.disable_buttons()
        await roll_damage(interaction, self.dmg_dice, 0, self, is_crit=self.is_crit)

    @discord.ui.button(label="⚔️ Damage + Modifiers", style=discord.ButtonStyle.primary)
    async def damage_mod_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_owner(interaction): return
        if self.used: return
        await interaction.response.send_modal(DamageModModal(self.owner_id, self.dmg_dice, self))

    @discord.ui.button(label="💨 Miss", style=discord.ButtonStyle.secondary)
    async def miss_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_owner(interaction): return
        if self.used: return
        self.used = True
        self.disable_buttons()
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            embed=discord.Embed(
                description=f"💨 **[ MISS ]** — {interaction.user.mention} โจมตีไม่โดน",
                color=COLOR_NORMAL))

async def roll_damage(interaction: discord.Interaction, dmg_dice: str, mod: int, view: DamageView, is_crit: bool = False):
    """Parse damage dice string like 1d4+4 or 1d5+DEX or pure number like 1"""
    view.used = True
    view.disable_buttons()

    dice_str = dmg_dice.strip()
    base_match = re.match(r"(\d*d\d+)", dice_str)

    # Pure number (no dice) — แสดงค่าตรงๆ ไม่ทอย
    if not base_match:
        try:
            base_val = int(float(dice_str))
        except:
            embed = discord.Embed(description=f"[ ERROR ] — รูปแบบ dice ไม่ถูกต้อง: `{dmg_dice}`", color=COLOR_ERROR)
            await interaction.response.edit_message(view=view)
            return await interaction.followup.send(embed=embed, ephemeral=True)

        total = base_val + mod
        parts = [str(base_val)]
        if mod: parts.append(f"Mod({mod:+})")
        embed = discord.Embed(color=COLOR_FUMBLE)
        embed.add_field(name="💥 Damage", value=f"**{total}**", inline=True)
        embed.add_field(name="📌 Fixed", value=" + ".join(parts), inline=True)
        if is_crit:
            embed.add_field(name="​", value="*Fixed damage ไม่ได้รับผลจาก Critical Hit*", inline=False)
        await interaction.response.edit_message(view=view)
        return await interaction.followup.send(embed=embed)

    dice_base = base_match.group(1)
    # ลบ stat pattern ออกก่อน เช่น +WIS(4) หรือ +DEX(-2) แล้วหา numeric bonus ที่เหลือ
    cleaned = re.sub(r'[+\-]?[A-Z]+\([^)]+\)', '', dice_str)
    bonus_matches = re.findall(r'[+\-]\d+', cleaned.replace(dice_base, '', 1))
    base_bonus = sum(int(b) for b in bonus_matches)
    # หา stat value จากวงเล็บ เช่น WIS(4) → 4, DEX(-2) → -2
    stat_val_match = re.search(r"[A-Z]+\((-?\d+)\)", dice_str)
    stat_bonus = int(stat_val_match.group(1)) if stat_val_match else 0
    total_bonus = base_bonus + stat_bonus + mod

    # Roll
    count_match = re.match(r"(\d+)d(\d+)", dice_base)
    if count_match:
        count = int(count_match.group(1))
        sides = int(count_match.group(2))
    else:
        count = 1
        sides = int(re.search(r"d(\d+)", dice_base).group(1))

    # ถ้า crit ทอย dice สองเท่า
    crit_count = count * 2 if is_crit else count
    crit_base = f"{crit_count}d{sides}" if is_crit else dice_base

    rolls = [random.randint(1, sides) for _ in range(crit_count)]
    roll_sum = sum(rolls)
    total = roll_sum + total_bonus

    roll_parts = [f"{crit_base}({roll_sum})"]
    stat_match = re.search(r"([A-Z]+\(-?\d+\))", dice_str)
    if stat_match:
        roll_parts.append(stat_match.group(1))
    if base_bonus:
        roll_parts.append(f"weapon({base_bonus:+})")
    if mod:
        roll_parts.append(f"Mod({mod:+})")

    roll_display = " + ".join(roll_parts)

    embed = discord.Embed(color=COLOR_CRITICAL if is_crit else COLOR_FUMBLE)
    if is_crit:
        embed.description = f"⭐ **[ CRITICAL HIT ]** — เต๋าเปลี่ยนจาก `{dice_base}` → `{crit_base}`"
    embed.add_field(name="💥 Damage", value=f"**{total}**", inline=True)
    embed.add_field(name="🎲 Roll", value=roll_display, inline=True)

    await interaction.response.edit_message(view=view)
    await interaction.followup.send(embed=embed)


# ── /atk (PRTS) ────────────────────────────────────────────────────────────

def get_highest_stats(cd):
    stats = {"STR": cd["str"], "DEX": cd["dex"], "INT": cd["int"], "WIS": cd["wis"]}
    max_val = max(stats.values())
    return [k for k, v in stats.items() if v == max_val], max_val

@tree.command(name="atk", description="[PRTS] เรียก Attack Roll ของโอเปอเรเตอร์")
@app_commands.describe(player="เลือกโอเปอเรเตอร์เป้าหมาย")
async def atk(interaction: discord.Interaction, player: discord.Member):
    if not is_prts(interaction.user):
        return await interaction.response.send_message(
            embed=discord.Embed(description=f"[ ACCESS DENIED ] — {interaction.user.mention} : Insufficient clearance. PRTS Only.", color=COLOR_ERROR),
            ephemeral=True)

    _, cd = get_default_char(player.id)
    if not cd:
        return await interaction.response.send_message(
            embed=discord.Embed(description=f"[ ERROR ] — {player.mention} : ไม่พบข้อมูลโอเปอเรเตอร์ในฐานข้อมูล กรุณาลงทะเบียนผ่าน `/set-char`", color=COLOR_ERROR),
            ephemeral=True)

    top_stats, max_val = get_highest_stats(cd)
    embed = discord.Embed(title="🎯 Target Locked. Attack Roll.", color=COLOR_INFO)

    if len(top_stats) == 1:
        embed.add_field(name="Your Attack Dice", value=f"🎲 !r d20 + {top_stats[0]} ({fmt_stat(max_val)})", inline=False)
        embed.add_field(name="[ Main Stat ]", value=top_stats[0], inline=True)
    else:
        stat_list = ", ".join(top_stats)
        embed.add_field(name="[ STAT CONFLICT DETECTED ]",
                        value=f"ตรวจพบค่า stat เท่ากันหลายตัว — **{stat_list}** : `{fmt_stat(max_val)}`\nเลือก stat ที่ต้องการใช้แล้วดำเนินการต่อได้เลย",
                        inline=False)
        embed.add_field(name="Your Attack Dice", value=f"🎲 !r d20 + [{stat_list}] ({fmt_stat(max_val)})", inline=False)

    embed.set_footer(text=f"Issued by: {interaction.user.display_name}")
    await interaction.response.send_message(content=f"{player.mention} | **Target Locked. Attack Roll.**", embed=embed)

# ── /player-check ──────────────────────────────────────────────────────────

@tree.command(name="player-check", description="ดู HP / AP ของ player ทุกคนในปาร์ตี้")
async def player_check(interaction: discord.Interaction):
    data = load_data()
    lines = []
    i = 1
    for uid, user in data.items():
        # แสดงเฉพาะตัวละครที่ไม่มี is_npc flag
        for char_name, cd in user.get("chars", {}).items():
            if cd.get("is_npc"): continue
            # ใช้ default เท่านั้น ไม่แสดงตัวที่ไม่ใช่ default
            if user.get("default") != char_name: continue
            member = interaction.guild.get_member(int(uid))
            name = member.display_name if member else cd.get("name", f"User {uid}")
            hp = f"{cd['current_hp']}/{cd['max_hp']}"
            ap = f"{cd['current_ap']}/{cd['max_ap']}"
            char_display = cd.get("name", char_name)
            owner = f"<@{uid}>"
            lines.append(f"`{i}.` **{char_display}** ({owner}) — ❤️ HP {hp} | ✨ AP {ap}")
            i += 1

    if not lines:
        return await interaction.response.send_message(
            embed=discord.Embed(description="[ ERROR ] — ไม่พบข้อมูลโอเปอเรเตอร์ในระบบเลย", color=COLOR_ERROR))

    embed = discord.Embed(title="[ PARTY STATUS ]", description="\n".join(lines), color=COLOR_INFO)
    embed.set_footer(text=f"Issued by: {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

# ── /npc-check ─────────────────────────────────────────────────────────────

@tree.command(name="npc-check", description="[PRTS] ดูตัวละครทั้งหมดของ PRTS")
async def npc_check(interaction: discord.Interaction):
    if not is_prts(interaction.user):
        return await interaction.response.send_message(
            embed=discord.Embed(description=f"[ ACCESS DENIED ] — {interaction.user.mention} : Insufficient clearance. PRTS Only.", color=COLOR_ERROR),
            ephemeral=True)

    data = load_data()
    lines = []
    i = 1
    for uid, user in data.items():
        for char_name, cd in user.get("chars", {}).items():
            if not cd.get("is_npc"): continue
            hp = f"{cd['current_hp']}/{cd['max_hp']}"
            ap = f"{cd['current_ap']}/{cd['max_ap']}"
            lines.append(f"`{i}.` **{char_name}** — ❤️ HP {hp} | ✨ AP {ap}")
            i += 1

    if not lines:
        return await interaction.response.send_message(
            embed=discord.Embed(description="[ ERROR ] — ไม่พบข้อมูล NPC ในระบบ", color=COLOR_ERROR), ephemeral=True)

    embed = discord.Embed(title="[ NPC ROSTER ] — PRTS Only", description="\n".join(lines), color=COLOR_PURPLE)
    embed.set_footer(text=f"Issued by: {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── /initiative ────────────────────────────────────────────────────────────

class InitiativeBonusModal(discord.ui.Modal, title="Initiative Bonus"):
    bonus = discord.ui.TextInput(
        label="โบนัส",
        placeholder="เช่น +2 หรือ -1",
        required=True)
    def __init__(self, guild_id, adv_dis=None):
        super().__init__()
        self.guild_id = guild_id
        self.adv_dis = adv_dis
    async def on_submit(self, interaction: discord.Interaction):
        val = self.bonus.value.strip()
        import re as _re
        if not _re.match(r"^[+\-]\d+$", val):
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ERROR ] — กรุณาใส่ `+2` หรือ `-1` (ต้องมี + หรือ - นำหน้า)", color=COLOR_ERROR),
                ephemeral=True)
        bonus_val = int(val)
        _, cd = get_default_char(interaction.user.id)
        if not cd:
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ERROR ] — ไม่พบตัวละคร Default กรุณาใช้ `/set-char` ก่อน", color=COLOR_ERROR),
                ephemeral=True)
        char_name = cd["name"]
        if self.guild_id in initiative_active and char_name in initiative_active[self.guild_id]:
            return await interaction.response.send_message(
                embed=discord.Embed(description=f"[ WARNING ] — **{char_name}** ทอยไปแล้วในรอบนี้", color=COLOR_WARN),
                ephemeral=True)
        wis = cd["wis"]
        r1 = random.randint(1, 20)
        if self.adv_dis == "adv":
            r2 = random.randint(1, 20)
            roll = max(r1, r2)
            roll_display = f"~~{min(r1,r2)}~~ → **{roll}** 🎲🎲 Advantage"
        elif self.adv_dis == "dis":
            r2 = random.randint(1, 20)
            roll = min(r1, r2)
            roll_display = f"~~{max(r1,r2)}~~ → **{roll}** 🎲🎲 Disadvantage"
        else:
            roll = r1
            roll_display = f"**{roll}**"
        total = roll + wis + bonus_val
        if self.guild_id not in initiative_active:
            initiative_active[self.guild_id] = {}
        initiative_active[self.guild_id][char_name] = total
        bonus_str = f" + Bonus({val})" if bonus_val else ""
        embed = discord.Embed(
            description=f"🎲 **{char_name}** ทอย Initiative ได้ **{total}** `d20({roll_display}) + WIS({wis}){bonus_str}`",
            color=COLOR_CYAN)
        await interaction.response.send_message(embed=embed)


class InitiativeView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    async def do_roll(self, interaction: discord.Interaction, adv_dis: str = None):
        _, cd = get_default_char(interaction.user.id)
        if not cd:
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ERROR ] — ไม่พบตัวละคร Default กรุณาใช้ `/set-char` ก่อน", color=COLOR_ERROR),
                ephemeral=True)

        char_name = cd["name"]
        if self.guild_id in initiative_active and char_name in initiative_active[self.guild_id]:
            return await interaction.response.send_message(
                embed=discord.Embed(description=f"[ WARNING ] — **{char_name}** ทอยไปแล้วในรอบนี้", color=COLOR_WARN),
                ephemeral=True)

        wis = cd["wis"]
        r1 = random.randint(1, 20)

        if adv_dis == "adv":
            r2 = random.randint(1, 20)
            roll = max(r1, r2)
            roll_display = f"~~{min(r1,r2)}~~ → **{roll}** 🎲🎲 Advantage"
        elif adv_dis == "dis":
            r2 = random.randint(1, 20)
            roll = min(r1, r2)
            roll_display = f"~~{max(r1,r2)}~~ → **{roll}** 🎲🎲 Disadvantage"
        else:
            roll = r1
            roll_display = f"**{roll}**"

        total = roll + wis

        if self.guild_id not in initiative_active:
            initiative_active[self.guild_id] = {}
        initiative_active[self.guild_id][char_name] = total

        embed = discord.Embed(
            description=f"🎲 **{char_name}** ทอย Initiative ได้ **{total}** `d20({roll_display}) + WIS({wis})`",
            color=COLOR_CYAN)
        await interaction.response.send_message(embed=embed)

    @discord.ui.button(label="🎲 Roll Initiative", style=discord.ButtonStyle.primary)
    async def roll_init(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.do_roll(interaction)

    @discord.ui.button(label="🎲 Advantage", style=discord.ButtonStyle.primary)
    async def roll_adv(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.do_roll(interaction, adv_dis="adv")

    @discord.ui.button(label="🎲 Disadvantage", style=discord.ButtonStyle.danger)
    async def roll_dis(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.do_roll(interaction, adv_dis="dis")

    @discord.ui.button(label="➕ Bonus", style=discord.ButtonStyle.secondary)
    async def roll_bonus(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(InitiativeBonusModal(self.guild_id))

@tree.command(name="initiative", description="[PRTS] เปิด Initiative Phase")
async def initiative_cmd(interaction: discord.Interaction):
    if not is_prts(interaction.user):
        return await interaction.response.send_message(
            embed=discord.Embed(description=f"[ ACCESS DENIED ] — {interaction.user.mention} : Insufficient clearance. PRTS Only.", color=COLOR_ERROR),
            ephemeral=True)
    initiative_active[interaction.guild.id] = {}
    embed = discord.Embed(title="⚔️ INITIATIVE PHASE",
                          description="กดปุ่มด้านล่างเพื่อทอย Initiative ของตัวเอง\nระบบจะทอย 1d20 + WIS ให้อัตโนมัติ",
                          color=COLOR_CYAN)
    view = InitiativeView(interaction.guild.id)
    await interaction.response.send_message(embed=embed, view=view)

@tree.command(name="initiative-npc", description="[PRTS] ทอย Initiative ให้ NPC หรือมอนสเตอร์")
@app_commands.describe(name="ชื่อ NPC หรือมอนสเตอร์", bonus="bonus เพิ่มเติม เช่น 2 หรือ -1", adv_dis="Advantage หรือ Disadvantage")
@app_commands.choices(adv_dis=[
    app_commands.Choice(name="Advantage", value="adv"),
    app_commands.Choice(name="Disadvantage", value="dis"),
])
async def initiative_npc(interaction: discord.Interaction, name: str, bonus: int = 0, adv_dis: str = None):
    if not is_prts(interaction.user):
        return await interaction.response.send_message(
            embed=discord.Embed(description=f"[ ACCESS DENIED ] — {interaction.user.mention} : Insufficient clearance. PRTS Only.", color=COLOR_ERROR),
            ephemeral=True)
    if interaction.guild.id not in initiative_active:
        initiative_active[interaction.guild.id] = {}

    r1 = random.randint(1, 20)
    if adv_dis == "adv":
        r2 = random.randint(1, 20)
        roll = max(r1, r2)
        roll_display = f"~~{min(r1,r2)}~~ → {roll} 🎲🎲 Advantage"
    elif adv_dis == "dis":
        r2 = random.randint(1, 20)
        roll = min(r1, r2)
        roll_display = f"~~{max(r1,r2)}~~ → {roll} 🎲🎲 Disadvantage"
    else:
        roll = r1
        roll_display = str(roll)

    total = roll + bonus
    initiative_active[interaction.guild.id][name] = total
    bonus_str = fmt(bonus) if bonus else ""
    embed = discord.Embed(
        description=f"🎲 **{name}** ทอยได้ **{total}** `d20({roll_display}){bonus_str}`\nซ่อนผลจนกว่าจะ /initiative-order",
        color=COLOR_PURPLE)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="initiative-order", description="[PRTS] ปิด Initiative Phase และแสดง order")
async def initiative_order(interaction: discord.Interaction):
    if not is_prts(interaction.user):
        return await interaction.response.send_message(
            embed=discord.Embed(description=f"[ ACCESS DENIED ] — {interaction.user.mention} : Insufficient clearance. PRTS Only.", color=COLOR_ERROR),
            ephemeral=True)
    if interaction.guild.id not in initiative_active or not initiative_active[interaction.guild.id]:
        return await interaction.response.send_message(
            embed=discord.Embed(description="[ ERROR ] — ยังไม่มีใครทอย Initiative กรุณาใช้ `/initiative` ก่อน", color=COLOR_ERROR),
            ephemeral=True)

    order = sorted(initiative_active[interaction.guild.id].items(), key=lambda x: x[1], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (name, roll) in enumerate(order):
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} **{name}** — {roll}")

    embed = discord.Embed(title="⚔️ INITIATIVE ORDER", description="\n".join(lines), color=COLOR_CYAN)
    embed.set_footer(text=f"Issued by: {interaction.user.display_name}")
    initiative_active.pop(interaction.guild.id, None)
    await interaction.response.send_message(embed=embed)

# ── /ammo ──────────────────────────────────────────────────────────────────

def get_ranged_weapons(cd: dict) -> dict:
    return cd.get("ranged", {})

def get_weapon_name(cd: dict, slot: int) -> str:
    for w in cd.get("weapons", []):
        if w["slot"] == slot:
            return w["name"]
    return f"Slot {slot}"

def build_ammo_status_embed(char_name: str, cd: dict, slot: str) -> discord.Embed:
    ranged = get_ranged_weapons(cd)
    r = ranged[slot]
    wpn_name = get_weapon_name(cd, int(slot))
    unit = r["unit"]
    total = r["total"]
    mag_size = r["mag_size"]
    current = r["current_in_mag"]
    total_all = current + total
    if mag_size == 0:
        embed = discord.Embed(title=f"🎯 สถานะ — {wpn_name} (Slot {slot})", color=COLOR_INFO)
        embed.add_field(name="🎒 จำนวนที่เหลือ", value=f"**{total}** {unit}", inline=True)
        embed.add_field(name="⚡ ต่อช็อต", value=f"{r['per_shot']} {unit}", inline=True)
    else:
        embed = discord.Embed(title=f"🎯 สถานะ — {wpn_name} (Slot {slot})", color=COLOR_INFO)
        embed.add_field(name="📦 ซอง", value=f"**{current} / {mag_size}** {unit}", inline=True)
        embed.add_field(name="🎒 คลัง", value=f"**{total}** {unit}", inline=True)
        embed.add_field(name="🔢 รวม", value=f"**{total_all}** {unit}", inline=True)
        if current > 0:
            shots_left = current // (r['per_shot'] if isinstance(r['per_shot'], int) else 1)
            embed.add_field(name="\u200b", value=f"อีก {current} {unit} จะต้อง Reload", inline=False)
    return embed


class AmmoAddModal(discord.ui.Modal, title="เติมจำนวน"):
    amount = discord.ui.TextInput(label="จำนวนที่ต้องการเติม", placeholder="เช่น 10", required=True)
    def __init__(self, user_id, char_name, slot, view):
        super().__init__()
        self.user_id = user_id; self.char_name = char_name; self.slot = slot; self.sv = view
    async def on_submit(self, interaction):
        try:
            amt = int(self.amount.value)
            if amt <= 0: raise ValueError
        except:
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ERROR ] — กรอกตัวเลขที่มากกว่า 0", color=COLOR_ERROR), ephemeral=True)
        cd = get_char(self.user_id, self.char_name)
        cd["ranged"][self.slot]["total"] += amt
        update_char(self.user_id, self.char_name, cd)
        embed = build_ammo_status_embed(self.char_name, cd, self.slot)
        embed.set_footer(text=f"Operator: {interaction.user.display_name}")
        await interaction.response.edit_message(embed=embed, view=self.sv)


class AmmoReduceModal(discord.ui.Modal, title="ลดจำนวน"):
    amount = discord.ui.TextInput(label="จำนวนที่ต้องการลด", placeholder="เช่น 5", required=True)
    def __init__(self, user_id, char_name, slot, view):
        super().__init__()
        self.user_id = user_id; self.char_name = char_name; self.slot = slot; self.sv = view
    async def on_submit(self, interaction):
        try:
            amt = int(self.amount.value)
            if amt <= 0: raise ValueError
        except:
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ERROR ] — กรอกตัวเลขที่มากกว่า 0", color=COLOR_ERROR), ephemeral=True)
        cd = get_char(self.user_id, self.char_name)
        cd["ranged"][self.slot]["total"] = max(0, cd["ranged"][self.slot]["total"] - amt)
        update_char(self.user_id, self.char_name, cd)
        embed = build_ammo_status_embed(self.char_name, cd, self.slot)
        embed.set_footer(text=f"Operator: {interaction.user.display_name}")
        await interaction.response.edit_message(embed=embed, view=self.sv)


class AmmoUnloadModal(discord.ui.Modal, title="เอาออกจากซอง"):
    amount = discord.ui.TextInput(label="จำนวนที่จะเอาออกจากซอง", placeholder="ใส่จำนวน หรือ 999 เพื่อเอาออกทั้งหมด", required=True)
    def __init__(self, user_id, char_name, slot, view):
        super().__init__()
        self.user_id = user_id; self.char_name = char_name; self.slot = slot; self.sv = view
    async def on_submit(self, interaction):
        try:
            amt = int(self.amount.value)
            if amt <= 0: raise ValueError
        except:
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ERROR ] — กรอกตัวเลขที่มากกว่า 0 หรือ 999", color=COLOR_ERROR), ephemeral=True)
        cd = get_char(self.user_id, self.char_name)
        r = cd["ranged"][self.slot]
        cur = r["current_in_mag"]
        actual = cur if amt >= 999 else min(amt, cur)
        r["current_in_mag"] -= actual
        r["total"] += actual
        update_char(self.user_id, self.char_name, cd)
        embed = build_ammo_status_embed(self.char_name, cd, self.slot)
        embed.set_footer(text=f"↩️ เอาออกจากซอง {actual} {r['unit']} — Operator: {interaction.user.display_name}")
        await interaction.response.edit_message(embed=embed, view=self.sv)


class AmmoSetupModal(discord.ui.Modal, title="⚙️ Setup"):
    total_input = discord.ui.TextInput(label="จำนวนที่มีทั้งหมด", placeholder="เช่น 50", required=True)
    unit_input  = discord.ui.TextInput(label="สรรพนาม", placeholder="เช่น ลูก / ดอก / อัน / ชิ้น", required=True)
    mag_input   = discord.ui.TextInput(label="ขนาดซอง (ถ้าไม่มีซองใส่ 000)", placeholder="เช่น 15 หรือ 000", required=True)
    per_shot    = discord.ui.TextInput(label="จำนวนที่ใช้ต่อช็อต", placeholder="เช่น 1 (ปกติ) หรือ 2 (ลูกซองแฝด) หรือ 2d6 (ปืนกล)", required=True)
    def __init__(self, user_id, char_name, slot):
        super().__init__()
        self.user_id = user_id; self.char_name = char_name; self.slot = str(slot)
    async def on_submit(self, interaction):
        try: total = int(self.total_input.value); assert total >= 0
        except: return await interaction.response.send_message(
            embed=discord.Embed(description="[ ERROR ] — จำนวนต้องเป็นตัวเลข >= 0", color=COLOR_ERROR), ephemeral=True)
        unit = self.unit_input.value.strip()
        try:
            mag_raw = self.mag_input.value.strip()
            mag = 0 if mag_raw == "000" else int(mag_raw)
            assert mag >= 0
        except: return await interaction.response.send_message(
            embed=discord.Embed(description="[ ERROR ] — ขนาดซองต้องเป็นตัวเลข หรือ 000", color=COLOR_ERROR), ephemeral=True)
        per_raw = self.per_shot.value.strip()
        import re as _re
        if _re.match(r"^\d+d\d+$", per_raw):
            per = per_raw
        else:
            try: per = int(per_raw); assert per >= 1
            except: return await interaction.response.send_message(
                embed=discord.Embed(description="[ ERROR ] — จำนวนต่อช็อตต้องเป็นตัวเลข เช่น 1, 2 หรือ 2d6", color=COLOR_ERROR), ephemeral=True)
        stock = max(0, total - mag) if mag > 0 else total
        cd = get_char(self.user_id, self.char_name)
        if "ranged" not in cd: cd["ranged"] = {}

        # ถ้ามีข้อมูลเดิมอยู่แล้ว → เตือนก่อน
        if self.slot in cd["ranged"]:
            old = cd["ranged"][self.slot]
            old_total = old["current_in_mag"] + old["total"]
            class OverwriteConfirmView(discord.ui.View):
                def __init__(self_v):
                    super().__init__(timeout=30)
                    self_v.new_stock = stock
                    self_v.new_mag = mag
                    self_v.new_total = total
                    self_v.new_unit = unit
                    self_v.new_per = per

                @discord.ui.button(label="✅ ยืนยัน ทับข้อมูลเดิม", style=discord.ButtonStyle.danger)
                async def confirm(self_v, inter, button):
                    if inter.user.id != self.user_id: return
                    cd2 = get_char(self.user_id, self.char_name)
                    if "ranged" not in cd2: cd2["ranged"] = {}
                    cd2["ranged"][self.slot] = {"total": self_v.new_stock, "unit": self_v.new_unit, "mag_size": mag, "current_in_mag": mag if mag > 0 else self_v.new_total, "per_shot": self_v.new_per}
                    update_char(self.user_id, self.char_name, cd2)
                    wpn_name = get_weapon_name(cd2, int(self.slot))
                    embed = discord.Embed(title="[ LONG-RANGE SETUP COMPLETE ]",
                        description=f"อัปเดตข้อมูลอาวุธระยะไกลสำหรับ **{wpn_name} (Slot {self.slot})** เรียบร้อยแล้ว", color=COLOR_INFO)
                    embed.set_footer(text=f"Operator: {inter.user.display_name}")
                    self_v.stop()
                    await inter.response.edit_message(embed=embed, view=None)

                @discord.ui.button(label="❌ ยกเลิก", style=discord.ButtonStyle.secondary)
                async def cancel(self_v, inter, button):
                    if inter.user.id != self.user_id: return
                    self_v.stop()
                    await inter.response.edit_message(embed=discord.Embed(description="[ CANCELLED ] — ยกเลิกแล้ว", color=COLOR_INFO), view=None)

            wpn_name = get_weapon_name(cd, int(self.slot))
            warn_embed = discord.Embed(
                title="⚠️ [ WARNING ] — มีข้อมูลอยู่แล้ว",
                description=f"**{wpn_name} (Slot {self.slot})** มีข้อมูลอาวุธระยะไกลอยู่แล้ว\nรวมทั้งหมด: **{old_total} {old['unit']}**\n\nถ้ายืนยัน ข้อมูลเดิมจะหายไปถาวร",
                color=COLOR_WARN)
            return await interaction.response.send_message(embed=warn_embed, view=OverwriteConfirmView(), ephemeral=True)
        cd["ranged"][self.slot] = {"total": stock, "unit": unit, "mag_size": mag, "current_in_mag": mag if mag > 0 else total, "per_shot": per}
        update_char(self.user_id, self.char_name, cd)
        wpn_name = get_weapon_name(cd, int(self.slot))
        embed = discord.Embed(title="[ LONG-RANGE SETUP COMPLETE ]",
            description=f"ตั้งค่าระบบอาวุธระยะไกลสำหรับ **{wpn_name} (Slot {self.slot})** เรียบร้อยแล้ว", color=COLOR_INFO)
        embed.add_field(name="🎯 จำนวนที่มีทั้งหมด", value=f"{total} {unit}", inline=True)
        if mag > 0: embed.add_field(name="📦 ซอง", value=f"{mag}/{mag} {unit}", inline=True)
        embed.add_field(name="⚡ ต่อช็อต", value=f"{per} {unit}", inline=True)
        embed.set_footer(text=f"Operator: {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AmmoAdjustView(discord.ui.View):
    def __init__(self, user_id, char_name, slot):
        super().__init__(timeout=300)
        self.user_id = user_id; self.char_name = char_name; self.slot = slot
        cd = get_char(user_id, char_name)
        r = cd.get("ranged", {}).get(slot, {})
        if r.get("mag_size", 0) > 0:
            unload_btn = discord.ui.Button(label="↩️ เอาออกจากซอง", style=discord.ButtonStyle.secondary, row=1)
            async def unload_callback(inter, _uid=user_id, _cn=char_name, _slot=slot):
                if inter.user.id != _uid:
                    return await inter.response.send_message(embed=discord.Embed(description="[ ACCESS DENIED ]", color=COLOR_ERROR), ephemeral=True)
                await inter.response.send_modal(AmmoUnloadModal(_uid, _cn, _slot, self))
            unload_btn.callback = unload_callback
            self.add_item(unload_btn)

    async def check_owner(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(embed=discord.Embed(description="[ ACCESS DENIED ]", color=COLOR_ERROR), ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        await interaction.response.send_message(
            embed=discord.Embed(description="[ ERROR ] — แผงนี้หมดอายุแล้ว กรุณาใช้ `/ammo` ใหม่อีกครั้ง", color=COLOR_ERROR),
            ephemeral=True)

    @discord.ui.button(label="🔄 Reload", style=discord.ButtonStyle.primary, row=0)
    async def reload_btn(self, interaction, button):
        if not await self.check_owner(interaction): return
        cd = get_char(self.user_id, self.char_name)
        r = cd["ranged"][self.slot]
        mag = r["mag_size"]; unit = r["unit"]
        if mag == 0:
            return await interaction.response.send_message(embed=discord.Embed(description="[ ERROR ] — อาวุธนี้ไม่มีระบบซอง", color=COLOR_ERROR), ephemeral=True)
        needed = mag - r["current_in_mag"]
        actual = min(needed, r["total"])
        r["current_in_mag"] += actual; r["total"] -= actual
        update_char(self.user_id, self.char_name, cd)
        if actual < needed:
            desc = f"Reload ได้แค่ **{actual} {unit}** (ในคลังมีไม่พอบรรจุให้เต็มซอง)\nซอง: {r['current_in_mag']}/{mag} {unit} — คลัง: {r['total']} {unit}"
        else:
            desc = f"Reload สำเร็จ!\nซอง: {r['current_in_mag']}/{mag} {unit} — คลัง: {r['total']} {unit}"
        reload_embed = discord.Embed(title="🔄 [ RELOAD ]", description=desc, color=0x2DB87A)
        reload_embed.set_footer(text=f"Operator: {interaction.user.display_name}")
        status_embed = build_ammo_status_embed(self.char_name, cd, self.slot)
        status_embed.set_footer(text=f"Operator: {interaction.user.display_name}")
        await interaction.response.edit_message(embed=status_embed, view=self)
        await interaction.followup.send(embed=reload_embed, ephemeral=True)

    @discord.ui.button(label="➕ เติมจำนวน", style=discord.ButtonStyle.success, row=0)
    async def add_btn(self, interaction, button):
        if not await self.check_owner(interaction): return
        await interaction.response.send_modal(AmmoAddModal(self.user_id, self.char_name, self.slot, self))

    @discord.ui.button(label="➖ ลดจำนวน", style=discord.ButtonStyle.danger, row=0)
    async def reduce_btn(self, interaction, button):
        if not await self.check_owner(interaction): return
        await interaction.response.send_modal(AmmoReduceModal(self.user_id, self.char_name, self.slot, self))


class AmmoClearConfirmView(discord.ui.View):
    def __init__(self, user_id, char_name, slot):
        super().__init__(timeout=30)
        self.user_id = user_id; self.char_name = char_name; self.slot = slot

    @discord.ui.button(label="✅ ยืนยัน", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if interaction.user.id != self.user_id: return
        cd = get_char(self.user_id, self.char_name)
        if "ranged" in cd and self.slot in cd["ranged"]: del cd["ranged"][self.slot]
        update_char(self.user_id, self.char_name, cd)
        self.stop()
        wpn_name = get_weapon_name(cd, int(self.slot))
        await interaction.response.edit_message(embed=discord.Embed(
            description=f"[ COMPLETE ] — ลบสถานะอาวุธระยะไกลของ **{wpn_name} (Slot {self.slot})** ออกแล้ว", color=COLOR_INFO), view=None)

    @discord.ui.button(label="❌ ยกเลิก", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction, button):
        if interaction.user.id != self.user_id: return
        self.stop()
        await interaction.response.edit_message(embed=discord.Embed(description="[ CANCELLED ]", color=COLOR_INFO), view=None)


async def do_ammo_reload(interaction, user_id, char_name, slot, ephemeral=True):
    cd = get_char(user_id, char_name)
    r = cd["ranged"][slot]
    mag = r["mag_size"]; unit = r["unit"]
    if mag == 0:
        return await interaction.response.send_message(embed=discord.Embed(description="[ ERROR ] — อาวุธนี้ไม่มีระบบซอง", color=COLOR_ERROR), ephemeral=True)
    needed = mag - r["current_in_mag"]
    actual = min(needed, r["total"])
    r["current_in_mag"] += actual; r["total"] -= actual
    update_char(user_id, char_name, cd)
    if actual < needed:
        desc = f"Reload ได้แค่ **{actual} {unit}** (ในคลังมีไม่พอบรรจุให้เต็มซอง)\nซอง: {r['current_in_mag']}/{mag} {unit} — คลัง: {r['total']} {unit}"
    else:
        desc = f"Reload สำเร็จ!\nซอง: {r['current_in_mag']}/{mag} {unit} — คลัง: {r['total']} {unit}"
    embed = discord.Embed(title="🔄 [ RELOAD ]", description=desc, color=0x2DB87A)
    embed.set_footer(text=f"Operator: {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed, ephemeral=ephemeral)


class AmmoReloadSelectView(discord.ui.View):
    def __init__(self, user_id, char_name, ranged_slots, cd):
        super().__init__(timeout=60)
        self.user_id = user_id; self.char_name = char_name
        options = [discord.SelectOption(
            label=f"Slot {s} — {get_weapon_name(cd, int(s))}",
            description=f"{r['current_in_mag']}/{r['mag_size']} {r['unit']}" if r['mag_size'] > 0 else f"{r['total']} {r['unit']}",
            value=s) for s, r in ranged_slots.items()]
        select = discord.ui.Select(placeholder="เลือกอาวุธที่จะ Reload", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(embed=discord.Embed(description="[ ACCESS DENIED ]", color=COLOR_ERROR), ephemeral=True)
        slot = interaction.data["values"][0]
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        await do_ammo_reload(interaction, self.user_id, self.char_name, slot)


class AmmoMainView(discord.ui.View):
    def __init__(self, user_id, char_name):
        super().__init__(timeout=120)
        self.user_id = user_id; self.char_name = char_name

    async def check_owner(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(embed=discord.Embed(description="[ ACCESS DENIED ]", color=COLOR_ERROR), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⚙️ Setup", style=discord.ButtonStyle.primary, row=0)
    async def setup_btn(self, interaction, button):
        if not await self.check_owner(interaction): return
        cd = get_char(self.user_id, self.char_name)
        weapons = [w for w in cd.get("weapons", []) if w.get("name")]
        if not weapons:
            return await interaction.response.send_message(embed=discord.Embed(description="[ ERROR ] — ไม่พบข้อมูลอาวุธ กรุณา sync ก่อน", color=COLOR_ERROR), ephemeral=True)
        if len(weapons) == 1:
            await interaction.response.send_modal(AmmoSetupModal(self.user_id, self.char_name, weapons[0]["slot"]))
        else:
            options = [discord.SelectOption(label=f"Slot {w['slot']} — {w['name']}", value=str(w["slot"])) for w in weapons]
            embed = discord.Embed(title="⚙️ Setup — เลือกอาวุธที่จะลงทะเบียน", description="แสดงเฉพาะอาวุธที่กรอกข้อมูลไว้ในชีทแล้ว", color=COLOR_INFO)
            embed.set_footer(text=f"Operator: {interaction.user.display_name}")
            view = discord.ui.View(timeout=60)
            select = discord.ui.Select(placeholder="เลือกอาวุธ", options=options)
            async def on_select(inter):
                await inter.response.send_modal(AmmoSetupModal(self.user_id, self.char_name, int(inter.data["values"][0])))
            select.callback = on_select
            view.add_item(select)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="🎯 Adjust Ammo", style=discord.ButtonStyle.success, row=0)
    async def adjust_btn(self, interaction, button):
        if not await self.check_owner(interaction): return
        cd = get_char(self.user_id, self.char_name)
        ranged = get_ranged_weapons(cd)
        if not ranged:
            return await interaction.response.send_message(embed=discord.Embed(description="[ ERROR ] — ยังไม่มีอาวุธระยะไกล กรุณาใช้ Setup ก่อน", color=COLOR_ERROR), ephemeral=True)
        if len(ranged) == 1:
            slot = list(ranged.keys())[0]
            embed = build_ammo_status_embed(self.char_name, cd, slot)
            embed.set_footer(text=f"Operator: {interaction.user.display_name}")
            await interaction.response.send_message(embed=embed, view=AmmoAdjustView(self.user_id, self.char_name, slot), ephemeral=True)
        else:
            options = [discord.SelectOption(label=f"Slot {s} — {get_weapon_name(cd, int(s))}", description=f"{r['total']} {r['unit']}", value=s) for s, r in ranged.items()]
            view = discord.ui.View(timeout=60)
            select = discord.ui.Select(placeholder="เลือกอาวุธ", options=options)
            async def on_select(inter):
                slot = inter.data["values"][0]
                embed = build_ammo_status_embed(self.char_name, cd, slot)
                embed.set_footer(text=f"Operator: {inter.user.display_name}")
                await inter.response.edit_message(embed=embed, view=AmmoAdjustView(self.user_id, self.char_name, slot))
            select.callback = on_select
            view.add_item(select)
            await interaction.response.send_message(embed=discord.Embed(title="🎯 Adjust Ammo — เลือกอาวุธ", color=COLOR_INFO), view=view, ephemeral=True)

    @discord.ui.button(label="🔄 Reload", style=discord.ButtonStyle.secondary, row=0)
    async def reload_btn(self, interaction, button):
        if not await self.check_owner(interaction): return
        cd = get_char(self.user_id, self.char_name)
        ranged = {s: r for s, r in get_ranged_weapons(cd).items() if r.get("mag_size", 0) > 0}
        if not ranged:
            return await interaction.response.send_message(embed=discord.Embed(description="[ ERROR ] — ไม่มีอาวุธระยะไกลที่มีระบบซอง", color=COLOR_ERROR), ephemeral=True)
        if len(ranged) == 1:
            await do_ammo_reload(interaction, self.user_id, self.char_name, list(ranged.keys())[0])
        else:
            await interaction.response.send_message(embed=discord.Embed(title="🔄 Reload — เลือกอาวุธ", color=COLOR_INFO),
                view=AmmoReloadSelectView(self.user_id, self.char_name, ranged, cd), ephemeral=True)

    @discord.ui.button(label="🗑️ Clear", style=discord.ButtonStyle.danger, row=0)
    async def clear_btn(self, interaction, button):
        if not await self.check_owner(interaction): return
        cd = get_char(self.user_id, self.char_name)
        ranged = get_ranged_weapons(cd)
        if not ranged:
            return await interaction.response.send_message(embed=discord.Embed(description="[ ERROR ] — ไม่มีอาวุธระยะไกลที่ลงทะเบียนไว้", color=COLOR_ERROR), ephemeral=True)
        if len(ranged) == 1:
            slot = list(ranged.keys())[0]
            wpn_name = get_weapon_name(cd, int(slot))
            embed = discord.Embed(title="[ WARNING ] — ลบสถานะอาวุธระยะไกล",
                description=f"กำลังจะลบสถานะการเป็นอาวุธระยะไกลทั้งหมดของ **{wpn_name} (Slot {slot})** ออก\n\n• จำนวนที่เหลือทั้งหมดจะหายไปถาวร\n• อาวุธชิ้นนี้จะไม่ถูกนับเป็นอาวุธระยะไกลอีกต่อไป\n• การกระทำนี้ไม่สามารถยกเลิกได้",
                color=COLOR_FUMBLE)
            await interaction.response.send_message(embed=embed, view=AmmoClearConfirmView(self.user_id, self.char_name, slot), ephemeral=True)
        else:
            options = [discord.SelectOption(label=f"Slot {s} — {get_weapon_name(cd, int(s))}", description=f"{r['total']} {r['unit']}", value=s) for s, r in ranged.items()]
            view = discord.ui.View(timeout=60)
            select = discord.ui.Select(placeholder="เลือกอาวุธที่จะลบ", options=options)
            async def on_select(inter):
                slot = inter.data["values"][0]
                wpn_name = get_weapon_name(cd, int(slot))
                embed = discord.Embed(title="[ WARNING ] — ลบสถานะอาวุธระยะไกล",
                    description=f"กำลังจะลบสถานะการเป็นอาวุธระยะไกลทั้งหมดของ **{wpn_name} (Slot {slot})** ออก\n\n• จำนวนที่เหลือทั้งหมดจะหายไปถาวร\n• อาวุธชิ้นนี้จะไม่ถูกนับเป็นอาวุธระยะไกลอีกต่อไป\n• การกระทำนี้ไม่สามารถยกเลิกได้",
                    color=COLOR_FUMBLE)
                await inter.response.edit_message(embed=embed, view=AmmoClearConfirmView(self.user_id, self.char_name, slot))
            select.callback = on_select
            view.add_item(select)
            await interaction.response.send_message(embed=discord.Embed(title="🗑️ Clear — เลือกอาวุธที่จะลบ", color=COLOR_INFO), view=view, ephemeral=True)


@tree.command(name="ammo", description="จัดการระบบอาวุธระยะไกล")
@app_commands.describe(char="ชื่อตัวละคร (ไม่ใส่ = default)")
async def ammo_cmd(interaction: discord.Interaction, char: str = None):
    char_name, cd, err = resolve_char(interaction.user.id, char)
    if err: return await interaction.response.send_message(embed=err, ephemeral=True)
    embed = discord.Embed(title="🎯 ระบบอาวุธระยะไกล",
        description=("**⚙️ Setup** — ลงทะเบียนอาวุธให้เป็นอาวุธระยะไกล\n"
                     "**🎯 Adjust Ammo** — ดูสถานะและปรับจำนวนที่เหลือ พร้อมปุ่ม Reload\n"
                     "**🔄 Reload** — เติมซองทันที\n"
                     "**🗑️ Clear** — ลบสถานะอาวุธระยะไกลออก"),
        color=COLOR_INFO)
    embed.set_footer(text=f"Operator: {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed, view=AmmoMainView(interaction.user.id, char_name), ephemeral=True)


# ── /party ─────────────────────────────────────────────────────────────────

def load_parties() -> dict:
    data = load_data()
    return data.get("_parties", {})

def save_parties(parties: dict):
    data = load_data()
    data["_parties"] = parties
    save_data(data)

def build_party_embed(party_name: str, party: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"🏦 Party Wallet — {party_name}",
        color=COLOR_INFO)
    embed.add_field(name="💰 เงิน", value=f"**{party.get('gold', 0):,} LMD**", inline=False)
    items = party.get("key_items", [])
    if items:
        item_lines = "\n".join(
            f"• **{it['name']}** — เก็บโดย: {it.get('holder', '?')}"
            for it in items)
        embed.add_field(name="🗝️ Key Items", value=item_lines, inline=False)
        embed.set_footer(text="เลือก dropdown เพื่ออ่านคำอธิบายไอเทม")
    else:
        embed.add_field(name="🗝️ Key Items", value="ยังไม่มี Key Item", inline=False)
    return embed


class PartyWalletModal(discord.ui.Modal, title="สร้าง / อัปเดตกระเป๋าเงินปาร์ตี้"):
    party_name = discord.ui.TextInput(label="ชื่อปาร์ตี้", placeholder="เช่น ทีม Rhodes Island", required=True)
    gold = discord.ui.TextInput(label="จำนวน LMD ตอนนี้", placeholder="เช่น 500", required=True)
    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.gold.value.replace(",", ""))
            assert amount >= 0
        except:
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ERROR ] — จำนวน LMD ต้องเป็นตัวเลข >= 0", color=COLOR_ERROR), ephemeral=True)
        parties = load_parties()
        name = self.party_name.value.strip()
        if name not in parties:
            parties[name] = {"gold": amount, "key_items": []}
        else:
            parties[name]["gold"] = amount
        save_parties(parties)
        embed = discord.Embed(
            description=f"[ COMPLETE ] — บันทึกกระเป๋าเงินของ **{name}** เรียบร้อยแล้ว\n💰 {amount:,} LMD",
            color=COLOR_INFO)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class PartyItemAddModal(discord.ui.Modal, title="เพิ่ม Key Item"):
    item_name = discord.ui.TextInput(label="ชื่อไอเทม", placeholder="เช่น แผนที่โบราณ", required=True)
    item_holder = discord.ui.TextInput(label="ใครเก็บไว้", placeholder="เช่น Valen", required=True)
    item_desc = discord.ui.TextInput(label="คำอธิบาย", placeholder="เช่น แผนที่ที่นำไปสู่ขุมทรัพย์", required=True, style=discord.TextStyle.paragraph)
    def __init__(self, party_name: str, check_view=None, parent_interaction=None):
        super().__init__()
        self.party_name = party_name
        self.check_view = check_view
        self.parent_interaction = parent_interaction
    async def on_submit(self, interaction: discord.Interaction):
        parties = load_parties()
        if self.party_name not in parties:
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ERROR ] — ไม่พบปาร์ตี้นี้", color=COLOR_ERROR), ephemeral=True)
        parties[self.party_name]["key_items"].append({
            "name": self.item_name.value.strip(),
            "holder": self.item_holder.value.strip(),
            "desc": self.item_desc.value.strip()
        })
        save_parties(parties)
        embed = build_party_embed(self.party_name, parties[self.party_name])
        await interaction.response.send_message(
            embed=discord.Embed(description=f"[ COMPLETE ] — เพิ่ม **{self.item_name.value}** ให้ **{self.party_name}** แล้ว", color=COLOR_INFO),
            ephemeral=True)
        if self.check_view and self.parent_interaction:
            try:
                await self.parent_interaction.edit_original_response(embed=embed, view=self.check_view)
            except: pass


class PartyGoldModal(discord.ui.Modal, title="เพิ่ม / ลด LMD"):
    add_amount = discord.ui.TextInput(label="เพิ่ม LMD (ไม่บังคับ)", placeholder="เช่น 200", required=False)
    cut_amount = discord.ui.TextInput(label="ลด LMD (ไม่บังคับ)", placeholder="เช่น 100", required=False)
    def __init__(self, party_name: str, view):
        super().__init__()
        self.party_name = party_name
        self.pv = view
    async def on_submit(self, interaction: discord.Interaction):
        parties = load_parties()
        party = parties.get(self.party_name)
        if not party:
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ERROR ] — ไม่พบปาร์ตี้นี้", color=COLOR_ERROR), ephemeral=True)
        add_val = 0
        cut_val = 0
        try:
            if self.add_amount.value.strip():
                add_val = int(self.add_amount.value.replace(",", ""))
                assert add_val >= 0
        except:
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ERROR ] — จำนวนที่เพิ่มต้องเป็นตัวเลข >= 0", color=COLOR_ERROR), ephemeral=True)
        try:
            if self.cut_amount.value.strip():
                cut_val = int(self.cut_amount.value.replace(",", ""))
                assert cut_val >= 0
        except:
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ERROR ] — จำนวนที่ลดต้องเป็นตัวเลข >= 0", color=COLOR_ERROR), ephemeral=True)
        old = party["gold"]
        party["gold"] = max(0, old + add_val - cut_val)
        save_parties(parties)
        embed = build_party_embed(self.party_name, party)
        await interaction.response.edit_message(embed=embed, view=self.pv)


class PartyManageView(discord.ui.View):
    def __init__(self, party_name: str, is_prts_user: bool):
        super().__init__(timeout=300)
        self.party_name = party_name
        self.is_prts_user = is_prts_user
        # เพิ่ม dropdown อ่านคำอธิบายไอเทม
        self._refresh_item_select()

    def _refresh_item_select(self):
        # ลบ select เดิมออกก่อน (ถ้ามี)
        for item in self.children[:]:
            if isinstance(item, discord.ui.Select) and item.custom_id == "item_desc_select":
                self.remove_item(item)
        parties = load_parties()
        items = parties.get(self.party_name, {}).get("key_items", [])
        if not items:
            return
        options = [
            discord.SelectOption(
                label=it["name"][:25],
                description=f"เก็บโดย: {it.get('holder', '?')}",
                value=str(i))
            for i, it in enumerate(items)]
        select = discord.ui.Select(
            placeholder="🗝️ เลือกไอเทมเพื่ออ่านคำอธิบาย",
            options=options,
            custom_id="item_desc_select",
            row=2)
        async def on_select(inter: discord.Interaction):
            idx = int(inter.data["values"][0])
            pts = load_parties()
            it = pts.get(self.party_name, {}).get("key_items", [])[idx]
            embed = discord.Embed(
                title=f"🗝️ {it['name']}",
                color=COLOR_INFO)
            embed.add_field(name="เก็บโดย", value=it.get("holder", "?"), inline=True)
            embed.add_field(name="คำอธิบาย", value=it.get("desc", "—"), inline=False)
            await inter.response.send_message(embed=embed, ephemeral=True)
        select.callback = on_select
        self.add_item(select)

    async def check_prts(self, interaction: discord.Interaction) -> bool:
        if not self.is_prts_user:
            await interaction.response.send_message(
                embed=discord.Embed(description="[ ACCESS DENIED ] — PRTS Only", color=COLOR_ERROR), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="💰 เพิ่ม/ลด LMD", style=discord.ButtonStyle.primary)
    async def gold_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_prts(interaction): return
        await interaction.response.send_modal(PartyGoldModal(self.party_name, self))

    @discord.ui.button(label="🗝️ จัดการ Key Items", style=discord.ButtonStyle.secondary)
    async def items_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_prts(interaction): return
        parties = load_parties()
        party = parties.get(self.party_name, {})
        items = party.get("key_items", [])
        view = PartyItemManageView(self.party_name, items, self, interaction)
        await interaction.response.send_message(
            embed=discord.Embed(title=f"🗝️ จัดการ Key Items — {self.party_name}", color=COLOR_INFO),
            view=view, ephemeral=True)


class PartyItemManageView(discord.ui.View):
    def __init__(self, party_name: str, items: list, parent_view, parent_interaction=None):
        super().__init__(timeout=120)
        self.party_name = party_name
        self.items = items
        self.parent_view = parent_view
        self.parent_interaction = parent_interaction

    @discord.ui.button(label="➕ เพิ่ม Key Item", style=discord.ButtonStyle.success)
    async def add_item(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            PartyItemAddModal(self.party_name, self.parent_view, self.parent_interaction))

    @discord.ui.button(label="🗑️ ลบ Key Item", style=discord.ButtonStyle.danger)
    async def remove_item(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.items:
            return await interaction.response.send_message(
                embed=discord.Embed(description="[ ERROR ] — ยังไม่มี Key Item", color=COLOR_ERROR), ephemeral=True)
        options = [discord.SelectOption(label=it["name"], description=it["desc"][:50], value=str(i))
                   for i, it in enumerate(self.items)]
        view = discord.ui.View(timeout=60)
        select = discord.ui.Select(placeholder="เลือก Key Item ที่จะลบ", options=options)
        async def on_select(inter):
            idx = int(inter.data["values"][0])
            parties = load_parties()
            removed = parties[self.party_name]["key_items"].pop(idx)
            save_parties(parties)
            embed = build_party_embed(self.party_name, parties[self.party_name])
            await inter.response.edit_message(
                embed=discord.Embed(description=f"[ COMPLETE ] — ลบ **{removed['name']}** ออกแล้ว", color=COLOR_INFO),
                view=None)
            # อัปเดต embed หลักผ่าน parent_interaction
            if self.parent_interaction:
                try:
                    await self.parent_interaction.edit_original_response(embed=embed, view=self.parent_view)
                except: pass
        select.callback = on_select
        view.add_item(select)
        await interaction.response.send_message(
            embed=discord.Embed(title="🗑️ เลือก Key Item ที่จะลบ", color=COLOR_INFO),
            view=view, ephemeral=True)


@tree.command(name="party-wallet", description="[PRTS] สร้างหรืออัปเดตกระเป๋าเงินปาร์ตี้")
async def party_wallet(interaction: discord.Interaction):
    if not is_prts(interaction.user):
        return await interaction.response.send_message(
            embed=discord.Embed(description=f"[ ACCESS DENIED ] — PRTS Only", color=COLOR_ERROR), ephemeral=True)
    await interaction.response.send_modal(PartyWalletModal())


@tree.command(name="party-item", description="[PRTS] เพิ่ม Key Item ให้ปาร์ตี้")
@app_commands.describe(party="ชื่อปาร์ตี้ที่จะเพิ่มไอเทม")
async def party_item(interaction: discord.Interaction, party: str = None):
    if not is_prts(interaction.user):
        return await interaction.response.send_message(
            embed=discord.Embed(description=f"[ ACCESS DENIED ] — PRTS Only", color=COLOR_ERROR), ephemeral=True)
    parties = load_parties()
    if not parties:
        return await interaction.response.send_message(
            embed=discord.Embed(description="[ ERROR ] — ยังไม่มีปาร์ตี้ กรุณาใช้ `/party-wallet` ก่อน", color=COLOR_ERROR), ephemeral=True)
    if party and party in parties:
        await interaction.response.send_modal(PartyItemAddModal(party))
    elif len(parties) == 1:
        await interaction.response.send_modal(PartyItemAddModal(list(parties.keys())[0]))
    else:
        options = [discord.SelectOption(label=n, value=n) for n in parties]
        view = discord.ui.View(timeout=60)
        select = discord.ui.Select(placeholder="เลือกปาร์ตี้", options=options)
        async def on_select(inter):
            await inter.response.send_modal(PartyItemAddModal(inter.data["values"][0]))
        select.callback = on_select
        view.add_item(select)
        await interaction.response.send_message(
            embed=discord.Embed(title="เลือกปาร์ตี้ที่จะเพิ่ม Key Item", color=COLOR_INFO),
            view=view, ephemeral=True)


@tree.command(name="party-check", description="ดูเงินและ Key Items ของปาร์ตี้")
async def party_check(interaction: discord.Interaction):
    parties = load_parties()
    if not parties:
        return await interaction.response.send_message(
            embed=discord.Embed(description="[ ERROR ] — ยังไม่มีปาร์ตี้ กรุณาให้ PRTS ใช้ `/party-wallet` ก่อน", color=COLOR_ERROR), ephemeral=True)
    if len(parties) == 1:
        name = list(parties.keys())[0]
        party = parties[name]
        embed = build_party_embed(name, party)
        view = PartyManageView(name, is_prts(interaction.user))
        return await interaction.response.send_message(embed=embed, view=view)
    options = [discord.SelectOption(label=n, description=f"{p.get('gold',0):,} LMD", value=n) for n, p in parties.items()]
    class PartySelectView(discord.ui.View):
        def __init__(self_v):
            super().__init__(timeout=60)
        @discord.ui.select(placeholder="เลือกปาร์ตี้ที่ต้องการดู", options=options)
        async def on_select(self_v, inter, select):
            pname = select.values[0]
            party = load_parties().get(pname, {})
            embed = build_party_embed(pname, party)
            view = PartyManageView(pname, is_prts(inter.user))
            await inter.response.edit_message(embed=embed, view=view)
    await interaction.response.send_message(
        embed=discord.Embed(title="🏦 เลือกปาร์ตี้ที่ต้องการดู", color=COLOR_INFO),
        view=PartySelectView())


# ── /help ──────────────────────────────────────────────────────────────────

HELP_OPTIONS = [
    discord.SelectOption(label="🚀 ขั้นที่ 1 — ลงทะเบียนตัวละคร", value="step1"),
    discord.SelectOption(label="❤️ ขั้นที่ 2 — จัดการ HP / AP", value="step2"),
    discord.SelectOption(label="🎲 ขั้นที่ 3 — การทอยและโจมตี", value="step3"),
    discord.SelectOption(label="⚔️ ขั้นที่ 4 — Initiative", value="step4"),
    discord.SelectOption(label="🔐 สำหรับ PRTS", value="prts"),
    discord.SelectOption(label="🎯 Optional — ระบบอาวุธระยะไกล", value="optional"),
]
_SHEET_URL = "https://docs.google.com/spreadsheets/d/1pSM4shNdx9ldRqVt01w8kvsnsQXg8WB9rqFwl7nQ02U/edit?gid=2113616821#gid=2113616821"

def build_help_main_embed():
    e = discord.Embed(title="📖 คู่มือการใช้งาน Proto PCS",
        description="โปรดเลือกหัวข้อที่ท่านต้องการเรียนรู้", color=COLOR_CYAN)
    e.add_field(name="🚀 ขั้นที่ 1 — ลงทะเบียนตัวละคร",
        value="จุดเริ่มต้นที่ทุกคนต้องทำก่อน ระบบจะดึงข้อมูลตัวละครจาก Google Sheets มาให้อัตโนมัติ", inline=False)
    e.add_field(name="❤️ ขั้นที่ 2 — จัดการ HP / AP",
        value="วิธีดูสถานะและอัปเดต HP/AP ระหว่างเกม รวมถึง Temporary HP", inline=False)
    e.add_field(name="🎲 ขั้นที่ 3 — การทอยและโจมตี",
        value="วิธีใช้คำสั่งทอยเต๋า Attack Roll และระบบ Damage พร้อม Critical Hit", inline=False)
    e.add_field(name="⚔️ ขั้นที่ 4 — Initiative",
        value="วิธีทอย Initiative กำหนดลำดับการต่อสู้ กดปุ่มเดียวก็ทอยได้เลย", inline=False)
    e.add_field(name="🔐 สำหรับ PRTS",
        value="คำสั่งพิเศษสำหรับ Game Master เช่น ดู HP ปาร์ตี้ ควบคุม Initiative และจัดการ NPC", inline=False)
    e.add_field(name="🎯 Optional — ระบบอาวุธระยะไกล",
        value="สำหรับผู้ที่มีปืน ธนู หน้าไม้ มีดบิน ระบบจะติดตามและหักจำนวนให้อัตโนมัติ", inline=False)
    e.set_footer(text="PCS TACTICAL SUPPORT SYSTEM")
    return e

def build_help_page_embed(page: str):
    if page == "step1":
        e = discord.Embed(title="🚀 ขั้นที่ 1 — ลงทะเบียนตัวละคร",
            description="ก่อนใช้คำสั่งอื่นทุกอัน ต้องทำขั้นตอนนี้ก่อนเสมอ\nบอทจะดึงข้อมูลทั้งหมดจาก Google Sheets มาให้อัตโนมัติ ไม่ต้องกรอกซ้ำในดิสคอร์ด",
            color=COLOR_INFO)
        e.add_field(name="📄 ขั้นตอนที่ 1 — เปิดชีทตัวละคร",
            value=f"คัดลอก template แล้วกรอกข้อมูลตัวละครให้ครบ\n{_SHEET_URL}\n\nชื่อแท็บในชีท (แถบล่างของ Sheets) คือชื่อที่ใช้กับบอท เช่น แท็บ `Valen`", inline=False)
        e.add_field(name="⚙️ ขั้นตอนที่ 2 — ลงทะเบียน",
            value="พิมพ์ `/set-char char:ชื่อแท็บ`\nไม่ต้องพิมพ์ชื่อเต็มก็ได้ เช่น `Val` แทน `Valen D\'Arcangelo`", inline=False)
        e.add_field(name="⭐ ตัวละคร Default คืออะไร?",
            value="คำสั่งทุกอย่างจะใช้ตัวละคร default อัตโนมัติเมื่อคุณไม่ระบุชื่อ\nตัวแรกที่ลงทะเบียนจะเป็น default เสมอ\nเปลี่ยนได้ด้วย `/switch char:ชื่อ`", inline=False)
        e.add_field(name="🔄 ถ้าแก้ชีทแล้วอยากให้บอทรับรู้",
            value="พิมพ์ `/sync` หรือรอได้เลย เพราะบอทจะ sync ให้อัตโนมัติทุก 25 นาที\nHP/AP ปัจจุบันจะไม่เปลี่ยน มีแค่ stat และอาวุธที่อัปเดต", inline=False)
        e.set_footer(text="✅ ทำขั้นตอนนี้ครั้งเดียวก็พอ ไม่ต้องทำซ้ำทุกครั้งที่เล่น")
    elif page == "step2":
        e = discord.Embed(title="❤️ ขั้นที่ 2 — จัดการ HP / AP",
            description="ใช้คำสั่งเหล่านี้เพื่อดูและอัปเดตสถานะตัวละครของคุณระหว่างเกม", color=COLOR_INFO)
        e.add_field(name="📊 ดูสถานะทั้งหมด",
            value="`/mystats` — แสดง HP/AP/AC/SPD, ค่า stat, อาวุธ และ Modifier ทั้งหมด\nผลจะแสดงให้ทุกคนในช่องเห็น", inline=False)
        e.add_field(name="🛡️ แผงควบคุม HP / AP",
            value="`/stat-update` — เปิดแผงควบคุม มีปุ่ม 3 แบบ:\n• **Add** — เพิ่ม (กดแล้วพิมพ์ตัวเลข)\n• **Cut** — ลด (กดแล้วพิมพ์ตัวเลข)\n• **Reset** — คืนกลับเต็ม\nกดได้เฉพาะเจ้าของเท่านั้น แผงหมดอายุหลัง 1 ชั่วโมง", inline=False)
        e.add_field(name="💛 Temporary HP",
            value="Temp HP คือ HP สำรอง โดนโจมตีจะหักจาก Temp ก่อน HP จริงไม่ถูกแตะ\n• รับแบบตัวเลข: `/temp-hp amount:6`\n• รับแบบทอย: `/temp-hp dice:1d6+1`\n• ลบออก: `/temp-hp clear:true`\nTemp HP ไม่ซ้อนกัน ได้ใหม่ต้องเลือกว่าจะเก็บอันเดิมหรือเปลี่ยน", inline=False)
        e.set_footer(text="PCS TACTICAL SUPPORT SYSTEM")
    elif page == "step3":
        e = discord.Embed(title="🎲 ขั้นที่ 3 — การทอยและโจมตี",
            description="มีคำสั่งทอยสองแบบ และคำสั่งโจมตีที่มีระบบ Damage ในตัว", color=COLOR_INFO)
        e.add_field(name="🎲 ทอย Skill Check (d20 + stat)",
            value="`/roll stat:wis`\nใส่เพิ่มได้: `modifiers:+2` / `adv_dis:Advantage` / `comment:ข้อความ`\n💀 ออก 1 = FUMBLE | ⭐ ออก 20 = CRITICAL", inline=False)
        e.add_field(name="🎲 ทอย Custom Dice",
            value="`/roll dice:2d6` — ทอยเต๋าอะไรก็ได้\nใส่ `modifiers` และ `comment` เพิ่มได้เช่นกัน", inline=False)
        e.add_field(name="⚔️ Attack Roll",
            value="`/atk-roll` — ทอย Attack พร้อมแสดง Damage Dice\nระบุ `weapon:1` สำหรับอาวุธหลัก หรือ `weapon:2`/`3` สำหรับรอง\n\nหลังทอยจะมีปุ่ม:\n• **⚔️ Damage** / **⚔️ Damage + Modifiers** / **💨 Miss**\n⭐ ออก 20 = Critical Hit เต๋า Damage เพิ่มเป็น 2 เท่า", inline=False)
        e.set_footer(text="✅ ถ้า stat สูงสุดเท่ากันหลายตัว บอทจะให้เลือกก่อนว่าจะใช้ตัวไหน")
    elif page == "step4":
        e = discord.Embed(title="⚔️ ขั้นที่ 4 — Initiative",
            description="Initiative คือการกำหนดลำดับว่าใครได้ต่อสู้ก่อน คุณไม่ต้องพิมพ์คำสั่งอะไรเอง", color=COLOR_INFO)
        e.add_field(name="วิธีทอย Initiative",
            value="1. รอให้ PRTS เปิด Initiative Phase ก่อน\n2. บอทจะส่งปุ่ม **🎲 Roll Initiative** มาในช่อง\n3. กดปุ่มนั้นได้เลย บอทจะทอย 1d20+WIS ให้อัตโนมัติ\n4. ผลของคุณจะโชว์ให้ทุกคนเห็นทันที\n5. รอ PRTS ประกาศลำดับสุดท้าย", inline=False)
        e.add_field(name="ผลของ NPC จะซ่อนไว้ก่อน",
            value="PRTS จะทอยให้ NPC แยก แต่ผลจะถูกซ่อนไว้จนกว่าจะประกาศลำดับ\nทุกคนจะเห็นพร้อมกันตอนที่ PRTS สรุปลำดับเท่านั้น", inline=False)
        e.set_footer(text="⚠️ ต้องลงทะเบียนตัวละคร (ขั้นที่ 1) ก่อน ถึงจะกดปุ่มนี้ได้")
    elif page == "prts":
        e = discord.Embed(title="🔐 สำหรับ PRTS",
            description="คำสั่งเหล่านี้ใช้ได้เฉพาะ Role **PRTS** เท่านั้น", color=COLOR_PURPLE)
        e.add_field(name="👥 ดูสถานะปาร์ตี้",
            value="`/player-check` — HP/AP ของทุกคน ทุกคนเห็น\n`/npc-check` — HP/AP ของ NPC เห็นเฉพาะ PRTS", inline=False)
        e.add_field(name="🎯 เรียก Attack Roll",
            value="`/atk @ผู้เล่น` — แจ้งให้ผู้เล่นทราบว่าต้องทอย Attack\nบอทจะใช้ stat สูงสุดของตัวละคร default ของเขาอัตโนมัติ", inline=False)
        e.add_field(name="⚔️ ควบคุม Initiative",
            value="`/initiative` — เปิด Phase ส่งปุ่มให้ทุกคนกด\n`/initiative-npc name:ชื่อ bonus:2` — ทอยให้ NPC (ผลซ่อนไว้ก่อน)\n`/initiative-order` — ปิด Phase ประกาศลำดับทั้งหมด", inline=False)
        e.set_footer(text="[ PROTO PCS — FOR INTERNAL USE ONLY ]")
    else:
        e = discord.Embed(title="🎯 Optional — ระบบอาวุธระยะไกล",
            description="สำหรับผู้ที่มีอาวุธระยะไกลเท่านั้น ถ้าไม่มีข้ามได้เลย", color=COLOR_INFO)
        e.add_field(name="⚙️ ลงทะเบียนอาวุธ (ทำครั้งเดียว)",
            value="`/ammo` → กด **Setup** → เลือกอาวุธ → กรอกฟอร์ม\n• จำนวนทั้งหมดที่มี / สรรพนาม / ขนาดซอง / จำนวนต่อช็อต\nถ้าไม่มีระบบซองให้ใส่ 000 ในช่องขนาดซอง", inline=False)
        e.add_field(name="🎯 ระหว่างเกม — บอทจัดการให้เอง",
            value="ใช้ `/atk-roll` ตามปกติ บอทจะหักจำนวนให้อัตโนมัติทุกครั้ง\nแสดงสถานะ ซอง / คลัง / รวม ใต้ผลทอยเสมอ\nถ้าซองหมด บอทจะแจ้งเตือนและมีปุ่ม Reload ให้กดได้เลย", inline=False)
        e.add_field(name="🔄 จัดการกระสุน",
            value="`/ammo` → **Adjust Ammo** — ดูสถานะและปรับจำนวน\n`/ammo` → **Reload** — เติมซองจากคลังอัตโนมัติ", inline=False)
        e.set_footer(text="✅ Setup ครั้งเดียวก็พอ บอทจะจำไว้ให้ตลอด")
    return e


class HelpMainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.select(placeholder="เลือกหัวข้อที่ต้องการ...", options=HELP_OPTIONS)
    async def select_page(self, interaction: discord.Interaction, select: discord.ui.Select):
        page = select.values[0]
        await interaction.response.edit_message(embed=build_help_page_embed(page), view=HelpPageView())


class HelpPageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="← กลับไปหน้าสารบัญของคู่มือ", style=discord.ButtonStyle.secondary)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_help_main_embed(), view=HelpMainView())


@tree.command(name="help", description="แสดงคู่มือการใช้งาน")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_help_main_embed(), view=HelpMainView())


# ── /pcs ───────────────────────────────────────────────────────────────────

@tree.command(name="pcs", description="[PRTS] ให้บอทส่งข้อความในนามของ PCS")
@app_commands.describe(message="ข้อความที่จะให้บอทพูด")
async def pcs_cmd(interaction: discord.Interaction, message: str):
    if not is_prts(interaction.user):
        return await interaction.response.send_message(
            embed=discord.Embed(
                description=f"[ ACCESS DENIED ] — {interaction.user.mention} : Insufficient clearance. PRTS Only.",
                color=COLOR_ERROR),
            ephemeral=True)
    await interaction.response.send_message("✅", ephemeral=True)
    await interaction.channel.send(message)
    print(f"[ PCS ] — {interaction.user.display_name} ({interaction.user.id}): {message}")


# ── Events ─────────────────────────────────────────────────────────────────

async def auto_sync_task():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(12 * 60 * 60)  # 12 ชั่วโมง
        print("[ AUTO SYNC ] — กำลัง sync ข้อมูลทุกตัวละคร...")
        data = load_data()
        synced = 0
        failed = 0
        fail_reasons = []
        for uid, user in data.items():
            if uid.startswith("_"): continue  # ข้าม _parties
            for char_name, cd in user.get("chars", {}).items():
                sheet_tab = cd.get("sheet_tab") or char_name
                is_npc = cd.get("is_npc", False)
                try:
                    new_data = fetch_npc_sheet(sheet_tab) if is_npc else fetch_sheet(sheet_tab)
                    if new_data:
                        new_data["current_hp"] = min(cd["current_hp"], new_data["max_hp"])
                        new_data["current_ap"] = min(cd["current_ap"], new_data["max_ap"])
                        if cd.get("temp_hp"): new_data["temp_hp"] = cd["temp_hp"]
                        if cd.get("ranged"): new_data["ranged"] = cd["ranged"]
                        if is_npc: new_data["is_npc"] = True
                        update_char(int(uid), char_name, new_data)
                        synced += 1
                    else:
                        failed += 1
                        fail_reasons.append(f"{char_name} (sheet_tab='{sheet_tab}') — หาแท็บไม่เจอ หรือ Google Sheets ไม่ตอบสนอง")
                except Exception as e:
                    print(f"[ AUTO SYNC ERROR ] {char_name}: {type(e).__name__}: {e}")
                    failed += 1
                    fail_reasons.append(f"{char_name} (sheet_tab='{sheet_tab}') — {type(e).__name__}: {e}")
                # หยุดพักระหว่างตัวละครเพื่อไม่ชน Google Sheets rate limit
                await asyncio.sleep(2)
        print(f"[ AUTO SYNC ] — เสร็จแล้ว: {synced} ✅  {failed} ❌")
        if fail_reasons:
            print("[ AUTO SYNC ] — รายละเอียดที่ล้มเหลว:")
            for reason in fail_reasons:
                print(f"    ⚠️ {reason}")

@bot.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=1474630417955950692))
    await tree.sync()
    bot.loop.create_task(auto_sync_task())
    print(f"[ SYSTEM ONLINE ] — Logged in as {bot.user}")
    print(f"[ AUTO SYNC ] — จะ sync ทุก 25 นาที")

bot.run(BOT_TOKEN)

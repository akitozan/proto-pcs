from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN      = os.getenv("BOT_TOKEN", "ใส่ TOKEN ของคุณตรงนี้")
SHEET_ID       = os.getenv("SHEET_ID", "ใส่ Google Sheets ID ตรงนี้")
CREDENTIALS    = os.getenv("CREDENTIALS_FILE", "credentials.json")
NPC_SHEET_ID   = os.getenv("NPC_SHEET_ID", "")
PRTS_ROLE      = "PRTS"
DATA_FILE      = os.getenv("DATA_FILE", "/app/data/data.json")

# Colors
COLOR_NORMAL   = 0x9B9EA4
COLOR_FUMBLE   = 0xC0392B
COLOR_CRITICAL = 0xF6D800
COLOR_ERROR    = 0xFF0000
COLOR_INFO     = 0x2980B9
COLOR_WARN     = 0xF6D800
COLOR_PURPLE   = 0x7B61FF
COLOR_CYAN     = 0x00AEEF

# Class base stats
CLASS_HP = {
    "Defender": 10, "Guard": 8, "Medic": 7,
    "Caster": 6,
    "Sniper (หน้าไม้)": 6, "Sniper (ปืนเล็ก)": 6, "Sniper (ปืนใหญ่)": 6,
}
CLASS_AP = {
    "Medic": 15, "Caster": 15,
}
CLASS_DAMAGE = {
    "Defender":          lambda s: "1d5",
    "Medic":             lambda s: f"1d4+WIS({s['wis']})",
    "Guard":             lambda s: f"1d8+MAX({max(s['str'],s['dex'],s['int'],s['wis'])})",
    "Caster":            lambda s: f"1d8+INT({s['int']})",
    "Sniper (หน้าไม้)":  lambda s: f"1d8+DEX({s['dex']})",
    "Sniper (ปืนเล็ก)":  lambda s: f"2d6+DEX({s['dex']})",
    "Sniper (ปืนใหญ่)":  lambda s: f"2d8+DEX({s['dex']})",
}

def fmt(val: int) -> str:
    if val > 0: return f"+{val}"
    if val < 0: return str(val)
    return ""

def fmt_stat(val: int) -> str:
    return f"+{val}" if val >= 0 else str(val)

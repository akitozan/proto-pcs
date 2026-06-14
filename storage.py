import json, os, re
import gspread
from google.oauth2.service_account import Credentials
from config import DATA_FILE, SHEET_ID, NPC_SHEET_ID, CREDENTIALS, CLASS_HP, CLASS_AP, CLASS_DAMAGE, fmt, fmt_stat

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# ── Local JSON storage ─────────────────────────────────────────────────────

def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user(user_id: int) -> dict:
    return load_data().get(str(user_id), {})

def save_user(user_id: int, user_data: dict):
    data = load_data()
    data[str(user_id)] = user_data
    save_data(data)

def get_char(user_id: int, char_name: str) -> dict | None:
    user = get_user(user_id)
    return user.get("chars", {}).get(char_name)

def get_default_char(user_id: int) -> tuple[str, dict] | tuple[None, None]:
    user = get_user(user_id)
    default = user.get("default")
    if not default:
        return None, None
    char = user.get("chars", {}).get(default)
    return default, char

def set_default(user_id: int, char_name: str):
    data = load_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"chars": {}, "default": None}
    data[uid]["default"] = char_name
    save_data(data)

def update_char(user_id: int, char_name: str, char_data: dict):
    data = load_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"chars": {}, "default": None}
    data[uid]["chars"][char_name] = char_data
    save_data(data)

def delete_char(user_id: int, char_name: str):
    data = load_data()
    uid = str(user_id)
    if uid not in data:
        return
    chars = data[uid].get("chars", {})
    if char_name in chars:
        del chars[char_name]
    if data[uid].get("default") == char_name:
        data[uid]["default"] = None
    save_data(data)

def list_chars(user_id: int) -> list[str]:
    user = get_user(user_id)
    return list(user.get("chars", {}).keys())

# ── Partial match ──────────────────────────────────────────────────────────

def find_char(user_id: int, query: str) -> tuple[str, dict] | tuple[None, None] | tuple[list, None]:
    user = get_user(user_id)
    chars = user.get("chars", {})
    q = query.lower()
    matches = [name for name in chars if name.lower().startswith(q)]
    if len(matches) == 1:
        return matches[0], chars[matches[0]]
    if len(matches) > 1:
        return matches, None
    return None, None

def find_char_global(query: str) -> list[tuple[int, str, dict]]:
    data = load_data()
    q = query.lower()
    results = []
    for uid, user in data.items():
        for name, char in user.get("chars", {}).items():
            if name.lower().startswith(q):
                results.append((int(uid), name, char))
    return results

# ── Google Sheets ──────────────────────────────────────────────────────────

def get_gc():
    creds_json = os.getenv("GOOGLE_CREDENTIALS")
    if creds_json:
        # อ่านจาก environment variable
        creds_info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    else:
        # fallback อ่านจากไฟล์ (สำหรับ local)
        creds = Credentials.from_service_account_file(CREDENTIALS, scopes=SCOPES)
    return gspread.authorize(creds)

def find_sheet_tab(sh, query: str) -> str | None:
    """Find sheet tab name by partial match"""
    q = query.lower()
    matches = [ws.title for ws in sh.worksheets() if ws.title.lower().startswith(q)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Return exact match if exists
        exact = [m for m in matches if m.lower() == q]
        return exact[0] if exact else None
    return None

def fetch_sheet(query: str) -> dict | None:
    """Fetch operator data — query can be partial sheet tab name"""
    try:
        gc = get_gc()
        sh = gc.open_by_key(SHEET_ID)

        # Try partial match on tab name
        tab_name = find_sheet_tab(sh, query)
        if not tab_name:
            return None

        ws = sh.worksheet(tab_name)
        rows = ws.get_all_values()
        result = parse_sheet(rows)
        result["sheet_tab"] = tab_name
        return result
    except Exception as e:
        print(f"[Sheets Error] {type(e).__name__}: {e}")
        return None

def fetch_npc_sheet(query: str) -> dict | None:
    """Fetch NPC data from NPC Sheet"""
    if not NPC_SHEET_ID:
        return None
    try:
        gc = get_gc()
        sh = gc.open_by_key(NPC_SHEET_ID)
        tab_name = find_sheet_tab(sh, query)
        if not tab_name:
            return None
        ws = sh.worksheet(tab_name)
        rows = ws.get_all_values()
        result = parse_sheet(rows)
        result["sheet_tab"] = tab_name
        result["is_npc"] = True
        return result
    except Exception as e:
        print(f"[NPC Sheets Error] {type(e).__name__}: {e}")
        return None

# ── Sheet parser ───────────────────────────────────────────────────────────

def find_label_same_row(rows, label):
    """Find label, return next non-empty value in same row"""
    for row in rows:
        for i, cell in enumerate(row):
            if cell.strip().lower() == label.lower():
                for j in range(i+1, len(row)):
                    if row[j].strip():
                        return row[j].strip()
    return None

def find_label_next_row(rows, label):
    """Find label, return value from same column in NEXT row"""
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            if cell.strip().lower() == label.lower():
                if r + 1 < len(rows) and c < len(rows[r+1]):
                    val = rows[r+1][c].strip()
                    if val:
                        return val
    return None

def to_int(val: str) -> int:
    try:
        return int(float(val))
    except:
        return 0

def parse_sheet(rows: list) -> dict:
    # ── Identity ──
    # Name: label "Name" same row → next cell value
    name     = find_label_same_row(rows, "Name") or ""
    discord  = find_label_same_row(rows, "Discord ID") or ""
    class_   = find_label_same_row(rows, "Class") or ""
    race     = find_label_same_row(rows, "Race") or ""
    infected = find_label_same_row(rows, "Infected") or ""
    shield   = find_label_same_row(rows, "Shield") or ""

    # ── Core Stats ──
    # STR/DEX/INT/WIS: label in row 12, value in row 13 (below)
    str_ = to_int(find_label_next_row(rows, "STR") or "0")
    dex  = to_int(find_label_next_row(rows, "DEX") or "0")
    int_ = to_int(find_label_next_row(rows, "INT") or "0")
    wis  = to_int(find_label_next_row(rows, "WIS") or "0")
    stats = {"str": str_, "dex": dex, "int": int_, "wis": wis}

    # ── Calculated values (compute from rules, don't read from sheet formulas) ──
    base_hp = CLASS_HP.get(class_, 6)
    if race in ("Forte", "Archosauria"):
        base_hp += 3
    max_hp = base_hp

    base_ap = CLASS_AP.get(class_, 10)
    if infected.lower() == "yes":
        base_ap += 5
    if race in ("Sarkaz:Lich", "Caprinae"):
        base_ap += 10
    max_ap = base_ap

    ac = 16 if class_ == "Defender" else max(10, 10 + dex)
    if shield.lower() == "yes":
        ac += 2
        if race == "Vouivre":
            ac += 1
    if race == "Kuranta":
        ac += 1

    spd = 6
    if race == "Kuranta": spd += 2
    if race == "Zalak":   spd += 3

    # ── Weapons ──
    weapons = []
    in_weapons = False
    for row in rows:
        joined = " ".join(row).lower()
        if "weapons" in joined and "max 3" in joined:
            in_weapons = True
            continue
        if in_weapons:
            if any(kw in joined for kw in ["accessories", "modifier", "attack roll"]):
                break
            # slot is in column A (index 0)
            slot_raw = row[0].strip() if row else ""
            try:
                slot = int(float(slot_raw))
            except:
                continue
            if slot not in (1, 2, 3):
                continue
            # Name in col B (index 1), Damage in col F (index 5), ATK Bonus in col H (index 7)
            wpn_name = row[1].strip() if len(row) > 1 else ""
            dmg      = row[5].strip() if len(row) > 5 else ""
            atk_raw  = row[7].strip() if len(row) > 7 else ""
            if not wpn_name:
                continue
            # แทน stat label ด้วยค่าจริง เช่น 1d5+DEX → 1d5+DEX(0)
            for stat_name, stat_key in [("STR","str"),("DEX","dex"),("INT","int"),("WIS","wis")]:
                if stat_name in dmg:
                    dmg = dmg.replace(stat_name, f"{stat_name}({stats[stat_key]})")
            try: atk_bonus = int(float(atk_raw))
            except: atk_bonus = 0
            weapons.append({
                "slot": slot,
                "name": wpn_name,
                "damage": dmg,
                "atk_bonus": atk_bonus,
                "primary": slot == 1,
            })

    # แทน stat label ทุก slot รวมถึง slot 1
    # (ไม่ override ด้วย CLASS_DAMAGE แล้ว ให้ดึงจากชีทตรงๆ)

    # ── Accessories ──
    accessories = []
    in_acc = False
    for row in rows:
        joined = " ".join(row).lower()
        if "accessories" in joined and "max 3" in joined:
            in_acc = True
            continue
        if in_acc:
            if any(kw in joined for kw in ["modifier", "attack roll"]):
                break
            slot_raw = row[0].strip() if row else ""
            try: slot = int(float(slot_raw))
            except: continue
            if slot not in (1, 2, 3): continue
            acc_name = row[1].strip() if len(row) > 1 else ""
            acc_eff  = row[4].strip() if len(row) > 4 else ""
            if acc_name:
                accessories.append({"slot": slot, "name": acc_name, "effect": acc_eff})

    # ── Modifiers ──
    modifiers = []
    in_mod = False
    for row in rows:
        joined = " ".join(row).lower()
        if "modifier" in joined and "reminder" not in joined and "mod name" not in joined:
            if any(kw in joined for kw in ["modifier", "mods"]):
                in_mod = True
                continue
        if in_mod:
            if "attack roll" in joined:
                break
            mod_name = row[0].strip() if row else ""
            mod_note = row[3].strip() if len(row) > 3 else ""
            skip = ("mod name", "", "modifier", "mods")
            if mod_name and mod_name.lower() not in skip:
                modifiers.append({"name": mod_name, "note": mod_note})

    return {
        "name": name,
        "discord_id": discord,
        "class": class_,
        "race": race,
        "infected": infected,
        "shield": shield,
        "str": str_, "dex": dex, "int": int_, "wis": wis,
        "max_hp": max_hp, "current_hp": max_hp,
        "max_ap": max_ap, "current_ap": max_ap,
        "ac": ac, "spd": spd,
        "weapons": weapons,
        "accessories": accessories,
        "modifiers": modifiers,
        "sheet_tab": "",
    }

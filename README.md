# PROTO PCS Bot v2 — วิธีติดตั้ง

## 1. ติดตั้ง library
```
pip install -r requirements.txt
```

## 2. ตั้งค่า .env
เปิดไฟล์ `.env` แล้วใส่:
- `BOT_TOKEN` — Token จาก Discord Developer Portal
- `SHEET_ID` — ID ของ Google Sheets (ดูจาก URL: docs.google.com/spreadsheets/d/**ID**/edit)

## 3. ตั้งค่า Google Sheets API
1. ไปที่ https://console.cloud.google.com
2. สร้าง Project ใหม่
3. เปิด "Google Sheets API"
4. สร้าง Service Account → Download credentials.json
5. วางไฟล์ credentials.json ไว้ในโฟลเดอร์เดียวกับ bot.py
6. เปิด Google Sheets → Share → ใส่ email ของ Service Account เป็น Viewer

## 4. โครงสร้าง Google Sheets
- แต่ละตัวละครอยู่คนละ Sheet tab
- ชื่อ Sheet tab = ชื่อตัวละคร (ใช้กับ /set-char char:ชื่อนี้)

## 5. รันบอท
```
python bot.py
```
หรือดับเบิลคลิก run_bot.bat (Windows)

## คำสั่งทั้งหมด
| คำสั่ง | รายละเอียด |
|--------|-----------|
| /set-char char:Val | ลงทะเบียนตัวละครจาก Sheets |
| /sync char:Val | อัปเดตข้อมูลจาก Sheets |
| /switch char:Val | เปลี่ยน default |
| /delete-char char:Val | ลบตัวละคร |
| /mystats (char:Val) | ดู stat ทั้งหมด |
| /stat-update (char:Val) | แผง HP/AP |
| /roll stat:wis bonus:+1 modify:adv | ทอย d20 + stat |
| /roll dice:2d6 bonus:+1 | ทอย custom dice |
| /atk-roll weapon:1 modify:adv | ทอย Attack Roll |
| /atk @player | [PRTS] เรียก Attack Roll |
| /player-check | ดู HP/AP ทุกคน |
| /npc-check | [PRTS] ดู NPC ทั้งหมด |
| /initiative | [PRTS] เปิด Initiative Phase |
| /initiative-npc name:xxx bonus:2 | [PRTS] ทอย Initiative ให้ NPC |
| /initiative-order | [PRTS] แสดง order |
| /help | คู่มือ |

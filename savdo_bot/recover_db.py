
import sqlite3
import os

SOURCE = "products_backup.db"
TARGET = "products_recovered2.db"

if not os.path.exists(SOURCE):
    print("Zaxira fayli topilmadi!")
    raise SystemExit

if os.path.exists(TARGET):
    print("products_recovered2.db allaqachon mavjud.")
    print("Uni o'chirmang. Avval nomini menga yuboring.")
    raise SystemExit

src = sqlite3.connect(SOURCE)
dst = sqlite3.connect(TARGET)

try:
    src.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
except sqlite3.DatabaseError as e:
    print("Oddiy o'qish ishlamadi:", e)

try:
    src.backup(dst)
    print("Baza nusxalandi.")
except sqlite3.DatabaseError as e:
    print("Nusxalash muvaffaqiyatsiz:", e)
finally:
    dst.close()
    src.close()

try:
    c = sqlite3.connect(TARGET)
    print("Tekshiruv:", c.execute("PRAGMA integrity_check").fetchall())
    c.close()
except sqlite3.DatabaseError as e:
    print("Yangi bazani tekshirishda xato:", e)

"""Полный отчёт о SQLite БД kz_energy_balance.db
Запуск:
    python db_info.py
    python db_info.py table_name   # детально по одной таблице
"""
import sqlite3, sys
from pathlib import Path

DB = Path(r"F:\zerdeli\Dissertation\02_Energy_Balance_and_Demand_Forecasting_KZ\Article\data\clean\kz_energy_balance.db")

if not DB.exists():
    print(f"❌ БД не найдена: {DB}")
    sys.exit(1)

conn = sqlite3.connect(DB)
cur = conn.cursor()

print("═" * 70)
print(f"  SQLITE DATABASE: {DB.name}")
print("═" * 70)
print(f"  Путь:        {DB}")
print(f"  Размер файла: {DB.stat().st_size / 1024 / 1024:.2f} MB")

# Все таблицы
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]

# Все индексы
cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
indexes = [r[0] for r in cur.fetchall()]

print(f"  Таблиц:      {len(tables)}")
print(f"  Индексов:    {len(indexes)}")

# Если запрошена конкретная таблица
if len(sys.argv) > 1:
    t = sys.argv[1]
    if t not in tables:
        print(f"\n❌ Таблица '{t}' не найдена. Доступные: {tables}")
        sys.exit(1)
    print()
    print("═" * 70)
    print(f"  ДЕТАЛЬНАЯ ИНФОРМАЦИЯ: {t}")
    print("═" * 70)
    cur.execute(f"PRAGMA table_info('{t}')")
    cols = cur.fetchall()
    print(f"  Колонок: {len(cols)}")
    for c in cols:
        nullable = "NULL OK" if not c[3] else "NOT NULL"
        pk = " ← PRIMARY KEY" if c[5] else ""
        print(f"    [{c[0]:>2}] {c[1]:<35s} {c[2]:<15s} {nullable}{pk}")
    n = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    print(f"\n  Строк: {n:,}")
    print(f"\n  Первые 5 строк:")
    cur.execute(f'SELECT * FROM "{t}" LIMIT 5')
    for r in cur.fetchall():
        print(f"    {r}")
    sys.exit(0)

# Общая таблица: все таблицы с количеством строк и колонок
print()
print(f"  {'TABLE':<35s}{'ROWS':>10s}{'COLS':>6s}")
print("  " + "─" * 53)
total = 0
for t in tables:
    n = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    cols = cur.execute(f"PRAGMA table_info('{t}')").fetchall()
    print(f"  {t:<35s}{n:>10,d}{len(cols):>6d}")
    total += n
print("  " + "─" * 53)
print(f"  {'TOTAL ROWS':<35s}{total:>10,d}")

# meta таблица — сборка
if "meta" in tables:
    print()
    print("─" * 70)
    print("  META (информация о сборке БД):")
    cur.execute("SELECT * FROM meta")
    for r in cur.fetchall():
        print(f"    {r[0]}: {r[1]}")

print()
print("─" * 70)
print("  ПРИМЕРЫ ЗАПРОСОВ:")
print()
print(f"  python db_info.py kegoc_balance        # детали о таблице")
print(f"  python db_info.py ml_metrics_renewable_cf")
print(f"  python db_info.py korem_hourly")

conn.close()

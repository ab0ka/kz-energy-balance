"""
KOREM Hourly Trading Data Parser
================================
KOREM (Kazakhstan Operator of Electric Energy and Capacity Market) publishes
monthly clearing-price / balancing-market XLSX files at
    https://www.korem.kz/uploads/{N}.{RusMonth}_{year}_g._{zone}.xlsx
e.g.  6._Iyun_2024_g._Severnaya_zona.xlsx

Verified real schema (column 0..7):
    0: Дата                                     (DD.MM.YYYY)
    1: Час                                      ("0-1" .. "23-0", → int 0..23)
    2: Контрольный час, (Да/Нет)
    3: Направление часа                         (На повышение / На понижение)
    4: Доходы субъектов ОРЭ (повышение)
    5: Доходы субъектов ОРЭ (понижение)
    6: Сумма, оплачиваемая РЦ БРЭ — поставщику   (KZT)
    7: Сумма, оплачиваемая системному оператору  (KZT)

Outputs:
    data/clean/korem_hourly.csv
    table `korem_hourly` in kz_energy_balance.db

Usage:
    python3 scripts/korem_xlsx_parser.py data/raw/korem/
    python3 scripts/korem_xlsx_parser.py --file data/raw/korem/foo.xlsx
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw" / "korem"
CLEAN_DIR = BASE_DIR / "data" / "clean"
DB_PATH = CLEAN_DIR / "kz_energy_balance.db"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

RUS_MONTH = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11,
    "декабрь": 12,
    # accusative / preposition variants observed in KOREM filenames
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
    # transliterated variants
    "yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5, "iyun": 6,
    "iyul": 7, "avgust": 8, "sentyabr": 9, "oktyabr": 10, "noyabr": 11,
    "dekabr": 12,
}

ZONE_MAP = {
    "север": "North", "north": "North", "сев": "North",
    "юг": "South",   "south": "South", "юж": "South",
    "запад": "West", "west":  "West",  "зап": "West",
    "с-ю": "North-South", "север-юг": "North-South", "yug": "South",
}


def _fix_mojibake(name: str) -> str:
    """Best-effort recovery of Cyrillic filenames mangled by cp866<->utf-8 or
    cp1251<->utf-8 round-trips on Windows file extraction.

    KOREM filenames downloaded as a ZIP and unpacked with the wrong codepage
    end up on disk as "╨п╨╜╨▓╨░╤А╤М" instead of "Январь". We try the two
    common mojibake reversals and keep whichever yields more Cyrillic letters.
    """
    candidates = [name]
    for src, dst in (("cp866", "utf-8"), ("cp1252", "utf-8"), ("latin-1", "utf-8")):
        try:
            candidates.append(name.encode(src).decode(dst))
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    def cyr_score(s: str) -> int:
        return sum(1 for ch in s if "Ѐ" <= ch <= "ӿ")

    best = max(candidates, key=cyr_score)
    return unicodedata.normalize("NFC", best)


def _file_meta(name: str) -> tuple[Optional[int], Optional[int], Optional[str]]:
    fixed = _fix_mojibake(name)
    n = fixed.lower()
    year_m = re.search(r"20\d\d", n)
    year = int(year_m.group()) if year_m else None
    month = None
    for k, v in RUS_MONTH.items():
        if k in n:
            month = v
            break
    zone = None
    for k, v in ZONE_MAP.items():
        if k in n:
            zone = v
            break
    return year, month, zone


_HOUR_RE = re.compile(r"^\s*(\d{1,2})\s*-\s*\d{1,2}")


def _to_hour(v) -> Optional[int]:
    if pd.isna(v):
        return None
    s = str(v).strip()
    m = _HOUR_RE.match(s)
    if m:
        h = int(m.group(1))
        return h if 0 <= h <= 23 else None
    try:
        h = int(float(s))
        return h if 0 <= h <= 23 else None
    except ValueError:
        return None


def _detect_zone_sheet(xlsx: pd.ExcelFile) -> str:
    """Pick the most relevant sheet (the per-zone one)."""
    for sn in xlsx.sheet_names:
        snl = sn.lower()
        if any(k in snl for k in ["зон", "север", "юг", "запад", "zone", "north", "south", "west"]):
            return sn
    return xlsx.sheet_names[0]


def parse_one_file(path: Path) -> pd.DataFrame:
    year, month, zone = _file_meta(path.name)
    log.info(f"  {path.name}: year={year} month={month} zone={zone}")
    try:
        xls = pd.ExcelFile(path)
        sheet = _detect_zone_sheet(xls)
        df = pd.read_excel(path, sheet_name=sheet, header=0)
    except Exception as e:
        log.warning(f"    cannot read {path.name}: {e}")
        return pd.DataFrame()

    # Map columns (defensive, name-based)
    cols = {str(c).lower(): c for c in df.columns}
    def find(keys):
        for k in keys:
            for cl, orig in cols.items():
                if k in cl:
                    return orig
        return None

    c_date = find(["дата", "date"])
    c_hour = find(["час", "hour"])
    c_dir  = find(["направлен", "direction"])
    c_pos  = find(["доход", "повышен"])  # earnings up
    c_neg  = find(["понижен"])
    c_pay_supp = find(["рц брэ", "поставщик"])
    c_pay_so   = find(["системному", "оператору"])

    if c_date is None or c_hour is None:
        log.warning(f"    {path.name}: required columns not found in {sheet!r}")
        return pd.DataFrame()

    out = pd.DataFrame()
    # KOREM ships two date encodings across files: ISO 'YYYY-MM-DD' and
    # 'DD.MM.YYYY'. Forcing dayfirst=True silently mangles ISO dates (12 days
    # parsed swapped, the rest dropped to NaT). Pick whichever yields fewer NaT.
    iso_try = pd.to_datetime(df[c_date], errors="coerce", dayfirst=False)
    dmy_try = pd.to_datetime(df[c_date], errors="coerce", dayfirst=True)
    out["date"] = iso_try if iso_try.isna().sum() <= dmy_try.isna().sum() else dmy_try
    out["hour"] = df[c_hour].apply(_to_hour)
    if c_dir is not None:
        d = df[c_dir].astype(str).str.strip().str.lower()
        out["direction"] = d.map({
            "на повышение": "up",
            "на понижение": "down",
            "без регулирования": "none",
        }).fillna(d)
    out["earnings_up_kzt"]   = pd.to_numeric(df[c_pos], errors="coerce") if c_pos else np.nan
    out["earnings_down_kzt"] = pd.to_numeric(df[c_neg], errors="coerce") if c_neg else np.nan
    out["payment_to_supplier_kzt"]    = pd.to_numeric(df[c_pay_supp], errors="coerce") if c_pay_supp else np.nan
    out["payment_to_system_op_kzt"]   = pd.to_numeric(df[c_pay_so], errors="coerce") if c_pay_so else np.nan

    out["zone"] = zone
    out["year"] = year
    out["month"] = month
    out = out.dropna(subset=["date", "hour"]).reset_index(drop=True)
    out["hour"] = out["hour"].astype(int)
    return out


def parse_folder(folder: Path) -> pd.DataFrame:
    folder = Path(folder)
    pieces = []
    for f in sorted(folder.glob("*.xls*")):
        df = parse_one_file(f)
        if not df.empty:
            pieces.append(df)
    if not pieces:
        return pd.DataFrame()
    return pd.concat(pieces, ignore_index=True)


def write_to_sqlite(df: pd.DataFrame):
    if df.empty:
        return
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    out = df.copy()
    out["date"] = out["date"].astype(str)
    out.to_sql("korem_hourly", conn, if_exists="replace", index=False)
    # convenience: monthly aggregates
    monthly = (out.groupby(["year", "month", "zone"], dropna=False)
                  .agg(rows=("hour", "size"),
                       total_earnings_up=("earnings_up_kzt", "sum"),
                       total_earnings_down=("earnings_down_kzt", "sum"),
                       total_payment_to_supplier=("payment_to_supplier_kzt", "sum"),
                       total_payment_to_system_op=("payment_to_system_op_kzt", "sum"))
                  .reset_index())
    monthly.to_sql("korem_monthly", conn, if_exists="replace", index=False)
    conn.close()
    log.info(f"  wrote korem_hourly + korem_monthly → {DB_PATH}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("folder", nargs="?", help="Folder with KOREM XLSX files")
    p.add_argument("--file", help="Process a single XLSX")
    args = p.parse_args(argv)

    if args.file:
        df = parse_one_file(Path(args.file))
    elif args.folder:
        df = parse_folder(Path(args.folder))
    else:
        p.print_help()
        return 1

    if df.empty:
        log.warning("nothing extracted")
        return 2

    out_csv = CLEAN_DIR / "korem_hourly.csv"
    df.to_csv(out_csv, index=False, encoding="utf-8")
    log.info(f"wrote {out_csv} ({len(df):,} rows)")
    write_to_sqlite(df)
    print()
    print(f"Sample (first 6 rows):")
    print(df.head(6).to_string(index=False))
    print()
    print("Coverage: year × month × zone")
    print(df.groupby(["year", "month", "zone"]).size().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())

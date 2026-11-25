"""Validate the GREEN workbook and import the eligible barcode catalog into Supabase.

Run without --apply to see the quality report. Run from the bot root with
SUPABASE_ADMIN_DB_URL set to a PostgreSQL schema-owner connection for catalog maintenance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from math import isfinite
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from app.adapters.off import valid_barcode

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REVIEW_SECTIONS = {"green kitchen", "вода, соки, напитки"}
EXPECTED = {"A": "Раздел", "C": "Продукт (как в GREEN)", "G": "Штрихкод (EAN)",
            "H": "Артикул", "K": "Белки, г/100 г", "L": "Жиры, г/100 г",
            "M": "Углеводы, г/100 г", "N": "Ккал/100 г"}


def workbook_rows(path: Path):
    with ZipFile(path) as z:
        sheets = ET.fromstring(z.read("xl/workbook.xml")).findall(f".//{NS}sheet")
        if not sheets or sheets[0].get("name") != "БЖУ":
            raise ValueError("Первым листом должен быть «БЖУ»")
        strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            strings = ["".join(node.itertext()) for node in
                       ET.fromstring(z.read("xl/sharedStrings.xml")).iter(NS + "si")]
        with z.open("xl/worksheets/sheet1.xml") as source:
            for _, row in ET.iterparse(source, events=("end",)):
                if row.tag != NS + "row":
                    continue
                values = {}
                for cell in row:
                    name = cell.attrib["r"].rstrip("0123456789")
                    value = cell.findtext(NS + "v", default="")
                    if cell.get("t") == "s":
                        value = strings[int(value)]
                    elif cell.get("t") == "inlineStr":
                        value = "".join(cell.find(NS + "is").itertext())
                    values[name] = value.strip()
                row.clear()
                yield values


def catalog_gtin(code: str) -> str | None:
    if not valid_barcode(code):
        return None
    # Restricted-circulation codes can describe different products in different
    # stores. The GTIN-14 indicator 9 denotes variable measure.
    normalized = code.zfill(14)
    ean = normalized[-13:]
    if len(code) == 14 and code[0] == "9" or ean.startswith(("02", "2")):
        return None
    return normalized


def product_rows(path: Path):
    rows = workbook_rows(path)
    header = next(rows, {})
    if any(header.get(k) != v for k, v in EXPECTED.items()):
        raise ValueError("Колонки листа «БЖУ» не совпадают с ожидаемым форматом")
    report: Counter = Counter()
    candidates = []
    skus = set()
    for row in rows:
        report["Строк в основном листе"] += 1
        sku, name = row.get("H"), row.get("C")
        if not sku or not name:
            report["Пустой артикул или название"] += 1
            continue
        if sku in skus:
            raise ValueError(f"Повтор артикула {sku}")
        skus.add(sku)
        if row.get("A", "").casefold() in REVIEW_SECTIONS:
            report["Напитки и готовые блюда — сверить единицы"] += 1
            continue
        if row.get("Q"):
            report["Примечание в источнике — ручная проверка"] += 1
            continue
        try:
            kcal, protein, fat, carbs = (float(row[k].replace(",", ".")) for k in ("N", "K", "L", "M"))
        except (KeyError, ValueError):
            report["Неполное КБЖУ"] += 1
            continue
        energy = 4 * protein + 9 * fat + 4 * carbs
        if not (all(isfinite(n) and n >= 0 for n in (kcal, protein, fat, carbs))
                and kcal <= 900 and protein + fat + carbs <= 100
                and abs(kcal - energy) <= max(20, 0.2 * kcal)):
            report["КБЖУ требует сверки"] += 1
            continue
        codes = {gtin for code in row.get("G", "").split(";")
                 if (gtin := catalog_gtin(code.strip()))}
        if not codes:
            report["Без валидного универсального штрихкода"] += 1
            continue
        candidates.append((sku, name, row.get("D") or None, kcal, protein, fat, carbs, codes))

    occurrences = Counter(code for *_, codes in candidates for code in codes)
    products = []
    barcodes = []
    for *fields, codes in candidates:
        safe = sorted(code for code in codes if occurrences[code] == 1)
        if not safe:
            report["Остались только неоднозначные штрихкоды"] += 1
            continue
        products.append(fields)
        barcodes.extend((fields[0], code) for code in safe)
    report["Неоднозначных кодов исключено"] = sum(n > 1 for n in occurrences.values())
    report["Товаров к импорту"] = len(products)
    report["Штрихкодов к импорту"] = len(barcodes)
    return products, barcodes, report


def load(url: str, products, barcodes):
    import psycopg
    from app.postgres import connection_options

    with psycopg.connect(url, **connection_options(url)) as conn, conn.cursor() as cur:
        cur.execute(Path(__file__).resolve().parents[1].joinpath("supabase/green_source.sql").read_text())
        cur.execute("""CREATE TEMP TABLE green_foods (
             sku text PRIMARY KEY, name text, brand text, kcal float8, protein float8, fat float8, carbs float8
        ) ON COMMIT DROP""")
        cur.execute("CREATE TEMP TABLE green_barcodes (sku text, gtin text PRIMARY KEY) ON COMMIT DROP")
        for table, data in (("green_foods", products), ("green_barcodes", barcodes)):
            with cur.copy(f"COPY {table} FROM STDIN") as copy:
                for row in data:
                    copy.write_row(row)
        cur.execute("""INSERT INTO bot.foods
            (name_ru,name_en,brand,kcal_100g,protein_100g,fat_100g,carbs_100g,source,external_id)
            SELECT name,name,brand,kcal,protein,fat,carbs,'green','green:' || sku FROM green_foods
            ON CONFLICT (source,external_id) DO UPDATE SET
              name_ru=EXCLUDED.name_ru, name_en=EXCLUDED.name_en, brand=EXCLUDED.brand,
              kcal_100g=EXCLUDED.kcal_100g, protein_100g=EXCLUDED.protein_100g,
              fat_100g=EXCLUDED.fat_100g, carbs_100g=EXCLUDED.carbs_100g, deleted_at=NULL""")
        cur.execute("""DELETE FROM bot.food_barcodes b USING bot.foods f
            WHERE b.food_id=f.id AND f.source='green' AND NOT EXISTS
              (SELECT 1 FROM green_barcodes g WHERE f.external_id='green:'||g.sku AND b.gtin=g.gtin)""")
        cur.execute("""UPDATE bot.foods f SET deleted_at=CURRENT_TIMESTAMP
            WHERE f.source='green' AND f.deleted_at IS NULL AND NOT EXISTS
              (SELECT 1 FROM green_foods g WHERE f.external_id='green:'||g.sku)""")
        cur.execute("""INSERT INTO bot.food_barcodes(food_id,gtin)
            SELECT f.id,b.gtin FROM green_barcodes b JOIN bot.foods f
              ON f.source='green' AND f.external_id='green:'||b.sku
            ON CONFLICT DO NOTHING""")
        count = cur.execute("""SELECT count(*) FROM bot.foods f JOIN bot.food_barcodes b
                              ON b.food_id=f.id WHERE f.source='green' AND f.deleted_at IS NULL""").fetchone()[0]
        if count != len(barcodes):
            raise RuntimeError("Количество загруженных штрихкодов не совпало с отчётом")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    products, barcodes, report = product_rows(args.workbook)
    for label, count in report.items():
        print(f"{label}: {count}")
    if args.apply:
        from app.config import get_config

        cfg = get_config()
        url = cfg.supabase_admin_db_url or cfg.supabase_db_url
        if not url or not products:
            parser.error("Нужны SUPABASE_ADMIN_DB_URL и непустой каталог")
        load(url.get_secret_value(), products, barcodes)
        print("Каталог загружен и проверен")


if __name__ == "__main__":
    main()

"""Import real product snapshots from Plaza Vea's public online catalog.

Online availability belongs to the e-commerce source, not a specific branch.
These records intentionally stay outside the operational products/sales tables.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import get_engine, initialize_database
from models import products as operational_products, web_catalog_products

BASE = "https://www.plazavea.com.pe"
ROOT_CATEGORIES = {
    "abarrotes", "bebidas", "cuidado personal y salud", "limpieza", "mascotas",
    "frutas y verduras", "congelados", "quesos y fiambres", "panadería y pastelería",
    "carnes, aves y pescados", "lácteos y huevos", "desayunos",
    "pollo rostizado y comidas preparadas", "mercado saludable",
}
PAGE_SIZE = 50


def get_json(url: str):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible; PlazaVeaCatalogImport/1.0)"})
    for attempt in range(4):
        try:
            with urlopen(request, timeout=35) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))
    return []


def supermarket_roots(tree):
    return [(root["name"].strip(), root["url"]) for root in tree
            if root.get("name", "").strip().casefold() in ROOT_CATEGORIES and root.get("url")]


def plaza_vea_seller(product):
    for item in product.get("items") or []:
        for seller in item.get("sellers") or []:
            offer = seller.get("commertialOffer") or {}
            if seller.get("sellerName", "").strip().casefold() in {"plaza vea", "plazavea"} and offer.get("Price") is not None:
                return item, seller, offer
    return None


def records_for_category(root_name, category_url, offset):
    path = category_url.removeprefix(BASE)
    query = urlencode({"_from": offset, "_to": offset + PAGE_SIZE - 1})
    try:
        results = get_json(f"{BASE}/api/catalog_system/pub/products/search{path}?{query}")
    except Exception as exc:
        print(f"No se pudo leer {category_url}: {exc}")
        return []
    if not isinstance(results, list):
        return []

    captured = datetime.now().astimezone().isoformat(timespec="seconds")
    records = []
    for product in results:
        chosen = plaza_vea_seller(product)
        if not chosen:
            continue
        item, seller, offer = chosen
        item_id = str(item.get("itemId") or "").strip()
        product_id = str(product.get("productId") or "").strip()
        name = str(product.get("productName") or item.get("nameComplete") or item.get("name") or "").strip()
        price = offer.get("Price")
        if not item_id or not product_id or not name or price is None or float(price) <= 0:
            continue
        cats = [part.strip() for part in (product.get("categories") or [])[:1]]
        category = " > ".join(part.strip("/").replace("/", " > ") for part in cats) or root_name
        refs = item.get("referenceId") or []
        ref_code = next((str(ref.get("Value")) for ref in refs if ref.get("Key") == "RefId"), None)
        available = offer.get("AvailableQuantity")
        records.append({
            "item_id": item_id,
            "product_id": product_id,
            "reference_code": product.get("productReferenceCode") or ref_code,
            "ean": item.get("ean") or None,
            "name": name[:300],
            "brand": str(product.get("brand") or "").strip()[:160] or None,
            "category": category[:300],
            "unit": item.get("measurementUnit"),
            "price_pen": round(float(price), 2),
            "list_price_pen": round(float(offer["ListPrice"]), 2) if offer.get("ListPrice") is not None else None,
            "online_available_quantity": int(available) if available is not None else None,
            "online_available": int(bool(offer.get("IsAvailable"))) if offer.get("IsAvailable") is not None else None,
            "product_url": product.get("link") or f"{BASE}/{product.get('linkText', '')}/p",
            "source_category_url": category_url,
            "source": "plazavea.com.pe",
            "captured_at": captured,
        })
    return records


def collect(limit):
    tree = get_json(f"{BASE}/api/catalog_system/pub/category/tree/3")
    roots = supermarket_roots(tree)
    if not roots:
        raise RuntimeError("La web no devolvió categorías de supermercado para importar.")

    found = {}
    page = 0
    with ThreadPoolExecutor(max_workers=5) as executor:
        while len(found) < limit:
            added_this_round = False
            jobs = [executor.submit(records_for_category, name, url, page * PAGE_SIZE) for name, url in roots]
            for job in as_completed(jobs):
                rows = job.result()
                if rows:
                    added_this_round = True
                    for row in rows:
                        found.setdefault(row["item_id"], row)
                if len(found) >= limit:
                    break
            print(f"Categorías consultadas: página {page + 1}; productos únicos: {min(len(found), limit)}", flush=True)
            if not added_this_round:
                break
            page += 1
    return list(found.values())[:limit]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--sync-existing", action="store_true", help="Integrate the saved Plaza Vea snapshot without downloading the site again")
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 5000:
        parser.error("--limit debe estar entre 1 y 5000")

    rows = [] if args.sync_existing else collect(args.limit)
    if not rows and not args.sync_existing:
        raise RuntimeError("No se obtuvo ningún producto elegible del catálogo público.")

    initialize_database()
    with get_engine().begin() as connection:
        if rows:
            statement = pg_insert(web_catalog_products).values(rows)
            update_fields = {column.name: getattr(statement.excluded, column.name)
                             for column in web_catalog_products.columns if column.name != "item_id"}
            statement = statement.on_conflict_do_update(index_elements=[web_catalog_products.c.item_id], set_=update_fields)
            connection.execute(statement)
        source_rows = connection.execute(web_catalog_products.select()).mappings().all()
        store_rows = [{
            "code": f"PVW-{row['item_id']}",
            "name": row["name"][:180],
            "category": (row["category"].split(" > ", 1)[0] or "Otros")[:80],
            "price": row["price_pen"],
            "stock": 0,
            "unit_of_measure": row["unit"],
            "minimum_stock": None,
            "safety_stock": None,
            "maximum_stock": None,
            "supplier": None,
            "lead_time_days": None,
            "inventory_registered": 0,
            "source_item_id": row["item_id"],
            "source_url": row["product_url"],
            "source_captured_at": row["captured_at"],
        } for row in source_rows]
        store_statement = pg_insert(operational_products).values(store_rows)
        store_update = {field: getattr(store_statement.excluded, field)
                        for field in ("name", "category", "price", "unit_of_measure", "source_url", "source_captured_at")}
        store_statement = store_statement.on_conflict_do_update(
            index_elements=[operational_products.c.source_item_id], set_=store_update
        )
        connection.execute(store_statement)
        store_total = connection.execute(text("SELECT COUNT(*) FROM products")).scalar_one()
        total = connection.execute(text("SELECT COUNT(*) FROM web_catalog_products")).scalar_one()
    if rows:
        print(f"Importados/actualizados: {len(rows)} productos del catálogo público de Plaza Vea.")
    else:
        print("Usada la captura pública ya guardada; no se volvió a descargar la web.")
    print(f"Registros únicos en web_catalog_products: {total}.")
    print(f"Productos visibles en el módulo Productos: {store_total} en total, incluidos los artículos de Plaza Vea.")
    print("Origen: plazavea.com.pe; precios en PEN. La disponibilidad es de e-commerce y no confirma stock de Chiclayo.")
    print("No se crearon ventas ni historial de demanda.")


if __name__ == "__main__":
    main()

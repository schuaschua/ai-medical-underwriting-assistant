#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Azure list prices from Microsoft's public Retail Prices API (https://prices.azure.com, no sign-in).

Looks up pay-as-you-go retail prices for one service in one region and prints them as JSON, with
the query, the date fetched and each meter's id, so every price in an estimate can say where it
came from. Retail list prices only: no enterprise agreement, reservation or savings-plan discounts
unless --price-type Reservation is asked for.

Usage:
    uv run azure_prices.py --service "Virtual Machines" --region uaenorth --sku Standard_D4s_v5
    uv run azure_prices.py --service "Storage" --region westeurope --product-contains "Blob" --meter-contains "Hot LRS Data Stored"
    uv run azure_prices.py --filter "serviceName eq 'Azure Cosmos DB' and armRegionName eq 'uaenorth'"

Options:
    --service NAME           serviceName, exactly as Azure spells it ("Virtual Machines", "SQL Database", ...)
    --region NAME            armRegionName ("uaenorth", "westeurope", ...)
    --sku NAME               armSkuName ("Standard_D4s_v5"); many services leave it blank, use --meter-contains
    --currency CODE          currencyCode (default USD); the API supports a fixed list of currencies
    --price-type TYPE        Consumption (default), Reservation or DevTestConsumption
    --filter ODATA           raw OData filter, replaces the one built from the options above
    --product-contains TEXT  keep items whose productName contains TEXT (case-insensitive; repeatable)
    --meter-contains TEXT    keep items whose meterName contains TEXT (case-insensitive; repeatable)
    --include-spot           keep Spot and Low Priority meters (dropped by default)
    --include-windows        keep Windows VM meters (dropped by default: estimates assume Linux unless told)
    --max-pages N            pages to follow (default 10; 100 items a page)

Prints JSON: {"status", "query", "fetched", "currency", "count", "items": [{productName, skuName,
meterName, unitOfMeasure, retailPrice, tierMinimumUnits, reservationTerm, effectiveStartDate,
meterId, armRegionName, source}]}. Each item's "source" is the line to put in an estimate's
price_source. Exit codes: 0 ok (possibly zero items), 2 error (network, bad filter, bad options).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

API = "https://prices.azure.com/api/retail/prices"
KEEP = ("productName", "skuName", "meterName", "unitOfMeasure", "retailPrice", "tierMinimumUnits",
        "reservationTerm", "effectiveStartDate", "meterId", "armRegionName")


def quote(value: str) -> str:
    """An OData string literal: single quotes doubled."""
    return "'" + value.replace("'", "''") + "'"


def build_filter(args: argparse.Namespace) -> str:
    if args.filter:
        return args.filter
    parts = []
    if args.service:
        parts.append(f"serviceName eq {quote(args.service)}")
    if args.region:
        parts.append(f"armRegionName eq {quote(args.region)}")
    if args.sku:
        parts.append(f"armSkuName eq {quote(args.sku)}")
    if args.price_type:
        parts.append(f"priceType eq {quote(args.price_type)}")
    return " and ".join(parts)


def build_url(odata_filter: str, currency: str) -> str:
    query = {"currencyCode": quote(currency)}
    if odata_filter:
        query["$filter"] = odata_filter
    return API + "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)


def fetch(url: str, max_pages: int, opener=urllib.request.urlopen) -> list[dict]:
    items: list[dict] = []
    for _ in range(max_pages):
        with opener(url, timeout=30) as response:
            page = json.loads(response.read().decode("utf-8"))
        items.extend(page.get("Items", []))
        url = page.get("NextPageLink")
        if not url:
            break
    return items


def keep(item: dict, args: argparse.Namespace) -> bool:
    product, meter, sku = item.get("productName", ""), item.get("meterName", ""), item.get("skuName", "")
    if not args.include_spot and any(w in f"{meter} {sku}" for w in ("Spot", "Low Priority")):
        return False
    if not args.include_windows and item.get("serviceName") == "Virtual Machines" and "Windows" in product:
        return False
    if any(t.lower() not in product.lower() for t in args.product_contains):
        return False
    if any(t.lower() not in meter.lower() for t in args.meter_contains):
        return False
    return True


def shape(item: dict, fetched: str, currency: str) -> dict:
    out = {k: item.get(k) for k in KEEP if item.get(k) not in (None, "")}
    out["source"] = (f"Azure Retail Prices API, {item.get('productName')} / {item.get('meterName')}, "
                     f"{item.get('armRegionName')}, {currency} per {item.get('unitOfMeasure')}, "
                     f"meterId {item.get('meterId')}, fetched {fetched}")
    return out


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--service")
    p.add_argument("--region")
    p.add_argument("--sku")
    p.add_argument("--currency", default="USD")
    p.add_argument("--price-type", default="Consumption")
    p.add_argument("--filter")
    p.add_argument("--product-contains", action="append", default=[])
    p.add_argument("--meter-contains", action="append", default=[])
    p.add_argument("--include-spot", action="store_true")
    p.add_argument("--include-windows", action="store_true")
    p.add_argument("--max-pages", type=int, default=10)
    return p.parse_args(argv)


def main(argv: list[str] | None = None, opener=urllib.request.urlopen) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    odata = build_filter(args)
    if not odata:
        print(json.dumps({"status": "error", "message": "give --service/--region/--sku or --filter"}))
        return 2
    url = build_url(odata, args.currency)
    fetched = date.today().isoformat()
    try:
        raw = fetch(url, args.max_pages, opener)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300] if e.fp else ""
        print(json.dumps({"status": "error", "message": f"HTTP {e.code}: {detail}", "query": odata}))
        return 2
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(json.dumps({"status": "error", "message": f"price API unreachable: {e}", "query": odata}))
        return 2
    other = sorted({i.get("currencyCode") for i in raw} - {args.currency, None})
    if other:
        print(json.dumps({"status": "error", "query": odata,
                          "message": f"asked for {args.currency}, the API answered in {', '.join(other)}"}))
        return 2
    items = [shape(i, fetched, args.currency) for i in raw if keep(i, args)]
    items.sort(key=lambda i: (i.get("productName", ""), i.get("meterName", ""), i.get("tierMinimumUnits", 0)))
    print(json.dumps({"status": "ok", "query": odata, "fetched": fetched, "currency": args.currency,
                      "count": len(items), "items": items}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Tests for azure_prices.py with a fake API (no network)."""

import importlib.util
import io
import json
import sys
import urllib.parse
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "azure_prices.py"
spec = importlib.util.spec_from_file_location("azure_prices", SCRIPT)
azure_prices = importlib.util.module_from_spec(spec)
sys.modules["azure_prices"] = azure_prices
spec.loader.exec_module(azure_prices)


def item(meter, product="Virtual Machines Dsv5 Series", price=0.235, currency="USD"):
    return {"currencyCode": currency, "retailPrice": price, "unitOfMeasure": "1 Hour", "meterName": meter,
            "productName": product, "skuName": "Standard_D4s_v5", "serviceName": "Virtual Machines",
            "armRegionName": "uaenorth", "meterId": f"id-{meter}", "tierMinimumUnits": 0.0}


class FakeApi:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def __call__(self, url, timeout):
        self.urls.append(url)
        return io.BytesIO(json.dumps(self.pages[len(self.urls) - 1]).encode())


def run(capsys, api, *args):
    code = azure_prices.main(list(args), opener=api)
    return code, json.loads(capsys.readouterr().out)


def test_query_filters_and_pages(capsys):
    api = FakeApi([
        {"Items": [item("D4s v5"), item("D4s v5 Spot")], "NextPageLink": "https://next"},
        {"Items": [item("D4s v5", product="Virtual Machines Dsv5 Series Windows", price=0.419)]},
    ])
    code, out = run(capsys, api, "--service", "Virtual Machines", "--region", "uaenorth", "--sku", "Standard_D4s_v5")
    assert code == 0
    query = urllib.parse.unquote(api.urls[0])
    assert "serviceName eq 'Virtual Machines' and armRegionName eq 'uaenorth'" in query
    assert "currencyCode='USD'" in query and api.urls[1] == "https://next"
    assert [i["meterName"] for i in out["items"]] == ["D4s v5"]  # Spot and Windows dropped
    assert "meterId id-D4s v5" in out["items"][0]["source"]


def test_quotes_are_escaped():
    assert azure_prices.quote("O'Brien") == "'O''Brien'"


def test_wrong_currency_is_an_error(capsys):
    code, out = run(capsys, FakeApi([{"Items": [item("D4s v5", currency="USD")]}]),
                    "--service", "Virtual Machines", "--currency", "AED")
    assert code == 2 and "answered in USD" in out["message"]


def test_needs_a_query(capsys):
    code, out = run(capsys, FakeApi([]), "--price-type", "")
    assert code == 2

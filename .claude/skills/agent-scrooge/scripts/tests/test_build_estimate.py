"""Tests for build_estimate.py against a throwaway project (synthetic spine, rate card and model).

Run: uv run --with pytest --with openpyxl pytest -q skills/agent-scrooge/scripts/tests
Add --with formulas to also recalculate the workbook's formulas and compare them with the script's figures.
"""

import copy
import datetime
import csv
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "build_estimate.py"
spec = importlib.util.spec_from_file_location("build_estimate", SCRIPT)
build_estimate = importlib.util.module_from_spec(spec)
sys.modules["build_estimate"] = build_estimate
spec.loader.exec_module(build_estimate)

openpyxl = pytest.importorskip("openpyxl")

SPINE = "_bmad-output/planning-artifacts/architecture/demo/ARCHITECTURE-SPINE.md"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "_bmad" / "memory" / "agent-scrooge").mkdir(parents=True)
    (tmp_path / "_bmad" / "config.toml").write_text(
        '[modules.org]\ncost_estimates_folder = "{project-root}/docs/costing"\n')
    spine = tmp_path / SPINE
    spine.parent.mkdir(parents=True)
    spine.write_text("# Spine\n## AD-1 Hosting\nAKS, environments Dev and Prod.\n")
    epics = tmp_path / "_bmad-output" / "planning-artifacts" / "epics.md"
    epics.write_text("# Epics\n## Epic 1: Login\n- 1.1 Login\n## Epic 2: Reports\n- 2.1 Reports\n")
    (tmp_path / "_bmad" / "memory" / "agent-scrooge" / "rate-card.json").write_text(json.dumps(
        {"currency": "USD", "updated": "2026-09-28", "source": "test",
         "roles": [{"role": "Developer", "day_rate": 500}, {"role": "Tech Lead", "day_rate": 800}]}))
    return tmp_path


def model(**over) -> dict:
    m = {
        "name": "demo", "currency": "USD", "pricing_region": "uaenorth", "price_date": "2026-09-28",
        "assumptions": {"delivery_days": 63, "horizon_years": 3, "contingency_pct": 10},
        "environments": [
            {"name": "Dev", "run_months_per_year": 6, "items": [
                {"component": "AKS nodes", "service": "Virtual Machines", "sku": "Standard_D4s_v5",
                 "unit": "1 Hour", "unit_price": 0.235, "quantity": 2, "cite": f"{SPINE} AD-1", "price_source": "API"}]},
            {"name": "Prod", "delivery_months": 1, "items": [
                {"component": "AKS nodes", "service": "Virtual Machines", "sku": "Standard_D4s_v5",
                 "unit": "1 Hour", "unit_price": 0.235, "quantity": 3, "cite": f"{SPINE} AD-1", "price_source": "API"},
                {"component": "Storage", "service": "Storage", "unit": "1 GB/Month", "unit_price": 0.02,
                 "quantity": 500, "usage": 1, "cite": "assumption: 500 GB of documents", "price_source": "API"}]}],
        "team": [{"role": "Developer", "employees": 3, "cite": f"{SPINE} AD-1"},
                 {"role": "Tech Lead", "employees": 1, "allocation": 0.5, "cite": "owner: Tech Lead, 2026-09-28"}],
        "other_costs": [{"item": "Pen test", "cost": 5000, "cite": "assumption: typical"}],
        "support": [{"role": "Developer", "mandays_per_year": 20, "cite": "owner: Delivery Manager, 2026-09-28"}],
        "run_costs": [{"item": "Support contract", "annual_cost": 3000, "cite": "owner: Delivery Manager, 2026-09-28"}],
        "revenue": [{"stream": "Subscriptions", "amounts": [50000, 80000, 100000], "cite": "owner: Product Lead, 2026-09-28"}],
        "gaps": ["DR region not stated"],
    }
    m.update(over)
    return m


def write_model(project: Path, m: dict) -> Path:
    path = project / "docs" / "costing" / "demo.estimate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(m))
    return path


def run(project: Path, *args: str, capsys) -> tuple[int, dict]:
    code = build_estimate.main([str(project), *args])
    return code, json.loads(capsys.readouterr().out)


def test_figures(project, capsys):
    write_model(project, model())
    code, out = run(project, "demo.estimate.json", "--check", capsys=capsys)
    assert code == 0, out
    f = out["figures"]
    assert f["delivery_months"] == 3.0
    # Dev: 0.235 x 2 x 730 = 343.10 a month, 6 months a year; Prod: 0.235 x 3 x 730 + 0.02 x 500 = 524.65, 12 months
    assert f["environments"][0] == {"name": "Dev", "monthly": 343.1, "daily": 11.28, "delivery": 1029.3,
                                    "run_per_year": 2058.6, "run": 6175.8}
    assert f["environments"][1]["monthly"] == 524.65
    assert f["team"] == {"employees": 4, "mandays": 220.5, "cost": 119700.0}
    assert f["support"] == {"mandays_per_year": 20, "cost_per_year": 10000}
    # run per year 2058.60 + 6295.80 + 10000 + 3000 = 21354.40, three years; build 126253.95; contingency 10%
    assert f["subtotal"] == {"delivery": 126253.95, "run_per_year": 21354.4, "run": 64063.2, "total": 190317.15}
    assert f["grand_total"]["total"] == pytest.approx(209348.87, abs=0.01)
    my = f["multi_year"]
    assert my["periods"] == ["Build", "Year 1", "Year 2", "Year 3", "Year 4", "Year 5"]
    assert my["revenue"] == [0, 50000, 80000, 100000, 0, 0]
    assert my["costs"][:2] == [138879.35, 23489.84] and my["costs"][4:] == [0, 0]
    assert my["cumulative"][3] == pytest.approx(20651.14, abs=0.01)
    assert (f["payback"], f["revenue_total"]) == ("Year 3", 230000)
    assert f["roi"] == pytest.approx(0.0986, abs=0.0001)


def test_cost_escalation_compounds(project, capsys):
    write_model(project, model(assumptions={"delivery_days": 63, "horizon_years": 5, "cost_escalation_pct": 10},
                               revenue=[{"stream": "Subscriptions", "amounts": [1, 2, 3, 4, 5], "cite": "owner: x"}]))
    _, out = run(project, "demo.estimate.json", "--check", capsys=capsys)
    f = out["figures"]
    factor = sum(1.1 ** k for k in range(5))
    assert f["subtotal"]["run"] == pytest.approx(21354.4 * factor, abs=0.01)
    assert f["multi_year"]["costs"][5] == pytest.approx(21354.4 * 1.1 ** 4, abs=0.01)


def test_workbook_sheets_and_formulas(project, capsys):
    path = write_model(project, model())
    code, out = run(project, "demo.estimate.json", capsys=capsys)
    assert code == 0, out
    wb = openpyxl.load_workbook(out["workbook"])
    assert wb.sheetnames == ["Overall", "Multi-Year", "Epics", "Sprints", "Phase Budgets", "Revenue", "Rate Card", "Other Costs", "Run Costs", "Dev", "Prod",
                             "Sources & Gaps"]
    ov = wb["Overall"]
    cells = {c.value: c for row in ov.iter_rows() for c in row if isinstance(c.value, str)}
    assert "=DeliveryDays/WorkingDaysPerMonth" in cells
    assert any(v.startswith("=VLOOKUP(A") for v in cells)
    assert "Grand total" in cells
    assert {"DeliveryDays", "HoursPerMonth", "HorizonYears", "CostEscalation", "HorizonFactor", "Contingency",
            "FxRate"} <= set(wb.defined_names)
    assert wb["Dev"]["H5"].value == "=HoursPerMonth" and wb["Dev"]["I5"].value == "=F5*G5*H5*FxRate"
    assert wb["Dev"]["J5"].value == "=I5*12/365"
    assert {"tblSupport", "tblRunCosts", "tblRevenue", "tblMultiYear"} <= {t for ws in wb for t in ws.tables}
    horizon = [dv for dv in ov.data_validations.dataValidation]
    assert horizon and horizon[0].formula1 == "3" and horizon[0].formula2 == "5"
    my_cells = [c.value for row in wb["Multi-Year"].iter_rows() for c in row if isinstance(c.value, str)]
    assert any(v.startswith("=IF(3<=HorizonYears,") for v in my_cells)
    assert "Payback" in my_cells
    assert wb["Prod"]["H6"].value == 1
    gaps = [c.value for c in wb["Sources & Gaps"]["B"] if c.value]
    assert "DR region not stated" in gaps
    assert any("assumption: 500 GB" in g for g in gaps)
    stamp = json.loads(path.read_text())["stamp"]
    assert SPINE in stamp["files"] and "workbook_mtime" in stamp


def test_formulas_recalculate_to_the_same_figures(project, capsys):
    formulas = pytest.importorskip("formulas")
    write_model(project, model())
    _, out = run(project, "demo.estimate.json", capsys=capsys)
    sol = formulas.ExcelModel().loads(out["workbook"]).finish().calculate()
    values = {str(k).split("]")[-1].upper(): v.value[0][0] for k, v in sol.items() if hasattr(v, "value")}
    grand = [v for k, v in values.items() if k.startswith("OVERALL'!H") and isinstance(v, float)]
    assert max(grand) == pytest.approx(out["figures"]["grand_total"]["total"], abs=0.01)
    f = out["figures"]["multi_year"]
    net = [v for k, v in values.items() if k.startswith("MULTI-YEAR'!I") and isinstance(v, float)]
    assert any(v == pytest.approx(sum(f["net"]), abs=0.01) for v in net)
    payback = [v for k, v in values.items() if k.startswith("MULTI-YEAR'!B") and v == out["figures"]["payback"]]
    assert payback


@pytest.mark.parametrize("change, message", [
    (lambda m: m["environments"][0]["items"][0].update(cite=f"{SPINE} AD-9"), "'AD-9' does not appear"),
    (lambda m: m["environments"][0]["items"][0].update(cite="docs/missing.md"), "does not exist"),
    (lambda m: m["environments"][0]["items"][0].update(cite="../outside.md"), "outside the project"),
    (lambda m: m["environments"][0]["items"][0].update(unit_price=None), "unpriced item belongs in gaps"),
    (lambda m: m["environments"][0]["items"][0].pop("price_source"), "price_source missing"),
    (lambda m: m["team"].append({"role": "Architect", "employees": 1, "cite": "owner: x"}), "not on the rate card"),
    (lambda m: m["team"][0].update(allocation=1.5), "allocation"),
    (lambda m: m["environments"].append({"name": "Overall", "items": []}), "clashes"),
    (lambda m: m.update(currency="AED"), "rate card is in USD"),
    (lambda m: m.update(cloud_currency="EUR"), "fx_rate"),
    (lambda m: m["assumptions"].update(horizon_years=7), "horizon_years"),
    (lambda m: m["assumptions"].update(run_months=12), "replaced by horizon_years"),
    (lambda m: m["revenue"][0].update(amounts=[1, 2]), "each of the 3 years"),
    (lambda m: m["revenue"][0].pop("cite"), "no cite"),
    (lambda m: m["support"].append({"role": "Architect", "mandays_per_year": 5, "cite": "owner: x"}), "support[1]"),
    (lambda m: m["environments"][0].update(run_months_per_year=13), "run_months_per_year"),
])
def test_refuses_invalid_models(project, capsys, change, message):
    m = model()
    change(m)
    write_model(project, m)
    code, out = run(project, "demo.estimate.json", capsys=capsys)
    assert code == 1
    assert any(message in e for e in out["errors"]), out["errors"]


def test_fx_converts_cloud_prices(project, capsys):
    write_model(project, model(cloud_currency="EUR", fx_rate=2, fx_source="test rate"))
    _, out = run(project, "demo.estimate.json", "--check", capsys=capsys)
    assert out["figures"]["environments"][0]["monthly"] == 686.2


def test_drift_and_hand_edits(project, capsys):
    write_model(project, model())
    code, out = run(project, "demo.estimate.json", capsys=capsys)
    workbook = Path(out["workbook"])
    assert run(project, "demo.estimate.json", "--drift", capsys=capsys)[1]["status"] == "current"
    (project / SPINE).write_text("# Spine\n## AD-1 Hosting\nAKS, Dev, Test and Prod.\n")
    code, out = run(project, "demo.estimate.json", "--drift", capsys=capsys)
    assert code == 1 and out["changed"] == [SPINE]
    # a rebuild over an unchanged workbook is fine; over a hand-edited one it is refused
    assert run(project, "demo.estimate.json", capsys=capsys)[0] == 0
    later = workbook.stat().st_mtime + 60
    os.utime(workbook, (later, later))
    code, out = run(project, "demo.estimate.json", capsys=capsys)
    assert code == 2 and "changed after the last build" in out["message"]
    assert run(project, "demo.estimate.json", "--force", capsys=capsys)[0] == 0


USAGE = """kind,work_item,story_no,title,model,first_call,last_call,calls,input_tokens,cache_write_5m_tokens,cache_write_1h_tokens,cache_read_tokens,output_tokens,cost_units
planning,architecture,,,claude-opus-5-5,2026-09-01T09:00:00Z,2026-09-02T10:00:00Z,10,1000000,0,1000000,10000000,100000,0
story,PROJ-1,1.1,Login,claude-haiku-4-5-20251001,2026-09-07T09:00:00Z,2026-09-08T10:00:00Z,5,2000000,0,0,0,0,0
story,PROJ-1,1.1,Login,<synthetic>,2026-09-07T09:00:00Z,2026-09-07T09:00:00Z,1,0,0,0,0,0,0
"""
PRICES = {"source": "https://platform.claude.com/docs/en/about-claude/pricing", "fetched": "2026-09-28", "currency": "USD",
          "models": {"claude-opus-5-5": {"name": "Claude Opus 5.5", "input": 4, "cache_write_5m": 5, "cache_write_1h": 8,
                                         "cache_read": 0.2, "output": 20},
                     "claude-haiku-4-5": {"name": "Claude Haiku 4.5", "input": 1, "cache_write_5m": 1.25,
                                          "cache_write_1h": 2, "cache_read": 0.1, "output": 5}}}
TIMELINE = """category,name,detail,status,started,completed,days,source
project,demo,13 working days,complete,2026-08-28,2026-09-15,19,summary
epic,epic-1,2 stories,done,2026-09-07,2026-09-15,9,git
"""
DAILY = """day,kind,work_item,story_no,title,model,calls,input_tokens,cache_write_5m_tokens,cache_write_1h_tokens,cache_read_tokens,output_tokens,cost_units,cost_usd
2026-09-01,planning,architecture,,,claude-opus-5-5,6,1000000,0,0,0,0,0,4
2026-09-02,planning,architecture,,,claude-opus-5-5,4,0,0,1000000,10000000,100000,0,12
2026-09-07,story,PROJ-1,1.1,Login,claude-haiku-4-5-20251001,5,2000000,0,0,0,0,0,2
"""
# opus: 4 + 8 + 10M x 0.2 = 2 + 0.1M x 20 = 2 -> 16; haiku: 2M x 1 = 2 -> 18 USD
CLAUDE_USD = 18.0


def close_out_files(project: Path) -> None:
    ledger = project / "docs" / "costing" / "token_usage"
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / "claude_usage.csv").write_text(USAGE)
    (ledger / "project_timeline.csv").write_text(TIMELINE)
    (ledger / "claude_usage_daily.csv").write_text(DAILY)
    (project / "_bmad" / "memory" / "agent-scrooge" / "claude-prices.json").write_text(json.dumps(PRICES))


def test_close_out_sheets_tables_and_facts(project, capsys):
    close_out_files(project)
    write_model(project, model(close_out={}))
    code, out = run(project, "demo.estimate.json", capsys=capsys)
    assert code == 0, out
    claude = out["figures"]["claude"]
    assert (claude["calls"], claude["usd"], claude["unpriced_models"]) == (16, CLAUDE_USD, [])
    assert out["figures"]["grand_total"]["total"] == pytest.approx((190317.15 + CLAUDE_USD) * 1.1, abs=0.01)
    wb = openpyxl.load_workbook(out["workbook"])
    assert wb.sheetnames[-6:] == ["Claude Prices", "Claude Usage", "Claude Usage Detail", "Claude Usage Daily",
                                  "Project Timeline", "Sources & Gaps"]
    daily = wb["Claude Usage Daily"]
    assert daily["F7"].value == "claude-haiku-4-5-20251001" and daily["G7"].value == "claude-haiku-4-5"
    tables = {t for ws in wb for t in ws.tables}
    assert {"tblInputs", "tblTeam", "tblCostSummary", "tblEnvDev", "tblEnvProd", "tblRateCard", "tblOtherCosts",
            "tblClaudeUsage", "tblClaudeUsageDetail", "tblClaudeUsageDaily", "tblClaudePrices", "tblTimeline"} <= tables
    assert wb["Claude Usage Detail"]["F6"].value == "claude-haiku-4-5"
    assert '"="&$A5' in wb["Claude Usage"]["H5"].value
    facts = Path(out["facts"]).read_text().splitlines()
    assert facts[0] == "estimate,currency,period,year,category,item,amount"
    assert "demo,USD,Build,0,claude,Claude API usage,18.0" in facts
    assert "demo,USD,Year 2,2,revenue,Subscriptions,80000" in facts
    assert "demo,USD,Year 3,3,support,Run support (OPEX),10000.0" in facts
    assert not any(",Year 4," in line for line in facts)  # past the 3-year horizon
    rows = list(csv.DictReader(Path(out["facts"]).open()))
    costs = sum(float(r["amount"]) for r in rows if r["category"] not in ("revenue", "claude (memo)"))
    assert costs == pytest.approx(out["figures"]["grand_total"]["total"], abs=0.05)



def test_close_out_ai_feedback_sheet(project, capsys):
    close_out_files(project)
    (project / "_bmad" / "memory" / "agent-scrooge" / "ai-feedback.csv").write_text(
        "epic,rating,poor,categories,other,comment,status,recorded\n"
        "epic-1,4,no,,,,rated,2026-09-10\n"
        "epic-2,2,yes,AI testing standards not up to quality; Other,ignored the API contract,,rated,2026-09-15\n"
        "epic-3,,,,,,skipped,2026-09-20\n")
    write_model(project, model(close_out={}))
    code, out = run(project, "demo.estimate.json", capsys=capsys)
    assert code == 0, out
    wb = openpyxl.load_workbook(out["workbook"])
    assert wb.sheetnames[-2:] == ["AI Feedback", "Sources & Gaps"]
    fb = wb["AI Feedback"]
    assert "tblAIFeedback" in fb.tables and fb.tables["tblAIFeedback"].ref == "A4:H7"
    assert (fb["B6"].value, fb["E6"].value, fb["B7"].value) == (2, "ignored the API contract", None)
    assert fb["B8"].value == '=IFERROR(AVERAGE(B5:B7),"")'


def test_close_out_formulas_match(project, capsys):
    formulas = pytest.importorskip("formulas")
    close_out_files(project)
    write_model(project, model(close_out={}))
    _, out = run(project, "demo.estimate.json", capsys=capsys)
    sol = formulas.ExcelModel().loads(out["workbook"]).finish().calculate()
    values = {str(k).split("]")[-1].upper(): v.value[0][0] for k, v in sol.items() if hasattr(v, "value")}
    usage_total = [v for k, v in values.items() if k.startswith("CLAUDE USAGE'!H") and isinstance(v, float)]
    assert max(usage_total) == pytest.approx(CLAUDE_USD)  # the <synthetic> row must not sum every model
    grand = [v for k, v in values.items() if k.startswith("OVERALL'!H") and isinstance(v, float)]
    assert max(grand) == pytest.approx(out["figures"]["grand_total"]["total"], abs=0.01)
    daily = [v for k, v in values.items() if k.startswith("CLAUDE USAGE DAILY'!N") and isinstance(v, float)]
    assert sum(daily) == pytest.approx(CLAUDE_USD)


def test_close_out_memo_currency_and_unpriced(project, capsys):
    close_out_files(project)
    (project / "docs/costing/token_usage/claude_usage.csv").write_text(
        USAGE + "story,PROJ-1,1.1,Login,claude-mystery-9,2026-09-07T09:00:00Z,2026-09-07T09:00:00Z,1,5,0,0,0,0,0\n")
    write_model(project, model(close_out={"in_totals": False}))
    _, out = run(project, "demo.estimate.json", capsys=capsys)
    assert out["figures"]["claude"]["unpriced_models"] == ["claude-mystery-9"]
    assert out["figures"]["grand_total"]["total"] == pytest.approx(209348.87, abs=0.01)  # a memo, not in the totals
    labels = [c.value for c in openpyxl.load_workbook(out["workbook"])["Overall"]["A"] if c.value]
    assert any(str(v).startswith("Memo: Claude API usage") for v in labels)

    card = project / "_bmad" / "memory" / "agent-scrooge" / "rate-card.json"
    card.write_text(card.read_text().replace('"USD"', '"AED"'))
    write_model(project, model(currency="AED", cloud_currency="USD", fx_rate=3.6725, fx_source="t", close_out={}))
    code, out = run(project, "demo.estimate.json", "--check", capsys=capsys)
    assert code == 1 and any("close_out.fx_rate" in e for e in out["errors"])


def test_shipped_example_still_builds(capsys):
    example = Path(__file__).resolve().parents[2] / "assets" / "examples" / "falcon-portal"
    code = build_estimate.main([str(example), "falcon-portal.estimate.json", "--check"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["figures"]["currency"] == "USD"
    assert out["figures"]["grand_total"]["total"] == 990710.26
    assert (out["figures"]["horizon_years"], out["figures"]["payback"]) == (5, "Year 2")
    assert [e["epic"] for e in out["figures"]["epics"]] == ["Customer onboarding", "Review and decision"]
    assert out["figures"]["claude"]["calls"] == 2614


EPICS = [{"number": 1, "epic": "Login", "deliverables": ["1.1 Login"], "days": 21, "api_budget": 50,
          "cite": "_bmad-output/planning-artifacts/epics.md Epic 1: Login"},
         {"number": 2, "epic": "Reports", "deliverables": ["2.1 Reports"], "days": 42,
          "cite": "_bmad-output/planning-artifacts/epics.md Epic 2: Reports"}]


def epics_model(**over) -> dict:
    m = model(epics=copy.deepcopy(EPICS), **over)
    m["assumptions"]["start_date"] = "2026-09-07"  # a Monday
    m["other_costs"][0]["epic"] = 2
    return m


def test_epics_share_the_build_and_date_the_plan(project, capsys):
    write_model(project, epics_model())
    code, out = run(project, "demo.estimate.json", capsys=capsys)
    assert code == 0, out
    e1, e2 = out["figures"]["epics"]
    # 21 and 42 of 63 working days: a third and two thirds of the team (119700) and of the environments during delivery
    assert (e1["share"], e1["team_cost"], e2["team_cost"]) == (pytest.approx(1 / 3, abs=0.01), 39900, 79800)
    assert e1["infra_cost"] + e2["infra_cost"] == pytest.approx(1029.3 + 524.65, abs=0.02)
    assert (e1["other_costs"], e2["other_costs"]) == (0, 5000)
    assert e1["forecast_total"] == pytest.approx(39900 + e1["infra_cost"] + 50, abs=0.01)  # forecast uses the budget
    assert e1["actual_days"] is None and e1["cost_variance"] is None  # no actual dates yet
    assert (e1["start"], e1["end"], e2["start"]) == ("2026-09-07", "2026-10-05", "2026-10-06")
    assert (e1["weeks"], e2["weeks"]) == (4.2, 8.4)
    wb = openpyxl.load_workbook(out["workbook"])
    assert "tblEpics" in wb["Epics"].tables and wb["Other Costs"]["F5"].value == 2
    assert Path(out["workbook"]).with_name("demo.epics.csv").read_text().splitlines()[1].startswith("demo,USD,1,Login,1.1 Login,21")


def test_epic_formulas_match_and_take_actual_claude_costs(project, capsys):
    formulas = pytest.importorskip("formulas")
    close_out_files(project)
    usage = project / "docs/costing/token_usage/claude_usage.csv"
    usage.write_text(usage.read_text().replace("story,PROJ-1,1.1,Login", "story,PROJ-1,1.1,Login"))
    write_model(project, epics_model(close_out={}))
    _, out = run(project, "demo.estimate.json", capsys=capsys)
    e1 = out["figures"]["epics"][0]
    assert e1["api_actual"] == pytest.approx(2.0)  # haiku on story 1.1; opus was a planning phase
    sol = formulas.ExcelModel().loads(out["workbook"]).finish().calculate()
    values = {str(k).split("]")[-1].upper(): v.value[0][0] for k, v in sol.items() if hasattr(v, "value")}
    for col, key in (("J", "team_cost"), ("K", "infra_cost"), ("L", "other_costs"), ("N", "forecast_total"),
                     ("T", "api_actual")):
        for row, epic in ((5, out["figures"]["epics"][0]), (6, out["figures"]["epics"][1])):
            assert values[f"EPICS'!{col}{row}"] == pytest.approx(epic[key], abs=0.01), (col, row)
    assert values["EPICS'!G5"] == 46300  # 2026-10-05 as an Excel date


@pytest.mark.parametrize("change, message", [
    (lambda m: m["epics"][1].update(number=1), "used twice"),
    (lambda m: m["epics"][0].update(days=0), "days (working days)"),
    (lambda m: m["other_costs"][0].update(epic=9), "epic 9 is not one of the epics"),
    (lambda m: m["assumptions"].update(start_date="next monday"), "start_date"),
])
def test_refuses_bad_epics(project, capsys, change, message):
    m = epics_model()
    change(m)
    write_model(project, m)
    code, out = run(project, "demo.estimate.json", "--check", capsys=capsys)
    assert code == 1 and any(message in e for e in out["errors"]), out


def test_forecast_vs_actual_per_epic(project, capsys):
    close_out_files(project)
    # the timeline: epic 1 took Mon 7 Sep to Fri 25 Sep (15 working days, planned 21); epic 2 is still running
    (project / "docs/costing/token_usage/project_timeline.csv").write_text(
        TIMELINE + "epic,epic-1,1 stories,done,2026-09-07,2026-09-25,19,git\n"
                   "epic,epic-2,1 stories,in-progress,2026-09-28,,,git\n")
    m = epics_model(close_out={})
    m["epics"][1]["actual_team_cost"] = 70000  # timesheets
    m["epics"][1]["actual_start"], m["epics"][1]["actual_end"] = "2026-09-28", "2026-11-27"  # entered by hand
    write_model(project, m)
    code, out = run(project, "demo.estimate.json", capsys=capsys)
    assert code == 0, out
    e1, e2 = out["figures"]["epics"]
    assert (e1["actual_days"], e1["days_variance"], e1["days_variance_pct"]) == (15, -6, pytest.approx(-6 / 21, abs=0.001))
    # actual: team and infra scaled by 15/21, plus the actual Claude cost (2.00) instead of the budget (50)
    expected = e1["team_cost"] * 15 / 21 + e1["infra_cost"] * 15 / 21 + 2.0
    assert e1["actual_total"] == pytest.approx(expected, abs=0.01)
    assert e1["cost_variance"] == pytest.approx(expected - e1["forecast_total"], abs=0.01)
    assert (e2["actual_days"], e2["actual_team_cost"]) == (45, 70000)  # 28 Sep to 27 Nov, timesheet cost kept
    formulas = pytest.importorskip("formulas")
    sol = formulas.ExcelModel().loads(out["workbook"]).finish().calculate()
    values = {str(k).split("]")[-1].upper(): v.value[0][0] for k, v in sol.items() if hasattr(v, "value")}
    for col, key in (("Q", "actual_days"), ("R", "days_variance"), ("V", "actual_total"), ("W", "cost_variance")):
        for row, epic in ((5, e1), (6, e2)):
            assert values[f"EPICS'!{col}{row}"] == pytest.approx(epic[key], abs=0.01), (col, row)


def test_sprints_timebox_the_delivery(project, capsys):
    close_out_files(project)
    # sprint 1 is Mon 5 Jan to Fri 16 Jan 2026: a story done on the 15th, $2 of Haiku on the 7th, $3 of Opus on the 20th
    (project / "docs/costing/token_usage/project_timeline.csv").write_text(
        TIMELINE + "story,1-1-login,epic-1,done,2026-01-05,2026-01-15,11,git\n")
    (project / "docs/costing/token_usage/claude_usage_daily.csv").write_text(DAILY.splitlines()[0] + "\n"
        "2026-01-07,story,PROJ-1,1.1,Login,claude-haiku-4-5-20251001,5,2000000,0,0,0,0,0,2\n"
        "2026-01-20,story,PROJ-1,1.1,Login,claude-opus-5-5,2,750000,0,0,0,0,0,3\n")
    m = model(close_out={}, sprints=[{"number": 1, "stories": ["1.1 Login"], "api_budget": 10, "actual_team_cost": 20000}])
    m["assumptions"].update(start_date="2026-01-05", sprint_days=10)
    write_model(project, m)
    code, out = run(project, "demo.estimate.json", capsys=capsys)
    assert code == 0, out
    sprints = out["figures"]["sprints"]
    assert [x["days"] for x in sprints] == [10, 10, 10, 10, 10, 10, 3]  # 63 days of delivery
    s1, s2 = sprints[0], sprints[1]
    assert (s1["start"], s1["end"], s2["start"]) == ("2026-01-05", "2026-01-16", "2026-01-19")
    team_daily, infra_daily = 119700 / 63, (1029.3 + 524.65) / 63
    assert s1["team_cost"] == pytest.approx(10 * team_daily, abs=0.01)
    assert s1["forecast_total"] == pytest.approx(10 * team_daily + 10 * infra_daily + 10, abs=0.01)
    assert (s1["stories_done"], s1["stories_done_list"], s1["api_actual"]) == (1, ["1-1-login"], 2.0)
    assert s1["actual_total"] == pytest.approx(20000 + 10 * infra_daily + 2.0, abs=0.01)  # timesheet team cost
    # the last actual activity is Tue 20 Jan, so sprint 2 has two days worked, at the team's daily cost
    assert (s2["days_worked"], s2["api_actual"]) == (2, 3.0)
    assert s2["actual_team_cost"] == pytest.approx(2 * team_daily, abs=0.01)
    assert sprints[2]["days_worked"] is None and sprints[2]["cost_variance"] is None
    wb = openpyxl.load_workbook(out["workbook"])
    assert "tblSprints" in wb["Sprints"].tables
    assert Path(out["workbook"]).with_name("demo.sprints.csv").read_text().splitlines()[1].startswith("demo,USD,1,2026-01-05")
    formulas = pytest.importorskip("formulas")
    sol = formulas.ExcelModel().loads(out["workbook"]).finish().calculate()
    values = {str(k).split("]")[-1].upper(): v.value[0][0] for k, v in sol.items() if hasattr(v, "value")}
    for col, key in (("D", "days"), ("F", "team_cost"), ("G", "infra_cost"), ("I", "forecast_total"), ("J", "days_worked"),
                     ("K", "stories_done"), ("M", "api_actual"), ("O", "actual_total"), ("P", "cost_variance")):
        for row, sprint in ((5, s1), (6, s2)):
            assert values[f"SPRINTS'!{col}{row}"] == pytest.approx(sprint[key], abs=0.01), (col, row)
    excel_date = (datetime.date.fromisoformat(sprints[6]["start"]) - datetime.date(1899, 12, 30)).days
    assert values["SPRINTS'!B11"] == excel_date and values["SPRINTS'!D11"] == 3  # sprint 7, three days long


def test_sprints_past_the_plan_appear_when_work_runs_over(project, capsys):
    close_out_files(project)
    # 63 planned days end in sprint 7 (from Mon 30 Mar, 3 days); a story finished on Mon 13 Apr, the first day of sprint 8
    (project / "docs/costing/token_usage/project_timeline.csv").write_text(
        TIMELINE + "story,1-1-login,epic-1,done,2026-01-05,2026-04-13,99,git\n")
    (project / "docs/costing/token_usage/claude_usage_daily.csv").write_text(DAILY.splitlines()[0] + "\n")
    m = model(close_out={})
    m["assumptions"].update(start_date="2026-01-05", sprint_days=10)
    write_model(project, m)
    _, out = run(project, "demo.estimate.json", capsys=capsys)
    sprints = out["figures"]["sprints"]
    assert [x["days"] for x in sprints] == [10, 10, 10, 10, 10, 10, 3, 0]
    assert [x["days_worked"] for x in sprints[6:]] == [10, 1]
    assert sprints[7]["forecast_total"] == 0 and sprints[7]["stories_done"] == 1 and sprints[7]["cost_variance"] > 0


PHASES = [{"phase": "Architecture", "api_budget": 20, "cite": "owner: Delivery Manager, 2026-09-28"},
          {"phase": "test design", "api_budget": 5, "cite": "owner: Delivery Manager, 2026-09-28"}]


def test_phase_budgets_against_the_ledger(project, capsys):
    write_model(project, model(phase_budgets=PHASES))
    _, out = run(project, "demo.estimate.json", "--check", capsys=capsys)
    assert out["figures"]["phase_budgets"][0]["api_actual"] is None  # no close-out: budget only
    close_out_files(project)
    write_model(project, model(phase_budgets=PHASES, close_out={}))
    code, out = run(project, "demo.estimate.json", capsys=capsys)
    assert code == 0, out
    arch, test = out["figures"]["phase_budgets"]
    assert (arch["api_actual"], arch["variance"], arch["variance_pct"]) == (16.0, -4.0, -0.2)  # ledger: architecture
    assert (test["api_actual"], test["variance"]) == (0.0, -5.0)
    assert out["figures"]["grand_total"]["total"] == pytest.approx((190317.15 + CLAUDE_USD) * 1.1, abs=0.01)  # not added
    wb = openpyxl.load_workbook(out["workbook"])
    pb = wb["Phase Budgets"]
    assert "tblPhaseBudgets" in pb.tables and pb.tables["tblPhaseBudgets"].ref == "A4:F9"
    assert (pb["A5"].value, pb["B5"].value) == ("Architecture", 20)
    assert "SUMIFS" in pb["C5"].value
    phases = Path(out["workbook"]).with_name("demo.phases.csv").read_text().splitlines()
    assert phases[1] == "demo,USD,Architecture,20,16.0,-4.0,-0.2"
    formulas = pytest.importorskip("formulas")
    sol = formulas.ExcelModel().loads(out["workbook"]).finish().calculate()
    values = {str(k).split("]")[-1].upper(): v.value[0][0] for k, v in sol.items() if hasattr(v, "value")}
    assert values["PHASE BUDGETS'!C5"] == pytest.approx(16.0)
    assert values["PHASE BUDGETS'!D5"] == pytest.approx(-4.0)


@pytest.mark.parametrize(("budgets", "message"), [
    ([{"phase": "spec", "api_budget": 1, "cite": "owner: x"}, {"phase": "Spec", "api_budget": 2, "cite": "owner: x"}],
     "budgeted twice"),
    ([{"phase": "spec", "api_budget": -1, "cite": "owner: x"}], "api_budget is zero or more"),
    ([{"phase": "", "api_budget": 1, "cite": "owner: x"}], "phase is the planning phase"),
    ([{"phase": "spec", "api_budget": 1}], "no cite"),
])
def test_refuses_bad_phase_budgets(project, capsys, budgets, message):
    write_model(project, model(phase_budgets=budgets))
    code, out = run(project, "demo.estimate.json", capsys=capsys)
    assert code == 1 and any(message in e for e in out["errors"]), out["errors"]


def test_infra_cost_csv_has_daily_weekly_monthly_and_annual_opex(project, capsys):
    write_model(project, model())
    code, out = run(project, "demo.estimate.json", capsys=capsys)
    assert code == 0, out
    rows = list(csv.DictReader(Path(out["infra_cost"]).open()))
    assert Path(out["infra_cost"]).name == "demo.infra_cost.csv"
    assert list(rows[0])[:4] == ["estimate", "currency", "line", "environment"]
    dev = next(r for r in rows if r["line"] == "item" and r["environment"] == "Dev")
    monthly = 0.235 * 2 * 730  # always-on: hours per month
    assert float(dev["monthly"]) == pytest.approx(monthly, abs=0.01)
    assert float(dev["daily"]) == pytest.approx(monthly * 12 / 365, abs=0.01)
    assert float(dev["weekly"]) == pytest.approx(monthly * 12 / 365 * 7, abs=0.01)
    assert float(dev["annual"]) == pytest.approx(monthly * 6, abs=0.01)  # Dev runs 6 months a year after go-live
    envs = [r for r in rows if r["line"] == "environment total"]
    assert [r["environment"] for r in envs] == ["Dev", "Prod"]
    total = rows[-1]
    assert total["line"] == "total"
    assert float(total["annual"]) == pytest.approx(sum(x["run_per_year"] for x in out["figures"]["environments"]), abs=0.05)
    assert out["figures"]["infra_opex"]["annual"] == pytest.approx(float(total["annual"]), abs=0.01)

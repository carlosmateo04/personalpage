"""Money in and out: what the channel earned, what it cost, and whether it is ahead.

Entries are kept in state/money.json. Some are typed in (a payout that arrived, a
subscription), some are worked out and never stored: Claude's cost from the count of
requests (an estimate; the provider's bill is what counts) and the monthly costs set in
the settings. A campaign payout marked as paid on the campaign desk and YouTube's own
earnings figure, when the channel lets the app read it, are stored as entries with a key
so that they replace themselves rather than pile up.
"""

from __future__ import annotations

import csv
import io
import secrets
from datetime import date, datetime, timezone
from typing import Any

from . import store
from .config import Config

KINDS = ("income", "cost")


def _stored(cfg: Config) -> list[dict[str, Any]]:
    return list(store.read(cfg, "money", {"entries": []}).get("entries", []))


def _months(since: str, until: date) -> list[str]:
    try:
        year, month = int(since[:4]), int(since[5:7])
    except (ValueError, IndexError):
        return []
    out = []
    while (year, month) <= (until.year, until.month) and len(out) < 240:
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def worked_out(cfg: Config, today: date | None = None) -> list[dict[str, Any]]:
    """The entries nobody typed: Claude's estimated cost per month and the monthly costs from the settings."""
    today = today or datetime.now(timezone.utc).date()
    out: list[dict[str, Any]] = []
    for month, used in sorted(store.read(cfg, "ai_usage").items()):
        if isinstance(used, dict) and used.get("cost"):
            out.append({"id": f"claude:{month}", "date": f"{month}-28", "kind": "cost", "source": "Claude", "amount": round(float(used["cost"]), 2),
                        "note": f"{used.get('requests', 0)} request(s), estimated at list price", "auto": True})
    for fixed in cfg.money.monthly_costs:
        if not isinstance(fixed, dict) or not fixed.get("name"):
            continue
        for month in _months(str(fixed.get("since") or today.strftime("%Y-%m")), today):
            out.append({"id": f"fixed:{fixed['name']}:{month}", "date": f"{month}-01", "kind": "cost", "source": str(fixed["name"]),
                        "amount": round(float(fixed.get("amount") or 0), 2), "note": "every month", "auto": True})
    return out


def entries(cfg: Config, today: date | None = None) -> list[dict[str, Any]]:
    return sorted([*_stored(cfg), *worked_out(cfg, today)], key=lambda e: (e["date"], e["id"]), reverse=True)


def add(cfg: Config, kind: str, source: str, amount: float, when: str = "", note: str = "", key: str = "") -> dict[str, Any]:
    """Record money that came in or went out. With `key`, an earlier entry of the same key is replaced."""
    if kind not in KINDS:
        raise ValueError("an entry is income or a cost")
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError) as exc:
        raise ValueError("the amount has to be a number") from exc
    if amount <= 0 and not key:
        raise ValueError("the amount has to be more than zero")
    when = when or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        datetime.strptime(when, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("the date has to look like 2026-11-20") from exc
    source = " ".join(str(source).split())[:60]
    if not source:
        raise ValueError("say where the money came from or went to")
    entry = {"id": key or secrets.token_hex(5), "date": when, "kind": kind, "source": source, "amount": amount, "note": " ".join(str(note).split())[:200]}
    kept = [e for e in _stored(cfg) if e["id"] != entry["id"]]
    if amount > 0:
        kept.append(entry)
    store.write(cfg, "money", {"entries": kept})
    return entry


def remove(cfg: Config, ident: str) -> None:
    kept = [e for e in _stored(cfg) if e["id"] != ident]
    if len(kept) == len(_stored(cfg)):
        raise KeyError(ident)
    store.write(cfg, "money", {"entries": kept})


def summary(cfg: Config, today: date | None = None) -> dict[str, Any]:
    """Income, costs and what is left: per month (newest first), per year and in all."""
    everything = entries(cfg, today)
    months: dict[str, dict[str, Any]] = {}
    for e in everything:
        month = months.setdefault(e["date"][:7], {"month": e["date"][:7], "income": 0.0, "cost": 0.0, "by": {}})
        month[e["kind"]] = round(month[e["kind"]] + e["amount"], 2)
        signed = e["amount"] if e["kind"] == "income" else -e["amount"]
        month["by"][e["source"]] = round(month["by"].get(e["source"], 0.0) + signed, 2)
    rows = sorted(months.values(), key=lambda m: m["month"], reverse=True)
    for row in rows:
        row["net"] = round(row["income"] - row["cost"], 2)
    years: dict[str, dict[str, float]] = {}
    for row in rows:
        year = years.setdefault(row["month"][:4], {"income": 0.0, "cost": 0.0})
        year["income"], year["cost"] = round(year["income"] + row["income"], 2), round(year["cost"] + row["cost"], 2)
    income, cost = round(sum(r["income"] for r in rows), 2), round(sum(r["cost"] for r in rows), 2)
    return {"currency": cfg.money.currency, "months": rows, "entries": everything[:200],
            "years": [{"year": y, **v, "net": round(v["income"] - v["cost"], 2)} for y, v in sorted(years.items(), reverse=True)],
            "total": {"income": income, "cost": cost, "net": round(income - cost, 2)}}


def as_csv(cfg: Config, year: str = "") -> str:
    """Every entry of a year (or of all time) as a sheet for the tax return: date, kind, source, amount, note."""
    out = io.StringIO()
    sheet = csv.writer(out, lineterminator="\n")
    sheet.writerow(["date", "kind", "source", f"amount ({cfg.money.currency})", "note", "worked out"])
    for e in sorted(entries(cfg), key=lambda e: e["date"]):
        if not year or e["date"].startswith(year):
            sheet.writerow([e["date"], e["kind"], e["source"], f"{e['amount']:.2f}", e.get("note", ""), "yes" if e.get("auto") else ""])
    return out.getvalue()

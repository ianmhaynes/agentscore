"""
AgentScore variance scoring.

Takes the flat listing rows produced by scraper.py and produces per-agent
rankings: vendor variance % (guide vs sold price), accuracy %, and an
A/B/C score band. Mirrors the original AgentScore proof-of-concept model
(Balmain East NSW backup) but adapted for the new data sources:

  - Two adapters with different confidence levels (Ray White = high,
    Cloudhi/Harcourts = medium) are both included, but each agent's
    confidence is reported so a reader can judge how much to trust a
    given row. An agent whose sales are a mix of both is flagged "mixed".
  - Multi-agent (co-listed) sales give FULL credit to each agent, matching
    how the underlying listing rows already duplicate per agent — this
    mirrors the original model's behaviour, just inherited rather than
    re-decided here.
  - Rows missing guide_price or sold_price are excluded from variance
    calculation entirely (can't compute a ratio without both numbers),
    same exclusion rule the original model used for "Contact Agent" /
    undisclosed listings.
"""

from dataclasses import dataclass, field
from collections import defaultdict


MIN_SALES_FOR_RANKING = 3


@dataclass
class AgentScore:
    agent_name: str
    office_name: str
    total_sales: int
    scored_sales: int  # sales with both guide and sold price present
    avg_variance_pct: float  # e.g. 0.027 for +2.7%
    accuracy_pct: float
    score_band: str  # "A" | "B" | "C"
    confidence: str  # "high" | "medium" | "low" | "mixed"
    notes: str = ""


def _score_band(abs_variance_pct):
    if abs_variance_pct <= 0.03:
        return "A"
    elif abs_variance_pct <= 0.07:
        return "B"
    else:
        return "C"


def _notes_for_band(band):
    return {
        "A": "Highly accurate pricing",
        "B": "",
        "C": "Significant underquoting or overpricing",
    }[band]


def score_agents(listings, min_sales=MIN_SALES_FOR_RANKING):
    """
    listings: list of dicts (or objects with the same fields) as produced
    by scraper.py — must have status, agent_name, office_name, guide_price,
    sold_price, extraction_confidence.

    Returns a list of AgentScore, sorted by total_sales descending then
    avg_variance_pct ascending (most accurate first within similar volume),
    matching the original Balmain East ranking's apparent sort order.
    """
    by_agent = defaultdict(list)

    for row in listings:
        get = row.get if isinstance(row, dict) else lambda k: getattr(row, k, "")
        if get("status") != "Sold":
            continue
        agent = get("agent_name")
        if not agent:
            continue
        by_agent[agent].append(row)

    results = []
    for agent_name, rows in by_agent.items():
        total_sales = len(rows)

        scored_rows = []
        for row in rows:
            get = row.get if isinstance(row, dict) else lambda k: getattr(row, k, "")
            guide_raw, sold_raw = get("guide_price"), get("sold_price")
            if not guide_raw or not sold_raw:
                continue
            try:
                guide, sold = float(guide_raw), float(sold_raw)
            except (ValueError, TypeError):
                continue
            if guide <= 0:
                continue  # avoid division by zero / nonsensical guide price
            variance_pct = (sold - guide) / guide
            scored_rows.append((row, variance_pct))

        if len(scored_rows) < min_sales:
            continue  # not enough priced sales to produce a meaningful score,
            # even if total_sales (including unpriced ones) is higher

        avg_variance = sum(v for _, v in scored_rows) / len(scored_rows)
        accuracy = 1 - abs(avg_variance)
        band = _score_band(abs(avg_variance))

        office_names = set()
        confidences = set()
        for row, _ in scored_rows:
            get = row.get if isinstance(row, dict) else lambda k: getattr(row, k, "")
            if get("office_name"):
                office_names.add(get("office_name"))
            confidences.add(get("extraction_confidence") or "high")

        confidence = confidences.pop() if len(confidences) == 1 else "mixed"
        office_name = " / ".join(sorted(office_names)) if office_names else ""

        results.append(AgentScore(
            agent_name=agent_name,
            office_name=office_name,
            total_sales=total_sales,
            scored_sales=len(scored_rows),
            avg_variance_pct=avg_variance,
            accuracy_pct=accuracy,
            score_band=band,
            confidence=confidence,
            notes=_notes_for_band(band),
        ))

    results.sort(key=lambda r: (-r.total_sales, abs(r.avg_variance_pct)))
    return results


def summary_stats(listings, scores):
    """Headline stats for a 'Summary Stats' sheet, mirroring the original
    AgentScore Balmain East output."""
    total = len(listings)

    def get(row, key):
        return row.get(key) if isinstance(row, dict) else getattr(row, key, "")

    has_guide = sum(1 for r in listings if get(r, "guide_price"))
    has_sold = sum(1 for r in listings if get(r, "sold_price"))
    has_agent = sum(1 for r in listings if get(r, "agent_name"))

    offices = sorted(set(get(r, "office_name") for r in listings if get(r, "office_name")))
    adapters = sorted(set(get(r, "source_adapter") for r in listings if get(r, "source_adapter")))

    best = min(scores, key=lambda s: abs(s.avg_variance_pct)) if scores else None
    highest_volume = max(scores, key=lambda s: s.total_sales) if scores else None

    result = {
        "Total listing rows": total,
        "Rows with guide price": f"{has_guide} ({has_guide/total*100:.0f}%)" if total else "0",
        "Rows with sold price": f"{has_sold} ({has_sold/total*100:.0f}%)" if total else "0",
        "Rows with agent name": f"{has_agent} ({has_agent/total*100:.0f}%)" if total else "0",
        "Offices included": ", ".join(offices) if offices else "—",
        "Data sources": ", ".join(adapters) if adapters else "—",
        "Agents ranked (min 3 priced sales)": len(scores),
        "Best accuracy score": (
            f"{best.agent_name} {best.avg_variance_pct*100:+.1f}% ({best.score_band})"
            if best else "—"
        ),
        "Highest volume agent": (
            f"{highest_volume.agent_name} — {highest_volume.total_sales} sales"
            if highest_volume else "—"
        ),
    }

    # Per-adapter guide-price coverage — surfaces the real, structural gap
    # confirmed via live inspection (June 2026): Ray White retains a guide
    # price even on sold listings, while many Harcourts/Cloudhi listings
    # are sold without ever publishing one at all ("sold without a price"
    # disclaimer). This is NOT a scraping bug — see README for detail.
    sold_rows = [r for r in listings if get(r, "status") == "Sold"]
    for adapter in adapters:
        adapter_sold = [r for r in sold_rows if get(r, "source_adapter") == adapter]
        if not adapter_sold:
            continue
        priced = sum(1 for r in adapter_sold if get(r, "guide_price") and get(r, "sold_price"))
        pct = priced / len(adapter_sold) * 100 if adapter_sold else 0
        result[f"  {adapter}: sold listings with usable guide price"] = (
            f"{priced} of {len(adapter_sold)} ({pct:.0f}%)"
        )

    return result

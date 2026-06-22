import os
import sys

# Ensure this file's own directory is importable regardless of the working
# directory Vercel's runtime invokes it from — local `python3 app.py` from
# inside api/ already has this on sys.path, but Vercel's serverless import
# mechanism does not, which causes "No module named 'scraper'" there.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, send_file
import csv
import io
from datetime import date
from scraper import scrape_offices
from scoring import score_agents, summary_stats
from discovery import discover_agencies

app = Flask(__name__)

_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


@app.route("/")
def index():
    # Read the template directly rather than relying on Flask's default
    # template-folder resolution, which has been unreliable across
    # different serverless runtimes (working directory assumptions vary).
    with open(os.path.join(_TEMPLATE_DIR, "index.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.route("/api/discover", methods=["POST"])
def discover():
    data = request.get_json(force=True)
    area = data.get("area", "").strip()
    api_key = data.get("apiKey", "").strip()
    if not area:
        return jsonify({"error": "No suburb/postcode provided"}), 400
    if not api_key:
        return jsonify({"error": "No Google Places API key provided"}), 400

    log_lines = []

    def log(msg):
        log_lines.append(msg)

    agencies = discover_agencies(area, api_key=api_key, log=log)
    return jsonify({"agencies": agencies, "log": log_lines})


@app.route("/api/scrape", methods=["POST"])
def scrape():
    data = request.get_json(force=True)
    urls = data.get("urls", [])
    if not urls:
        return jsonify({"error": "No URLs provided"}), 400

    # Optional — only used by GenericFallbackAdapter's tier 4 (LLM
    # extraction), and only when every free tier fails to find a usable
    # address. Sent per-request, never stored server-side, same pattern
    # as the Google Places API key in /api/discover.
    llm_api_key = data.get("llmApiKey", "").strip() or None

    log_lines = []

    def log(msg):
        log_lines.append(msg)

    result = scrape_offices(urls, log=log, llm_api_key=llm_api_key)
    result["log"] = log_lines

    return jsonify(result)


@app.route("/api/export.csv", methods=["POST"])
def export_csv():
    data = request.get_json(force=True)
    listings = data.get("listings", [])
    if not listings:
        return jsonify({"error": "No data to export — run a scrape first"}), 400

    output = io.StringIO()
    fieldnames = list(listings[0].keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(listings)

    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    return send_file(
        mem,
        mimetype="text/csv",
        as_attachment=True,
        download_name="agentscore_listings.csv",
    )


@app.route("/api/export.xlsx", methods=["POST"])
def export_xlsx():
    try:
        import openpyxl
        from openpyxl.utils import get_column_letter
    except ImportError:
        return jsonify({"error": "openpyxl not installed on server"}), 500

    data = request.get_json(force=True)
    listings = data.get("listings", [])
    if not listings:
        return jsonify({"error": "No data to export — run a scrape first"}), 400

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Listings"

    fieldnames = list(listings[0].keys())
    ws.append(fieldnames)
    for row in listings:
        ws.append([row.get(f, "") for f in fieldnames])

    # Light formatting: bold header, autosize-ish columns
    for col_idx, name in enumerate(fieldnames, start=1):
        ws.cell(row=1, column=col_idx).font = openpyxl.styles.Font(bold=True)
        ws.column_dimensions[get_column_letter(col_idx)].width = max(14, len(name) + 2)

    mem = io.BytesIO()
    wb.save(mem)
    mem.seek(0)
    return send_file(
        mem,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="agentscore_listings.xlsx",
    )


@app.route("/api/export.rankings.xlsx", methods=["POST"])
def export_rankings_xlsx():
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        return jsonify({"error": "openpyxl not installed on server"}), 500

    data = request.get_json(force=True)
    listings = data.get("listings", [])
    if not listings:
        return jsonify({"error": "No data to export — run a scrape first"}), 400

    scores = score_agents(listings)
    stats = summary_stats(listings, scores)

    wb = openpyxl.Workbook()

    # --- Sheet 1: Agent Rankings (matches original AgentScore template) ---
    ws = wb.active
    ws.title = "Agent Rankings"

    offices = sorted(set(
        l.get("office_name") for l in listings if l.get("office_name")
    ))
    office_label = ", ".join(offices) if offices else "All offices"

    ws.append([f"AgentScore — {office_label}  |  {date.today().isoformat()}"])
    ws.cell(row=1, column=1).font = Font(bold=True, size=13, name="Arial")
    ws.append([
        "Vendor Variance = (Sold Price - Guide Price) / Guide Price  |  "
        "Listings without both prices excluded  |  Min 3 priced sales to rank  |  "
        "Source: AgentScore live scrape (Ray White Dynamics, Harcourts/Cloudhi)"
    ])
    ws.cell(row=2, column=1).font = Font(italic=True, size=9, name="Arial", color="666666")
    ws.append([])

    headers = ["#", "Agent", "Office", "Total Sales", "Priced Sales", "Avg Variance %", "Accuracy %", "Score", "Confidence", "Notes"]
    header_row = 4
    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=header_row, column=col_idx)
        cell.font = Font(bold=True, name="Arial")

    for i, s in enumerate(scores, start=1):
        ws.append([
            i,
            s.agent_name,
            s.office_name,
            s.total_sales,
            s.scored_sales,
            round(s.avg_variance_pct, 4),
            round(s.accuracy_pct, 4),
            s.score_band,
            s.confidence,
            s.notes,
        ])

    # Number formatting: percentages as 0.0%, matching xlsx skill conventions
    last_row = header_row + len(scores)
    for row_idx in range(header_row + 1, last_row + 1):
        ws.cell(row=row_idx, column=6).number_format = "0.0%;(0.0%)"
        ws.cell(row=row_idx, column=7).number_format = "0.0%"
        # Medium/mixed confidence rows get a subtle flag so a reader knows
        # to treat them with more caution than high-confidence Ray White rows
        conf_cell = ws.cell(row=row_idx, column=9)
        if conf_cell.value in ("medium", "low", "mixed"):
            conf_cell.font = Font(name="Arial", color="9C6500")

    col_widths = {1: 4, 2: 22, 3: 32, 4: 12, 5: 12, 6: 14, 7: 11, 8: 7, 9: 11, 10: 36}
    for col_idx, width in col_widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # Use a professional font throughout, per xlsx skill guidance
    for row in ws.iter_rows():
        for cell in row:
            if cell.font is None or cell.font.name != "Arial":
                existing = cell.font
                cell.font = Font(
                    name="Arial",
                    bold=existing.bold if existing else False,
                    italic=existing.italic if existing else False,
                    size=existing.size if existing else 11,
                    color=existing.color if existing else None,
                )

    # --- Sheet 2: Summary Stats ---
    ws2 = wb.create_sheet("Summary Stats")
    ws2.append(["AgentScore — Key Statistics"])
    ws2.cell(row=1, column=1).font = Font(bold=True, size=13, name="Arial")
    ws2.append([])
    for label, value in stats.items():
        ws2.append([label, value])
    ws2.column_dimensions["A"].width = 38
    ws2.column_dimensions["B"].width = 40
    for row in ws2.iter_rows():
        for cell in row:
            cell.font = Font(name="Arial", bold=(cell.row == 1))

    # --- Sheet 3: Excluded / Unscored rows for transparency ---
    ws3 = wb.create_sheet("Excluded From Scoring")
    ws3.append([
        "Listings excluded from scoring (missing price, active status, "
        "or agent below the 3-sale minimum). Included here for transparency, "
        "not used in the rankings above."
    ])
    ws3.cell(row=1, column=1).font = Font(italic=True, size=9, name="Arial", color="666666")
    ws3.append([])
    excl_headers = ["Status", "Address", "Agent", "Office", "Guide Price", "Sold Price", "Reason"]
    ws3.append(excl_headers)
    for cell in ws3[3]:
        cell.font = Font(bold=True, name="Arial")

    ranked_agents = {s.agent_name for s in scores}
    for l in listings:
        status = l.get("status", "")
        agent = l.get("agent_name", "")
        guide, sold = l.get("guide_price"), l.get("sold_price")
        source = l.get("source_adapter", "")
        reason = ""
        if status != "Sold":
            reason = "Active listing (not yet sold)"
        elif not guide and not sold:
            reason = "No price published on listing page"
        elif not guide and source == "cloudhi_rex":
            # Confirmed via live inspection: many Harcourts/Cloudhi sold
            # listings genuinely never had a guide price published at all
            # (e.g. "sold without a price" disclaimer) — not a scraping
            # gap, the source data itself only has the final sold figure.
            reason = "No guide price published (sold without disclosed price)"
        elif not guide or not sold:
            reason = "Missing guide or sold price"
        elif agent not in ranked_agents:
            reason = "Agent below 3-sale minimum"
        if reason:
            ws3.append([
                status, l.get("address", ""), agent, l.get("office_name", ""),
                guide, sold, reason,
            ])
    excl_widths = {1: 10, 2: 42, 3: 22, 4: 32, 5: 12, 6: 12, 7: 30}
    for col_idx, width in excl_widths.items():
        ws3.column_dimensions[get_column_letter(col_idx)].width = width
    for row in ws3.iter_rows(min_row=4):
        for cell in row:
            cell.font = Font(name="Arial")

    mem = io.BytesIO()
    wb.save(mem)
    mem.seek(0)
    return send_file(
        mem,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="agentscore_rankings.xlsx",
    )


if __name__ == "__main__":
    app.run(debug=True, port=5050)

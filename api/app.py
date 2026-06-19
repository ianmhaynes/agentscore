from flask import Flask, request, jsonify, send_file, render_template
import csv
import io
from scraper import scrape_offices

app = Flask(__name__)

# In-memory store of the most recent scrape result, keyed by a simple token,
# so the export endpoints can re-serve the same data without re-scraping.
_last_result = {"listings": []}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scrape", methods=["POST"])
def scrape():
    data = request.get_json(force=True)
    urls = data.get("urls", [])
    if not urls:
        return jsonify({"error": "No URLs provided"}), 400

    log_lines = []

    def log(msg):
        log_lines.append(msg)

    result = scrape_offices(urls, log=log)
    result["log"] = log_lines

    _last_result["listings"] = result["listings"]

    return jsonify(result)


@app.route("/api/export.csv")
def export_csv():
    listings = _last_result.get("listings", [])
    if not listings:
        return jsonify({"error": "No data to export yet — run a scrape first"}), 400

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


@app.route("/api/export.xlsx")
def export_xlsx():
    try:
        import openpyxl
        from openpyxl.utils import get_column_letter
    except ImportError:
        return jsonify({"error": "openpyxl not installed on server"}), 500

    listings = _last_result.get("listings", [])
    if not listings:
        return jsonify({"error": "No data to export yet — run a scrape first"}), 400

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


if __name__ == "__main__":
    app.run(debug=True, port=5050)

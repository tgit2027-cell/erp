"""Small standard-library writers (Office Open XML and PDF) for controlled report exports."""

from io import BytesIO
from decimal import Decimal, InvalidOperation
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape

from .reporting_services import ReportingValidationError


def _excel_column(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _xlsx_cell(reference, value, numeric=False):
    if value is None:
        return f'<c r="{reference}"/>'
    text = str(value)
    if numeric:
        try:
            number = Decimal(text)
            if number.is_finite():
                return f'<c r="{reference}" t="n"><v>{escape(text)}</v></c>'
        except (InvalidOperation, TypeError, ValueError):
            pass
    return f'<c r="{reference}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'


def report_export_tables(report):
    tables = []
    if report.get("fields"):
        tables.append(("Report rows", "rows", report["fields"], report.get("rows", [])))
    summary = report.get("summary")
    if isinstance(summary, dict) and summary:
        tables.append(("Summary", "summary", list(summary), [summary]))
    for key, title in (
        ("trend", "Trend"),
        ("best_selling_items", "Best-selling items"),
        ("best_selling_categories", "Best-selling categories"),
        ("expense_account_detail", "Posted expense account detail"),
    ):
        rows = report.get(key)
        if isinstance(rows, list) and rows:
            fields = list(dict.fromkeys(field for row in rows for field in row))
            tables.append((title, key, fields, rows))
    return tables


def report_export_manifest(report):
    manifest = []
    row_count = 0
    for _title, key, fields, rows in report_export_tables(report):
        prefix = "" if key == "rows" else f"{key}."
        manifest.extend(f"{prefix}{field}" for field in fields)
        row_count += len(rows)
    return list(dict.fromkeys(manifest)), row_count


def _xlsx(report, extra_numeric=()):
    numeric_fields = set(extra_numeric) | {
        "selling_price",
        "quantity",
        "sales_amount",
        "supplier_purchase_value",
        "landed_charges",
        "posted_expense_accounts",
        "revenue",
        "cost_of_goods_sold",
        "gross_margin",
        "gross_margin_percent",
        "inventory_value",
        "on_hand_quantity",
        "reserved_quantity",
        "supplier_unit_cost",
        "purchase_value",
        "landed_cost_allocation",
        "transferred_value",
        "budget",
        "actual",
        "variance_actual_minus_budget",
    }
    sheet_rows = [
        f'<row r="1">{_xlsx_cell("A1", report["title"])}</row>',
        f'<row r="2">{_xlsx_cell("A2", report["policy_label"])}</row>',
        f'<row r="3">{_xlsx_cell("A3", report.get("note", ""))}</row>',
    ]
    row_number = 5
    filter_ref = "A5:A5"
    for title, _key, fields, rows in report_export_tables(report):
        sheet_rows.append(f'<row r="{row_number}">{_xlsx_cell("A" + str(row_number), title)}</row>')
        row_number += 1
        headers = "".join(
            _xlsx_cell(f"{_excel_column(index)}{row_number}", field)
            for index, field in enumerate(fields, 1)
        )
        sheet_rows.append(f'<row r="{row_number}">{headers}</row>')
        header_row = row_number
        row_number += 1
        for row in rows:
            cells = "".join(
                _xlsx_cell(
                    f"{_excel_column(index)}{row_number}",
                    row.get(field),
                    numeric=field in numeric_fields,
                )
                for index, field in enumerate(fields, 1)
            )
            sheet_rows.append(f'<row r="{row_number}">{cells}</row>')
            row_number += 1
        filter_ref = f"A{header_row}:{_excel_column(max(len(fields), 1))}{max(row_number - 1, header_row)}"
        row_number += 1
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        f'<autoFilter ref="{filter_ref}"/></worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    return buffer.getvalue()


def _word_cell(value, header=False):
    text = escape("" if value is None else str(value))
    bold = "<w:rPr><w:b/></w:rPr>" if header else ""
    return (
        "<w:tc><w:tcPr><w:tcW w:w=\"2400\" w:type=\"dxa\"/></w:tcPr>"
        f"<w:p><w:r>{bold}<w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p></w:tc>"
    )


def _docx(report):
    title = escape(report["title"])
    policy = escape(report["policy_label"])
    note = escape(report.get("note", ""))
    tables = []
    for table_title, _key, fields, rows in report_export_tables(report):
        table_rows = [
            "<w:tr>" + "".join(_word_cell(field, header=True) for field in fields) + "</w:tr>"
        ]
        for row in rows:
            table_rows.append(
                "<w:tr>" + "".join(_word_cell(row.get(field)) for field in fields) + "</w:tr>"
            )
        tables.append(
            f'<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>{escape(table_title)}</w:t></w:r></w:p>'
            '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>'
            '<w:tblBorders><w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/>'
            '<w:bottom w:val="single" w:sz="4"/><w:right w:val="single" w:sz="4"/>'
            '<w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/>'
            '</w:tblBorders></w:tblPr>'
            f'{"".join(table_rows)}</w:tbl>'
        )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>'
        f'<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>{title}</w:t></w:r></w:p>'
        f'<w:p><w:r><w:t>{policy}</w:t></w:r></w:p>'
        f'<w:p><w:r><w:t>{note}</w:t></w:r></w:p>'
        f'{"".join(tables)}'
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720"/>'
        '</w:sectPr></w:body></w:document>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
        '<w:rPr><w:sz w:val="20"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
        '<w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style></w:styles>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    document_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '</Types>'
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/_rels/document.xml.rels", document_rels)
    return buffer.getvalue()


# PDF: A4 landscape, the built-in Helvetica fonts, one table after another,
# repeating column headers on each new page and numbering the pages.
PDF_WIDTH, PDF_HEIGHT, PDF_MARGIN = 842, 595, 36
PDF_FONT_SIZE, PDF_ROW = 7.5, 12


def _pdf_text(value):
    """Latin-1 text for the built-in fonts; characters they cannot draw become '?'."""
    text = "" if value is None else str(value)
    text = text.replace("\u2014", "-").replace("\u2013", "-").replace("\u2019", "'").replace("\u00b7", "-")
    return text.encode("latin-1", "replace").decode("latin-1")


def _pdf_escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_fit(text, width, size):
    # Helvetica averages about half the font size per character.
    limit = max(int(width / (size * 0.5)), 1)
    return text if len(text) <= limit else text[: max(limit - 1, 1)] + "~"


def _pdf_pages(report):
    pages, ops = [], []
    y = 0

    def text(x, y, value, size=PDF_FONT_SIZE, bold=False, right_edge=None):
        value = _pdf_escape(_pdf_text(value))
        if right_edge is not None:
            x = right_edge - len(value) * size * 0.5
        ops.append(f"BT /{'F2' if bold else 'F1'} {size} Tf {x:.1f} {y:.1f} Td ({value}) Tj ET")

    def new_page():
        nonlocal ops, y
        ops = []
        pages.append(ops)
        text(PDF_MARGIN, PDF_HEIGHT - PDF_MARGIN, report["title"], size=13, bold=True)
        text(PDF_MARGIN, PDF_HEIGHT - PDF_MARGIN - 14, report["policy_label"], size=7)
        y = PDF_HEIGHT - PDF_MARGIN - 34

    def line(y):
        ops.append(f"0.75 w {PDF_MARGIN} {y:.1f} m {PDF_WIDTH - PDF_MARGIN} {y:.1f} l S")

    new_page()
    if report.get("note"):
        text(PDF_MARGIN, y, report["note"], size=8)
        y -= 18
    usable = PDF_WIDTH - 2 * PDF_MARGIN
    for title, _key, fields, rows in report_export_tables(report):
        if not fields:
            continue
        width = usable / len(fields)
        numeric = {field for field in fields if rows and all(_is_number(row.get(field)) for row in rows if row.get(field) not in (None, ""))}

        def header():
            nonlocal y
            for index, field in enumerate(fields):
                x = PDF_MARGIN + index * width
                label = _pdf_fit(_pdf_text(field).replace("_", " "), width - 4, PDF_FONT_SIZE)
                text(x + 2, y, label, bold=True, right_edge=(x + width - 2) if field in numeric else None)
            line(y - 3)
            y -= PDF_ROW

        if y < PDF_MARGIN + 4 * PDF_ROW:
            new_page()
        text(PDF_MARGIN, y, title, size=10, bold=True)
        y -= PDF_ROW + 2
        header()
        for row in rows:
            if y < PDF_MARGIN + PDF_ROW:
                new_page()
                text(PDF_MARGIN, y, f"{title} (continued)", size=10, bold=True)
                y -= PDF_ROW + 2
                header()
            for index, field in enumerate(fields):
                x = PDF_MARGIN + index * width
                value = _pdf_fit(_pdf_text(row.get(field, "")), width - 4, PDF_FONT_SIZE)
                text(x + 2, y, value, right_edge=(x + width - 2) if field in numeric else None)
            y -= PDF_ROW
        y -= PDF_ROW
    for number, page in enumerate(pages, start=1):
        label = _pdf_escape(f"Page {number} of {len(pages)}")
        page.append(f"BT /F1 7 Tf {PDF_WIDTH - PDF_MARGIN - 50:.1f} {PDF_MARGIN - 18:.1f} Td ({label}) Tj ET")
    return pages


def _is_number(value):
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return True
    try:
        Decimal(str(value))
        return True
    except (InvalidOperation, ValueError):
        return False


def _pdf(report):
    pages = _pdf_pages(report)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        None,  # the page tree, filled in once the page numbers are known
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    kids = []
    for ops in pages:
        stream = "\n".join(ops).encode("latin-1")
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PDF_WIDTH} {PDF_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {len(objects)} 0 R >>".encode()
        )
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    title = _pdf_escape(_pdf_text(report["title"]))
    objects.append(f"<< /Title ({title}) /Producer (Tivoli ERP synthetic export) >>".encode("latin-1"))
    out = BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {len(objects)} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def render_table_xlsx(*, title, note, policy_label, fields, rows, numeric, summary=None):
    """One-sheet workbook for screens outside the report builder (for example the general ledger).

    fields are the column headers in order; rows are dicts keyed by those headers;
    numeric names the headers whose values are written as numbers.
    """
    report = {"title": title, "note": note, "policy_label": policy_label, "fields": fields, "rows": rows}
    if summary:
        report["summary"] = summary
    return _xlsx(report, extra_numeric=numeric)


def render_report_export(report, file_format):
    if file_format == "xlsx":
        return _xlsx(report)
    if file_format == "docx":
        return _docx(report)
    if file_format == "pdf":
        return _pdf(report)
    raise ReportingValidationError("Export format must be xlsx, docx or pdf.")

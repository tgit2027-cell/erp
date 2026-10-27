"""Small standard-library Office Open XML writers for controlled report exports."""

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


def _xlsx(report):
    numeric_fields = {
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


def render_report_export(report, file_format):
    if file_format == "xlsx":
        return _xlsx(report)
    if file_format == "docx":
        return _docx(report)
    raise ReportingValidationError("Export format must be xlsx or docx.")

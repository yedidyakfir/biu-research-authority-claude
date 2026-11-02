"""End-to-end checks for source preservation, row coverage, and Excel export."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from openpyxl import Workbook, load_workbook


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_report import validate  # noqa: E402


class WorkflowTest(unittest.TestCase):
    def test_prepare_classify_render_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "ledger.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Ledger"
            sheet.append(["Account", "Description", "Amount"])
            sheet.append(["Lab", "Sample analysis", 120.5])
            sheet.append([None, "Subtotal", 120.5])
            sheet.append(["Clearing", "Transfer", -120.5])
            note = workbook.create_sheet("Notes")
            note["A1"] = "Keep this sheet"
            workbook.save(source)
            workbook.close()

            output = folder / "output"
            subprocess.run([
                sys.executable, str(ROOT / "scripts" / "prepare_report.py"), str(source),
                "--output-dir", str(output), "--foundation", "isf", "--sheet", "Ledger",
                "--header-row", "1", "--record-column", "A", "--description-column", "B",
                "--amount-column", "C", "--account-column", "A",
            ], check=True, capture_output=True, text=True)
            copied = output / "source-copy-ledger.xlsx"
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), hashlib.sha256(copied.read_bytes()).digest())
            report_path = output / "review.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual([(r["row"], r["kind"]) for r in report["records"]], [(2, "transaction"), (3, "structural"), (4, "transaction")])
            self.assertEqual([sheet["name"] for sheet in report["sheets"]], ["Ledger", "Notes"])

            first, _, third = report["records"]
            first["classification"] = {"category_id": "services", "status": "classified", "rationale": "Sample analysis service", "uncertainty_reason": ""}
            third["classification"] = {"category_id": None, "status": "not_expense", "rationale": "Clearing transfer", "uncertainty_reason": ""}
            self.assertEqual(validate(report)[0], [])
            first["classification"]["status"] = "needs_review"
            first["classification"]["uncertainty_reason"] = ""
            self.assertTrue(any("uncertainty_reason" in error for error in validate(report)[0]))
            first["classification"]["status"] = "classified"
            report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

            html = output / "review.html"
            subprocess.run([sys.executable, str(ROOT / "scripts" / "render_review.py"), str(report_path), "--output", str(html)], check=True, capture_output=True, text=True)
            content = html.read_text(encoding="utf-8")
            self.assertIn('id="report-data"', content)
            self.assertIn("Sample analysis service", content)

            exported = output / "reviewed.xlsx"
            js = (
                "const fs=require('fs');"
                f"const x=require({json.dumps(str(ROOT / 'assets' / 'xlsx_export.js'))});"
                f"const r=JSON.parse(fs.readFileSync({json.dumps(str(report_path))},'utf8'));"
                f"fs.writeFileSync({json.dumps(str(exported))},x.buildXlsx(r));"
            )
            subprocess.run(["node", "-e", js], check=True, capture_output=True, text=True)
            original_book = load_workbook(source, read_only=True, data_only=False)
            exported_book = load_workbook(exported, read_only=True, data_only=False)
            self.assertEqual(exported_book.sheetnames, original_book.sheetnames)
            original_rows = list(original_book["Ledger"].values)
            exported_rows = list(exported_book["Ledger"].values)
            self.assertEqual([row[:3] for row in exported_rows], original_rows)
            self.assertEqual(exported_rows[1][3], "שירותים")
            self.assertEqual(exported_rows[3][4], "לא הוצאה")
            self.assertEqual(exported_book["Notes"]["A1"].value, "Keep this sheet")
            original_book.close()
            exported_book.close()


if __name__ == "__main__":
    unittest.main()

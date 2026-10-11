import contextlib
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from catalog import InputError, ROOT, ResourceReferences, main, parse_price, parse_stock, process_csv, render_catalog, write_outputs


class CatalogTests(unittest.TestCase):
    def setUp(self):
        test_temp = ROOT / "tmp"
        test_temp.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=test_temp)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def fixture(self, rows, header=None, delimiter=";", bom=False):
        path = self.base / "input.csv"
        with path.open("w", encoding="utf-8-sig" if bom else "utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter=delimiter)
            writer.writerow(header or ["sku", "name", "category", "price", "stock", "description"])
            writer.writerows(rows)
        return path

    def row(self, **changes):
        data = dict(sku="A-1", name="Блокнот", category="Бумага", price="123.45", stock="2", description="Демо")
        data.update(changes)
        return list(data.values())

    def test_normalization_bom_and_optional_description(self):
        path = self.fixture([[" a-1 ", "  Имя   товара ", " бумага ", "1\u00a0290,5", "002"]],
                            header=["sku", "name", "category", "price", "stock"], bom=True)
        result = process_csv(path)
        p = result.products[0]
        self.assertEqual((p.sku, p.name, p.category, p.price, p.stock, p.description),
                         ("A-1", "Имя товара", "Бумага", "1290.50", 2, ""))
        self.assertFalse(result.issues)

    def test_decimal_precision(self):
        self.assertEqual(parse_price("0.10"), "0.10")
        self.assertEqual(parse_price("1000000"), "1000000.00")
        self.assertEqual(parse_price("12 345,67"), "12345.67")

    def test_invalid_prices(self):
        for value in ("", "0", "-10", "+10", "NaN", "Infinity", "1e3", "12 34", "1,234.50", "1.999", "1000000.01", "10 ₽", "1  000", "١٢"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_price(value)

    def test_stock_boundaries(self):
        self.assertEqual(parse_stock("0"), 0)
        self.assertEqual(parse_stock("999999"), 999999)
        for value in ("", "-1", "1.5", "1e2", "1000000", "два", " 1 2 "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_stock(value)

    def test_required_fields_independently(self):
        for field in ("sku", "name", "category", "price", "stock"):
            with self.subTest(field=field):
                result = process_csv(self.fixture([self.row(**{field: " "})]))
                self.assertFalse(result.products)
                self.assertEqual([(i.field, i.code) for i in result.issues], [(field, "required")])

    def test_all_duplicate_occurrences_excluded(self):
        result = process_csv(self.fixture([self.row(), self.row(sku=" a-1 "), self.row(sku="B-2")]))
        self.assertEqual([p.sku for p in result.products], ["B-2"])
        self.assertEqual([(i.source_record, i.code) for i in result.issues], [(2, "duplicate_sku"), (3, "duplicate_sku")])

    def test_duplicate_conflict_with_invalid_row(self):
        result = process_csv(self.fixture([self.row(), self.row(name="")]))
        self.assertFalse(result.products)
        self.assertEqual(result.summary["rejected_records"], 2)

    def test_unknown_category(self):
        result = process_csv(self.fixture([self.row(category="Мебель")]))
        self.assertFalse(result.products)
        self.assertEqual(result.issues[0].code, "unknown_category")

    def test_multiple_errors_in_one_record(self):
        result = process_csv(self.fixture([self.row(sku="", price="нет")]))
        self.assertEqual(result.summary, {"input_records": 1, "accepted_records": 0, "rejected_records": 1, "issue_count": 2})
        self.assertEqual([i.code for i in result.issues], ["required", "invalid_price"])

    def test_bad_sku(self):
        for value in ("=X", "К-1", "A B", "-X", "A" * 33):
            with self.subTest(value=value):
                result = process_csv(self.fixture([self.row(sku=value)]))
                self.assertFalse(result.products)
                self.assertIn("sku_format", [i.code for i in result.issues])

    def test_text_length_limits(self):
        result = process_csv(self.fixture([self.row(name="X" * 101, description="Y" * 501)]))
        self.assertEqual([i.code for i in result.issues], ["too_long", "too_long"])

    def test_formula_like_text_rejected(self):
        for field in ("name", "description"):
            for sign in ("=", "+", "-", "@"):
                with self.subTest(field=field, sign=sign):
                    result = process_csv(self.fixture([self.row(**{field: " " + sign + "DEMO"})]))
                    self.assertFalse(result.products)
                    self.assertEqual(result.issues[0].code, "formula_text")

    def test_html_escaped(self):
        result = process_csv(self.fixture([self.row(name='<script>alert("x")</script>', description='<img onerror="alert(1)"> & text')]))
        page = render_catalog(result)
        self.assertNotIn("<script>", page)
        self.assertNotIn('<img onerror=', page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("&amp; text", page)

    def test_column_count(self):
        result = process_csv(self.fixture([self.row() + ["лишнее"], self.row(sku="B-2")[:-2]]))
        self.assertFalse(result.products)
        self.assertEqual(sum(i.code == "column_count" for i in result.issues), 2)

    def test_comma_delimiter_and_quoted_multiline(self):
        path = self.fixture([self.row(description="Строка 1\nСтрока 2; деталь, текст")], delimiter=",")
        result = process_csv(path, ",")
        self.assertEqual(result.products[0].description, "Строка 1 Строка 2; деталь, текст")
        self.assertEqual(result.products[0].source_record, 2)

    def test_multiline_issue_has_physical_end_line(self):
        result = process_csv(self.fixture([self.row(name="", description="строка\nвторая")]))
        self.assertEqual((result.issues[0].source_record, result.issues[0].source_line_end), (2, 3))

    def test_invalid_headers(self):
        for header in (["sku", "name"], ["sku", "name", "category", "price", "stock", "extra"],
                       ["sku", "name", "category", "price", "stock", "sku"]):
            with self.subTest(header=header), self.assertRaises(InputError):
                process_csv(self.fixture([], header=header))

    def test_empty_file(self):
        path = self.base / "empty.csv"
        path.write_text("", encoding="utf-8")
        with self.assertRaises(InputError):
            process_csv(path)

    def test_header_only_generates_empty_catalog(self):
        result = process_csv(self.fixture([]))
        self.assertEqual(result.summary["input_records"], 0)
        self.assertIn("В CSV есть только заголовок", render_catalog(result))

    def test_invalid_encoding_and_csv_syntax(self):
        path = self.base / "bad.csv"
        for content in (b"\xff\xfe", b'sku;name;category;price;stock\n"unterminated'):
            with self.subTest(content=content):
                path.write_bytes(content)
                with self.assertRaises(InputError):
                    process_csv(path)

    def test_report_csv_formula_values_are_literal(self):
        path = self.fixture([self.row(sku="=X", price="-100")])
        result = process_csv(path)
        write_outputs(result, path, self.base / "out")
        with (self.base / "out/issues.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter=";"))
        self.assertTrue(all(row["sku"].startswith("'=") for row in rows))
        self.assertEqual(next(row for row in rows if row["field"] == "price")["value"], "'-100")
        report = json.loads((self.base / "out/report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["issues"][0]["sku"], "=X")

    def test_source_cannot_be_overwritten(self):
        path = self.fixture([self.row()])
        source = self.base / "normalized.csv"
        source.write_bytes(path.read_bytes())
        original = source.read_bytes()
        with self.assertRaises(InputError):
            write_outputs(process_csv(source), source, self.base)
        self.assertEqual(source.read_bytes(), original)

    def test_cli_fatal_input_leaves_existing_output(self):
        out = self.base / "out"
        out.mkdir()
        marker = out / "index.html"
        marker.write_text("original", encoding="utf-8")
        bad = self.fixture([], header=["sku"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(bad), "--output", str(out)]), 2)
        self.assertEqual(marker.read_text(encoding="utf-8"), "original")

    def test_cli_missing_file(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(self.base / "missing.csv"), "--output", str(self.base / "out")]), 2)
        self.assertFalse((self.base / "out").exists())

    def test_demo_end_to_end_and_repeatability(self):
        source = ROOT / "data/products_demo.csv"
        original = source.read_bytes()
        out = self.base / "out"
        command = [sys.executable, str(ROOT / "catalog.py"), str(source), "--output", str(out)]
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["summary"], {"input_records": 20, "accepted_records": 12, "rejected_records": 8, "issue_count": 10})
        with (out / "normalized.csv").open(encoding="utf-8-sig", newline="") as handle:
            products = list(csv.DictReader(handle, delimiter=";"))
        self.assertEqual(len(products), 12)
        self.assertEqual(products[0]["price"], "1290.00")
        page = (out / "index.html").read_text(encoding="utf-8")
        self.assertEqual(page.count('<article class="card"'), 12)
        self.assertNotIn('data-sku="DUP-001"', page)
        self.assertIn("Нет в наличии", page)
        references = ResourceReferences()
        references.feed(page)
        self.assertTrue(all((out / name).is_file() for name in references.files))
        self.assertNotIn('assets/desk.svg', references.files)
        snapshot = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
        second = subprocess.run(command + ["--strict"], capture_output=True, text=True)
        self.assertEqual(second.returncode, 1)
        self.assertEqual(snapshot, {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()})
        self.assertEqual(source.read_bytes(), original)

    def test_normalized_csv_can_be_processed_again(self):
        result = process_csv(self.fixture([self.row()]))
        out = self.base / "out"
        write_outputs(result, self.base / "input.csv", out)
        again = process_csv(out / "normalized.csv")
        self.assertEqual(again.products, result.products)
        self.assertFalse(again.changes)

    def test_unicode_sku_does_not_expand_into_valid_identifier(self):
        for sku in ("ß-1", "ı-1", "ſ-1"):
            with self.subTest(sku=sku):
                result = process_csv(self.fixture([self.row(sku=sku), self.row(sku="SS-1")]))
                self.assertEqual([p.sku for p in result.products], ["SS-1"])
                self.assertEqual(result.issues[0].code, "sku_format")

    def test_hidden_characters_rejected_in_all_fields(self):
        for field in ("sku", "name", "category", "price", "stock", "description"):
            for char in ("\x00", "\x85", "\u200b", "\u202e"):
                with self.subTest(field=field, char=repr(char)):
                    result = process_csv(self.fixture([self.row(**{field: char + "=DEMO"})]))
                    self.assertFalse(result.products)
                    self.assertIn("hidden_character", [i.code for i in result.issues])

    def test_normalization_changes_preserve_exact_original(self):
        result = process_csv(self.fixture([self.row(sku=" a-1 ", name="Имя\n  товара", price="1 290,50", stock="002")]))
        changes = {c["field"]: (c["before"], c["after"]) for c in result.changes}
        self.assertEqual(changes, {"sku": (" a-1 ", "A-1"), "name": ("Имя\n  товара", "Имя товара"),
                                   "price": ("1 290,50", "1290.50"), "stock": ("002", "2")})

    def test_rejected_records_preserve_all_original_cells(self):
        rows = [self.row(sku="=X", price="нет") + ["extra; value"], self.row(sku="B", name="")]
        path = self.fixture(rows)
        result = process_csv(path)
        write_outputs(result, path, self.base / "out")
        report = json.loads((self.base / "out/report.json").read_text(encoding="utf-8"))
        self.assertEqual([r["values"] for r in report["rejected_records"]], rows)
        self.assertEqual([r["source_record"] for r in report["rejected_records"]], [2, 3])
        self.assertFalse(report["changes"])

    def test_blank_record_is_reported_not_silently_dropped(self):
        result = process_csv(self.fixture([self.row(), [], self.row(sku="B-1")]))
        self.assertEqual(result.summary["input_records"], 3)
        self.assertEqual(result.summary["accepted_records"], 2)
        self.assertEqual(result.rejected[0]["values"], [])
        self.assertIn("column_count", result.rejected[0]["codes"])

    def test_all_rejected_state_has_reason_and_no_category_controls(self):
        result = process_csv(self.fixture([self.row(price="-1")]))
        page = render_catalog(result)
        self.assertIn("Все записи отклонены", page)
        self.assertNotIn('<nav class="category-nav"', page)
        self.assertNotIn('<article class="card"', page)
        self.assertIn("invalid_price", [i.code for i in result.issues])

    def test_unknown_product_gets_explicit_placeholder(self):
        page = render_catalog(process_csv(self.fixture([self.row(sku="NEW-1", description="")])) )
        self.assertIn('src="assets/placeholder.svg"', page)
        self.assertIn("Нет изображения", page)
        self.assertIn("Описание не предоставлено", page)
        self.assertIn("Позиций: 1", page)

    def test_special_csv_characters_and_html_report_escaping(self):
        description = 'Кавычки "текст"; запятая, & знак'
        path = self.fixture([self.row(description=description), self.row(sku='<img onerror="x">')])
        result = process_csv(path)
        write_outputs(result, path, self.base / "out")
        again = process_csv(self.base / "out/normalized.csv")
        self.assertEqual(again.products[0].description, description)
        page = render_catalog(result)
        self.assertNotIn('<img onerror="x">', page)
        self.assertIn('&lt;img', page)

    def test_input_size_limit_is_explicit(self):
        path = self.fixture([self.row()])
        with patch("catalog.MAX_INPUT_BYTES", 20), self.assertRaisesRegex(InputError, "больше 5 МБ"):
            process_csv(path)

    def test_template_failure_does_not_overwrite_previous_output(self):
        path = self.fixture([self.row()])
        out = self.base / "out"
        out.mkdir()
        marker = out / "normalized.csv"
        marker.write_text("previous", encoding="utf-8")
        with patch("catalog.render_catalog", side_effect=KeyError("missing_placeholder")), self.assertRaises(InputError):
            write_outputs(process_csv(path), path, out)
        self.assertEqual(marker.read_text(encoding="utf-8"), "previous")

    def test_reordered_columns_keep_correct_field_mapping(self):
        path = self.fixture([["2", "123.45", "Бумага", "Блокнот", "A-1"]], header=["stock", "price", "category", "name", "sku"])
        result = process_csv(path)
        self.assertEqual((result.products[0].sku, result.products[0].stock), ("A-1", 2))

    def test_hardlink_to_source_is_refused_without_changes(self):
        source = self.fixture([self.row()])
        original = source.read_bytes()
        out = self.base / "out"
        out.mkdir()
        os.link(source, out / "normalized.csv")
        with self.assertRaises(InputError):
            write_outputs(process_csv(source), source, out)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual((out / "normalized.csv").read_bytes(), original)
        self.assertFalse((out / "index.html").exists())

    def test_hardlink_to_other_file_is_refused_without_changes(self):
        source = self.fixture([self.row()])
        other = self.base / "unrelated.txt"
        other.write_bytes(b"must survive")
        out = self.base / "out"
        out.mkdir()
        os.link(other, out / "issues.csv")
        with self.assertRaises(InputError):
            write_outputs(process_csv(source), source, out)
        self.assertEqual(other.read_bytes(), b"must survive")
        self.assertFalse((out / "normalized.csv").exists())

    def test_blocked_output_keeps_previous_bundle(self):
        source = self.fixture([self.row()])
        out = self.base / "out"
        out.mkdir()
        (out / "normalized.csv").write_bytes(b"old csv")
        (out / "index.html").write_bytes(b"old html")
        (out / "issues.csv").mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(source), "--output", str(out)]), 2)
        self.assertEqual((out / "normalized.csv").read_bytes(), b"old csv")
        self.assertEqual((out / "index.html").read_bytes(), b"old html")

    def test_commit_failure_restores_all_previous_files(self):
        source = self.fixture([self.row()])
        result = process_csv(source)
        out = self.base / "out"
        write_outputs(result, source, out)
        previous = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
        real_replace = os.replace

        def fail_report_once(src, dst):
            if Path(src).parent.name == "new" and Path(dst).name == "report.json":
                raise PermissionError("injected commit error")
            return real_replace(src, dst)

        source = self.fixture([self.row(name="Другое имя", price="200")])
        with patch("catalog.os.replace", side_effect=fail_report_once), self.assertRaises(OSError):
            write_outputs(process_csv(source), source, out)
        self.assertEqual(previous, {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()})
        self.assertFalse(list(out.glob(".productflow-*")))

    def test_failed_new_bundle_removes_committed_files(self):
        source = self.fixture([self.row()])
        out = self.base / "out"
        real_replace = os.replace

        def fail_index(src, dst):
            if Path(src).parent.name == "new" and Path(dst).name == "index.html":
                raise OSError("injected disk error")
            return real_replace(src, dst)

        with patch("catalog.os.replace", side_effect=fail_index), self.assertRaises(OSError):
            write_outputs(process_csv(source), source, out)
        self.assertEqual(list(out.iterdir()), [])

    def test_staging_failure_does_not_touch_previous_files(self):
        source = self.fixture([self.row()])
        out = self.base / "out"
        out.mkdir()
        (out / "index.html").write_bytes(b"old")
        real_write = Path.write_bytes

        def fail_stage(path, data):
            if path.parent.name == "new" and path.name == "changes.csv":
                raise OSError("injected staging error")
            return real_write(path, data)

        with patch("catalog.Path.write_bytes", new=fail_stage), self.assertRaises(OSError):
            write_outputs(process_csv(source), source, out)
        self.assertEqual((out / "index.html").read_bytes(), b"old")
        self.assertEqual([p.name for p in out.iterdir()], ["index.html"])

    def test_rollback_failure_preserves_recovery_backup(self):
        source = self.fixture([self.row()])
        out = self.base / "out"
        out.mkdir()
        (out / "normalized.csv").write_bytes(b"old csv")
        real_replace = os.replace

        def fail_commit_and_restore(src, dst):
            if Path(dst).name == "issues.csv" or Path(src).parent.name == "old":
                raise OSError("injected rollback error")
            return real_replace(src, dst)

        with patch("catalog.os.replace", side_effect=fail_commit_and_restore), self.assertRaisesRegex(InputError, "Резервные файлы"):
            write_outputs(process_csv(source), source, out)
        backups = list(out.glob(".productflow-*/old/normalized.csv"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"old csv")

    def test_missing_referenced_asset_keeps_previous_output(self):
        source = self.fixture([self.row(sku="DS-001")])
        clone = self.base / "clone"
        shutil.copytree(ROOT / "templates", clone / "templates")
        shutil.copytree(ROOT / "assets", clone / "assets")
        (clone / "assets/ds-001.svg").unlink()
        out = self.base / "out"
        out.mkdir()
        (out / "index.html").write_bytes(b"old html")
        with patch("catalog.ROOT", clone), self.assertRaisesRegex(InputError, "ds-001.svg"):
            write_outputs(process_csv(source), source, out)
        self.assertEqual((out / "index.html").read_bytes(), b"old html")
        self.assertEqual([p.name for p in out.iterdir()], ["index.html"])

    def test_redirected_asset_directory_is_refused(self):
        source = self.fixture([self.row()])
        out = self.base / "out"
        outside = self.base / "outside"
        outside.mkdir()
        out.mkdir()
        redirected = out / "assets"
        real_resolve = Path.resolve

        def redirected_resolve(path, *args, **kwargs):
            if path == redirected or redirected in path.parents:
                return outside / path.relative_to(redirected)
            return real_resolve(path, *args, **kwargs)

        with patch("catalog.Path.resolve", new=redirected_resolve), self.assertRaises(InputError):
            write_outputs(process_csv(source), source, out)
        self.assertEqual(list(out.iterdir()), [])
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

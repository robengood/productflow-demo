"""CSV validation and static catalogue generation. Python 3.10+, stdlib only."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
import shutil
import tempfile
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from html.parser import HTMLParser
from string import Template

ROOT = Path(__file__).resolve().parent
REQUIRED = ("sku", "name", "category", "price", "stock")
FIELDS = (*REQUIRED, "description")
CATEGORIES = {"Организация": "organization", "Бумага": "paper", "Аксессуары": "accessories"}
LIMITS = {"sku": 32, "name": 100, "category": 40, "description": 500}
MAX_INPUT_BYTES = 5 * 1024 * 1024
DEMO_IMAGES = {f"{prefix}-{i:03d}" for prefix in ("DS", "PP", "AC") for i in range(1, 5)}


class InputError(ValueError):
    """Rejected input, template or output operation; show a clear CLI error."""


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    category: str
    price: str
    stock: int
    description: str
    source_record: int


@dataclass(frozen=True)
class Issue:
    source_record: int
    source_line_end: int
    sku: str
    field: str
    code: str
    value: str
    message: str


@dataclass
class Result:
    products: list[Product]
    issues: list[Issue]
    total: int
    source_sha256: str
    changes: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    source_columns: list[str] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        return {"input_records": self.total, "accepted_records": len(self.products),
                "rejected_records": self.total - len(self.products), "issue_count": len(self.issues)}


def clean_text(value: str) -> str:
    """Trim and collapse whitespace without changing spelling or punctuation."""
    return " ".join(value.split())


def normalize_sku(value: str) -> str:
    text = clean_text(value)
    # Unicode case expansion must never turn an invalid identifier into ASCII.
    return text.upper() if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", text) else text


def has_hidden_characters(value: str) -> bool:
    return any(unicodedata.category(c) in ("Cc", "Cf") and c not in "\t\r\n" for c in value)


def parse_price(value: str) -> str:
    """Decimal rubles, max two decimals; spaces may group thousands exactly."""
    value = value.strip().replace("\u00a0", " ").replace("\u202f", " ")
    if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?: [0-9]{3})+)(?:[.,][0-9]{1,2})?", value):
        raise ValueError("Нужна положительная цена, например 1290.00 или 1 290,00; до 2 знаков после запятой.")
    amount = Decimal(value.replace(" ", "").replace(",", "."))
    if not Decimal("0") < amount <= Decimal("1000000"):
        raise ValueError("Цена должна быть больше 0 и не больше 1 000 000 рублей.")
    return f"{amount:.2f}"


def parse_stock(value: str) -> int:
    value = value.strip()
    if not re.fullmatch(r"[0-9]{1,6}", value):
        raise ValueError("Остаток должен быть целым числом от 0 до 999999.")
    return int(value)


def process_csv(path: Path, delimiter: str = ";") -> Result:
    """Validate all rows and all occurrences of duplicate normalized SKUs."""
    if delimiter not in (";", ","):
        raise InputError("Поддерживаются разделители ; и ,.")
    raw_rows = []
    try:
        with path.open("rb") as source:
            source_bytes = source.read(MAX_INPUT_BYTES + 1)
        if len(source_bytes) > MAX_INPUT_BYTES:
            raise InputError("Файл больше 5 МБ. Этот демонстрационный обработчик рассчитан на небольшие каталоги.")
        with io.StringIO(source_bytes.decode("utf-8-sig"), newline="") as handle:
            reader = csv.reader(handle, delimiter=delimiter, strict=True)
            header = next(reader, None)
            if header is None:
                raise InputError("CSV пуст: нет строки заголовка.")
            if len(header) != len(set(header)):
                raise InputError("В заголовке повторяются имена столбцов.")
            missing = set(REQUIRED) - set(header)
            extra = set(header) - set(FIELDS)
            if missing or extra:
                raise InputError(f"Неверный заголовок. Отсутствуют: {sorted(missing)}; неизвестные: {sorted(extra)}.")
            for record, values in enumerate(reader, start=2):
                raw_rows.append((record, reader.line_num, values))
    except (UnicodeError, csv.Error) as exc:
        raise InputError(f"Не удалось прочитать UTF-8 CSV: {exc}") from exc

    sku_index = header.index("sku")
    # Even a structurally invalid row can conflict with a valid SKU.
    sku_counts = Counter(normalize_sku(values[sku_index])
                         for _, _, values in raw_rows if len(values) > sku_index and clean_text(values[sku_index]))
    products, issues, changes, rejected = [], [], [], []
    for record, line_end, values in raw_rows:
        raw = dict(zip(header, values))
        sku = normalize_sku(raw.get("sku", ""))

        def issue(field: str, code: str, message: str, value: str | None = None) -> None:
            issues.append(Issue(record, line_end, sku, field, code,
                                raw.get(field, "") if value is None else value, message))

        before = len(issues)
        if len(values) != len(header):
            issue("row", "column_count", f"Ожидалось {len(header)} столбцов, получено {len(values)}.", " | ".join(values))
        text = {field: clean_text(raw.get(field, "")) for field in FIELDS}
        text["sku"] = sku
        for name, value in raw.items():
            if has_hidden_characters(value):
                issue(name, "hidden_character", "В поле есть невидимый управляющий символ. Удалите его в исходных данных.")
        for field in REQUIRED:
            if not text[field]:
                issue(field, "required", "Обязательное поле не заполнено.")
        for field, limit in LIMITS.items():
            if len(text[field]) > limit:
                issue(field, "too_long", f"Допускается не больше {limit} символов.")
        # Prevent formula-like text in exported CSV. HTML is escaped separately.
        for field in ("name", "description"):
            if text[field].startswith(("=", "+", "-", "@")):
                issue(field, "formula_text", "Текст начинается со знака формулы. Требуется ручная проверка.")
        if sku and not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,31}", sku):
            issue("sku", "sku_format", "Артикул: 1-32 символа A-Z, 0-9, _ или -; первый символ буква или цифра.")
        if sku and sku_counts[sku] > 1:
            issue("sku", "duplicate_sku", "Артикул повторяется. Все его записи исключены до уточнения.")
        category_lookup = {key.casefold(): key for key in CATEGORIES}
        category = category_lookup.get(text["category"].casefold())
        if text["category"] and category is None:
            issue("category", "unknown_category", "Категория должна быть одной из: " + ", ".join(CATEGORIES) + ".")
        price, stock = "", 0
        if text["price"]:
            try:
                price = parse_price(raw["price"])
            except ValueError as exc:
                issue("price", "invalid_price", str(exc))
        if text["stock"]:
            try:
                stock = parse_stock(raw["stock"])
            except ValueError as exc:
                issue("stock", "invalid_stock", str(exc))
        if len(issues) == before:
            product = Product(sku, text["name"], category, price, stock, text["description"], record)
            products.append(product)
            normalized = asdict(product)
            for name in FIELDS:
                original, updated = raw.get(name, ""), str(normalized[name])
                if original != updated:
                    changes.append({"source_record": record, "sku": sku, "field": name,
                                    "before": original, "after": updated})
        else:
            rejected.append({"source_record": record, "source_line_end": line_end,
                             "values": values, "codes": [i.code for i in issues[before:]]})
    return Result(products, issues, len(raw_rows), hashlib.sha256(source_bytes).hexdigest(), changes, rejected, header)


def money(value: str) -> str:
    return f"{Decimal(value):,.2f}".replace(",", "\u00a0").replace(".", ",")


def render_catalog(result: Result) -> str:
    esc = html.escape
    sections = []
    for category, slug in CATEGORIES.items():
        group = [p for p in result.products if p.category == category]
        if not group:
            continue
        cards = []
        for p in group:
            availability = f"В наличии: {p.stock} шт." if p.stock else "Нет в наличии"
            image = p.sku.lower() if p.sku in DEMO_IMAGES else "placeholder"
            image_note = "Иллюстрация" if p.sku in DEMO_IMAGES else "Нет изображения"
            description = f'<p class="description">{esc(p.description)}</p>' if p.description else '<p class="description missing">Описание не предоставлено</p>'
            cards.append(f'''<article class="card" data-sku="{esc(p.sku)}">
<div class="visual {slug}"><img src="assets/{image}.svg" alt="" width="480" height="300"><span class="image-note">{image_note}</span></div>
<div class="card-body"><span class="sku">{esc(p.sku)}</span><h3>{esc(p.name)}</h3>{description}
<div class="card-bottom"><strong class="price">{money(p.price)} <span>₽</span></strong><span class="stock {'empty' if not p.stock else ''}">{availability}</span></div></div></article>''')
        sections.append(f'<section class="category-section" id="{slug}" aria-labelledby="title-{slug}"><div class="section-heading"><h2 id="title-{slug}">{esc(category)}</h2><span>Позиций: {len(group)}</span></div><div class="grid">{"".join(cards)}</div></section>')
    nav = ''.join(f'<a href="#{slug}">{html.escape(category)} <span aria-hidden="true">↓</span></a>' for category, slug in CATEGORIES.items()
                  if any(p.category == category for p in result.products))
    nav = f'<nav class="category-nav" aria-label="Перейти к категории"><span>Перейти к категории</span>{nav}</nav>' if nav else ''
    empty_reason = "Все записи отклонены. Посмотрите причины в отчёте, исправьте CSV и повторите сборку." if result.total else "В CSV есть только заголовок. Добавьте товары и повторите сборку."
    content = ''.join(sections) or f'<div class="empty-state"><h2>Пока нет товаров</h2><p>{empty_reason}</p><a class="text-link" href="#data-report">Открыть результаты обработки ↓</a></div>'
    status = "Есть записи для исправления" if result.issues else ("Все записи прошли проверку" if result.total else "Входная таблица пуста")
    issue_rows = ''.join(f'<tr><td>{i.source_record}</td><td><code>{esc(i.sku or "—")}</code></td><td><code>{esc(i.field)}</code></td><td>{esc(i.message)}</td></tr>' for i in result.issues)
    details = f'<details class="issue-details"><summary>Посмотреть проблемы в данных ({len(result.issues)})</summary><div class="table-scroll" tabindex="0" role="region" aria-label="Таблица ошибок"><table><caption>Ошибки исходных записей. Заголовок CSV считается записью 1.</caption><thead><tr><th scope="col">Запись</th><th scope="col">Артикул</th><th scope="col">Поле</th><th scope="col">Что исправить</th></tr></thead><tbody>{issue_rows}</tbody></table></div></details>' if issue_rows else '<p class="no-issues">Ошибок в записях нет.</p>'
    return Template((ROOT / "templates/catalog.html").read_text(encoding="utf-8")).substitute(
        cards=content, navigation=nav, status=status, issue_details=details,
        change_count=len(result.changes), **result.summary)


def csv_safe(value: object) -> str:
    """Report may contain rejected formulas: export them as literal text."""
    text = ''.join(f"\\u{ord(c):04x}" if unicodedata.category(c) in ("Cc", "Cf") and c not in "\t\r\n" else c for c in str(value))
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


class ResourceReferences(HTMLParser):
    """Local files actually referenced by the generated catalogue."""
    def __init__(self):
        super().__init__()
        self.files: set[str] = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("src", "href") and value and not value.startswith("#"):
                self.files.add(value)


def check_output_targets(source: Path, destination: Path, names: list[Path]) -> None:
    """Reject aliases and redirected child paths before touching any output."""
    for relative in names:
        target = destination / relative
        if target.resolve() == source.resolve() or (target.is_file() and source.samefile(target)):
            raise InputError("Входной файл совпадает с выходным. Выберите отдельную папку --output.")
        for path in (target, *target.parents):
            if path == destination:
                break
            if path.is_symlink() or not path.resolve().is_relative_to(destination):
                raise InputError(f"Выходной путь перенаправлен файловой ссылкой: {relative}.")
        if target.exists():
            if not target.is_file():
                raise InputError(f"Вместо выходного файла существует каталог: {relative}.")
            if target.stat().st_nlink > 1:
                raise InputError(f"Выходной файл имеет жёсткие ссылки: {relative}. Выберите другую папку.")
        if any(p.exists() and not p.is_dir() for p in target.parents if p.is_relative_to(destination)):
            raise InputError(f"Родитель выходного файла не является каталогом: {relative}.")


def commit_outputs(source: Path, destination: Path, files: dict[Path, bytes]) -> None:
    """Stage the whole bundle, replace each file, restore on ordinary I/O failure.

    This is not a transaction across process crashes or concurrent writers.
    A failed rollback preserves its backup directory for manual recovery.
    """
    check_output_targets(source, destination, list(files))
    destination.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".productflow-", dir=destination))
    committed, created_dirs = [], []
    keep_backup = False
    try:
        for relative, content in files.items():
            staged = staging / "new" / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(content)
            target = destination / relative
            if target.exists():
                backup = staging / "old" / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(target.read_bytes())
        check_output_targets(source, destination, list(files))
        for relative in files:
            target = destination / relative
            if not target.parent.exists():
                target.parent.mkdir(parents=True)
                created_dirs.append(target.parent)
            os.replace(staging / "new" / relative, target)
            committed.append(relative)
    except OSError as exc:
        restoration_errors = []
        for relative in reversed(committed):
            try:
                backup = staging / "old" / relative
                if backup.exists():
                    os.replace(backup, destination / relative)
                else:
                    (destination / relative).unlink()
            except OSError as restore_exc:
                restoration_errors.append(str(restore_exc))
        if restoration_errors:
            keep_backup = True
            raise InputError(f"Ошибка записи и отката. Резервные файлы сохранены в {staging}; "
                             "не используйте неполный результат.") from exc
        for directory in reversed(created_dirs):
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
        raise
    finally:
        if not keep_backup:
            shutil.rmtree(staging)


def write_outputs(result: Result, source: Path, destination: Path) -> None:
    destination = destination.resolve()
    if destination / "assets" in source.resolve().parents:
        raise InputError("Входной файл совпадает с выходным. Выберите отдельную папку --output.")
    # Prepare templates before overwriting any previous result.
    try:
        page = render_catalog(result)
        stylesheet = (ROOT / "templates/style.css").read_text(encoding="utf-8")
        references = ResourceReferences()
        references.feed(page)
        assets = {}
        for name in sorted(references.files | {"assets/placeholder.svg"}):
            relative = Path(name)
            if relative.parts and relative.parts[0] == "assets" and ".." not in relative.parts:
                asset = ROOT / relative
                if not asset.is_file():
                    raise InputError(f"Не найден ресурс страницы: {name}.")
                assets[relative] = asset.read_bytes()
            elif name not in ("style.css", "normalized.csv", "issues.csv", "changes.csv", "report.json"):
                raise InputError(f"Не поддерживается ссылка на ресурс страницы: {name}.")
    except (KeyError, ValueError) as exc:
        raise InputError(f"Ошибка шаблона страницы: {exc}") from exc
    files = {}
    with io.StringIO(newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter=";")
        writer.writeheader()
        writer.writerows({name: getattr(p, name) for name in FIELDS} for p in result.products)
        files[Path("normalized.csv")] = handle.getvalue().encode("utf-8-sig")
    with io.StringIO(newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=Issue.__dataclass_fields__, delimiter=";")
        writer.writeheader()
        writer.writerows({key: csv_safe(value) for key, value in asdict(i).items()} for i in result.issues)
        files[Path("issues.csv")] = handle.getvalue().encode("utf-8-sig")
    with io.StringIO(newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("source_record", "sku", "field", "before", "after"), delimiter=";")
        writer.writeheader()
        writer.writerows({key: csv_safe(value) for key, value in change.items()} for change in result.changes)
        files[Path("changes.csv")] = handle.getvalue().encode("utf-8-sig")
    report = {"project_type": "synthetic educational demo", "source_file": source.name,
              "source_sha256": result.source_sha256, "summary": result.summary,
              "issues": [asdict(i) for i in result.issues], "changes": result.changes,
              "source_columns": result.source_columns, "rejected_records": result.rejected,
              "accepted_records": [{"sku": p.sku, "source_record": p.source_record} for p in result.products]}
    files.update({Path("report.json"): (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                  Path("index.html"): page.encode("utf-8"), Path("style.css"): stylesheet.encode("utf-8"), **assets})
    commit_outputs(source, destination, files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка CSV и сборка демонстрационного каталога.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    parser.add_argument("--delimiter", choices=(";", ","), default=";")
    parser.add_argument("--strict", action="store_true", help="Код 1 при ошибках строк; отчёты сохраняются.")
    args = parser.parse_args(argv)
    try:
        result = process_csv(args.input, args.delimiter)
        write_outputs(result, args.input, args.output)
    except (InputError, OSError) as exc:
        print(f"Ошибка: {exc}")
        return 2
    print(json.dumps(result.summary, ensure_ascii=False))
    print(f"Каталог и отчёты: {args.output.resolve()}")
    return 1 if args.strict and result.issues else 0


if __name__ == "__main__":
    raise SystemExit(main())

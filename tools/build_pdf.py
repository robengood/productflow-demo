"""Three-page editorial case: claims come from current local evidence."""
import hashlib
import html
import json
import os
from datetime import datetime
from pathlib import Path
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/pdf/portfolio_case.pdf"
W, H = 1200, 1080
INK, MUTED, GREEN, PAPER, LINE, RUST = map(HexColor, ("#223d34", "#59675f", "#254d3f", "#f6f6f2", "#d9dfd5", "#875037"))

def fonts():
    pairs = [(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/arial.ttf",
              Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/arialbd.ttf"),
             (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
              Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))]
    for regular, bold in pairs:
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("Case", str(regular)))
            pdfmetrics.registerFont(TTFont("CaseBold", str(bold)))
            pdfmetrics.registerFontFamily("Case", normal="Case", bold="CaseBold")
            return
    raise SystemExit("Arial or DejaVu Sans with Cyrillic support is required.")

def para(c, text, x, top, width, size=18, color=MUTED, bold=False, max_height=None):
    p = Paragraph(text, ParagraphStyle("text", fontName="CaseBold" if bold else "Case",
                  fontSize=size, leading=size*1.3, textColor=color))
    _, height = p.wrap(width, H)
    if max_height is not None and height > max_height:
        raise ValueError(f"Text overflow ({height} > {max_height}): {text[:80]}")
    if top-height < 46:
        raise ValueError(f"Text below content area: {text[:80]}")
    p.drawOn(c, x, top-height)
    return height

def label(c, text, x, y, color=MUTED, size=12):
    c.setFont("CaseBold", size)
    c.setFillColor(color)
    c.drawString(x, y, text)

def rule(c, x1, y, x2):
    c.setStrokeColor(LINE)
    c.setLineWidth(1)
    c.line(x1, y, x2, y)

def base(c, n, section):
    c.setFillColor(PAPER)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    label(c, "PRODUCTFLOW / ПОРТФОЛИО", 48, H-32)
    c.setFont("Case", 12)
    c.drawRightString(W-48, H-32, section)
    rule(c, 48, 40, W-48)
    label(c, "ProductFlow", 48, 21, size=11)
    c.drawRightString(W-48, 21, f"{n:02d} / 03")

def shot(c, name, x, y, width):
    image = ImageReader(str(ROOT / "evidence/screenshots" / name))
    iw, ih = image.getSize()
    height = width*ih/iw
    c.drawImage(image, x, y, width, height)
    c.setStrokeColor(LINE)
    c.rect(x, y, width, height, stroke=1, fill=0)
    return height

def main():
    fonts()
    verification = json.loads((ROOT/"evidence/verification.json").read_text(encoding="utf-8"))
    browser = json.loads((ROOT/"evidence/browser-checks.json").read_text(encoding="utf-8"))
    report = json.loads((ROOT/"output/report.json").read_text(encoding="utf-8"))
    if not all(verification["checks"].values()) or verification["test_exit_code"]:
        raise SystemExit("Run successful checks first.")
    if verification["summary"] != report["summary"] or report["source_sha256"] != hashlib.sha256((ROOT/"data/products_demo.csv").read_bytes()).hexdigest():
        raise SystemExit("Evidence does not match the input and output.")
    if not all(d["matchesOutput"] for d in browser["downloads"]):
        raise SystemExit("Browser download checks did not pass.")
    for name, digest in browser["artifact_sha256"].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != digest:
            raise SystemExit(f"Browser evidence is stale: {name}")
    summary = report["summary"]
    date = datetime.fromisoformat(verification["checked_at_moscow"]).strftime("%d.%m.%Y")
    changes = [v for v in report["changes"] if v["source_record"] == 2]
    raw = {r["source_record"]: dict(zip(report["source_columns"], r["values"])) for r in report["rejected_records"]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    c = Canvas(str(OUT), pagesize=(W,H), pageCompression=1)
    c.setTitle("ProductFlow - подготовка товарного каталога")
    c.setAuthor("Учебный проект, подготовленный с использованием AI")
    c.setSubject("Проверка CSV, история изменений, каталог и фактический QA")

    base(c, 1, "ПРОЕКТ И РЕЗУЛЬТАТ")
    para(c, "ProductFlow", 48, 1022, 530, 53, INK, True)
    para(c, "Подготовка товарного каталога", 48, 953, 540, 24, INK)
    para(c, "Инструмент проверяет товарную таблицу, отделяет ошибки и создаёт данные для выгрузки и страницу каталога.", 641, 1010, 511, 21, max_height=91)
    para(c, "Учебный проект · синтетические данные · с помощью AI", 48, 912, 1104, 15, GREEN)
    label(c, f'{summary["input_records"]} записей  →  {summary["accepted_records"]} товаров  +  {summary["rejected_records"]} отклонённых записей', 48, 870, INK, 18)
    label(c, "Python / HTML / CSS", 947, 870, size=13)
    shot(c, "desktop-top.jpg", 48, 76, 1104)
    label(c, "Реальный скриншот каталога. Обработка CSV выполняется локально, до открытия страницы.", 48, 54, size=11)
    c.showPage()

    base(c, 2, "ДАННЫЕ И ПРОСЛЕЖИВАЕМОСТЬ")
    para(c, "Что произошло с исходной таблицей", 48, 1019, 1104, 39, INK, True)
    para(c, "Один принятый товар и реальные причины отклонения записей из демонстрационного CSV.", 48, 955, 1104, 19)
    label(c, "CSV", 48, 892, INK, 19)
    label(c, "→  Проверка", 196, 892, INK, 19)
    label(c, "→  Нормализация", 447, 892, INK, 19)
    label(c, "→  Выгрузки + HTML", 804, 892, INK, 19)
    rule(c, 48, 871, 1152)
    label(c, "ЗАПИСЬ 2 / DS-001", 48, 842)
    label(c, "РЕЗУЛЬТАТ В КАТАЛОГЕ", 792, 842)
    para(c, "Поле", 48, 813, 125, 16, MUTED, True)
    para(c, "Входной CSV", 187, 813, 286, 16, MUTED, True)
    para(c, "Нормализовано", 490, 813, 266, 16, GREEN, True)
    names = {"sku":"Артикул","name":"Название","category":"Категория","price":"Цена"}
    for i, change in enumerate(changes):
        y=765-i*68
        para(c, names[change["field"]], 48, y, 120, 17, INK)
        # Visible middle dots reveal exact spaces without ambiguous typography.
        before=html.escape(change["before"].replace(" ","·"))
        para(c, before, 187, y, 280, 18, MUTED, max_height=49)
        para(c, html.escape(change["after"]), 490, y, 266, 18, GREEN, True, max_height=49)
        rule(c,48,y-49,756)
    para(c, "Точка · обозначает пробел. Программа меняет формат, сохраняя точные значения до и после в журнале.", 48, 478, 698, 16, max_height=48)
    shot(c, "card-detail.jpg", 792, 382, 360)
    para(c, f'Всего зафиксировано {len(report["changes"])} изменений полей.<br/>Источник: output/changes.csv', 792, 363, 360, 15, max_height=62)
    para(c, "Ошибочные записи: решение не подменяется догадкой", 48, 373, 714, 22, INK, True, max_height=62)
    headers=[(48,"Запись / SKU"),(338,"Исходное значение"),(626,"Решение программы")]
    for x,t in headers: label(c,t,x,286,size=14)
    samples=[
      ("14 и 15 / DUP-001", raw[14]["sku"] + " / " + raw[15]["sku"].replace(" ", "·"), "Обе записи исключены: артикул повторяется."),
      ("17 / BAD-002", "Цена: " + raw[17]["price"], "Запись исключена: цена должна быть положительной."),
      ("18 / BAD-003", "Остаток: " + raw[18]["stock"], "Запись исключена: требуется целое число."),
    ]
    # Validate every example against the actual report.
    for record,code in [(14,"duplicate_sku"),(15,"duplicate_sku"),(17,"invalid_price"),(18,"invalid_stock")]:
        assert any(i["source_record"]==record and i["code"]==code for i in report["issues"])
    for i,(a,b,d) in enumerate(samples):
        top=263-i*58
        rule(c,48,top+9,1152)
        para(c,a,48,top,270,17,INK)
        para(c,b,338,top,265,17,RUST)
        para(c,d,626,top,526,17,INK,max_height=46)
    para(c, "Отклонённые записи сохранены целиком в report.json. Ошибок в полях: "+str(summary["issue_count"])+".<br/>Номер записи учитывает заголовок CSV как запись 1.",48,85,1104,13,max_height=36)
    c.showPage()

    base(c, 3, "ИНТЕРФЕЙС И ПРОВЕРКА")
    para(c, "Каталог и проверяемый результат", 48, 1019, 1104, 39, INK, True)
    para(c, "Четыре колонки на широком экране, одна на телефоне. Цена, остаток и описание видны в карточке.",48,955,1104,19)
    label(c,"DESKTOP / 1440 × 1000 / ФРАГМЕНТ КАТАЛОГА",48,895)
    label(c,"MOBILE / 375 × 844",840,895)
    shot(c,"desktop-row.jpg",48,584,740)
    shot(c,"mobile-catalog.jpg",840,178,310)
    para(c,str(verification["test_count"]),48,562,110,70,GREEN,True)
    para(c,"автоматических тестов прошли",172,544,600,25,INK,True)
    para(c,f'Python {verification["python"]} · Windows 11 · {date}',172,505,600,15)
    rule(c,48,461,788)
    para(c,"Данные",48,440,160,18,INK,True)
    para(c,"Поля, дубликаты, числа, кодировка, скрытые символы, безопасный HTML и повторный импорт выгрузки.",224,440,558,17,max_height=68)
    rule(c,48,365,788)
    para(c,"Браузер",48,344,160,18,INK,True)
    para(c,"320 / 375 / 768 / 1024 / 1440 px без переполнения. Проверены переходы, таблица ошибок, 4 скачивания и 2 пустых состояния.",224,344,558,17,max_height=70)
    rule(c,48,264,788)
    para(c,"Технологии",48,245,160,18,INK,True)
    para(c,"Python и unittest; HTML, CSS и SVG.<br/>Без сторонних зависимостей в приложении.",224,245,558,17,max_height=46)
    para(c,"Ограничения",48,174,160,18,INK,True)
    para(c,"CSV обрабатывается в терминале. Нет корзины и загрузки через браузер. Импорт в CMS и нагрузка не проверялись.",224,174,558,17,max_height=69)
    rule(c,48,96,1152)
    para(c,"Исходники и доказательства: архив проекта, README.md, docs/QA_REPORT.md, evidence/.<br/>Публичный репозиторий и онлайн-демо пока не созданы.",48,82,1104,13,max_height=36)
    c.showPage()
    c.save()
    print(OUT)

if __name__ == "__main__":
    main()


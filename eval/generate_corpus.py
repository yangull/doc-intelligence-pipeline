"""Generate the synthetic eval corpus.

Output is deterministic: same bytes on every run. The generated PDFs under
eval/corpus/ are committed, and eval/dataset.json asserts against the exact
values written here — change a number in this file and the dataset must change
with it.

    uv run python -m eval.generate_corpus
"""

import io
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

CORPUS_DIR = Path(__file__).parent / "corpus"
PAGE_WIDTH, PAGE_HEIGHT = A4

LEFT = 25 * mm
TOP = PAGE_HEIGHT - 30 * mm


class Writer:
    def __init__(self, pdf: canvas.Canvas):
        self.pdf = pdf
        self.y = TOP

    def line(self, text="", size=10, bold=False, gap=6 * mm, x=LEFT):
        self.pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        self.pdf.drawString(x, self.y, text)
        self.y -= gap

    def row(self, left, right, size=10, bold=False):
        self.pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        self.pdf.drawString(LEFT, self.y, left)
        self.pdf.drawRightString(PAGE_WIDTH - 25 * mm, self.y, right)
        self.y -= 6 * mm

    def rule(self):
        self.pdf.line(LEFT, self.y + 2 * mm, PAGE_WIDTH - 25 * mm, self.y + 2 * mm)
        self.y -= 3 * mm

    def paragraph(self, text, size=10, width=95):
        words = text.split()
        current = ""
        for word in words:
            if len(current) + len(word) + 1 > width:
                self.line(current, size=size, gap=5 * mm)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            self.line(current, size=size, gap=5 * mm)


# Filenames written by the current builders. Lets main() spot corpus files left behind
# by a builder that was removed, and lets the tests derive the expected set instead of
# hardcoding a count that has to be edited in lockstep.
WRITTEN: set[str] = set()


def new_pdf(filename: str) -> canvas.Canvas:
    WRITTEN.add(filename)
    # invariant=1 fixes the creation timestamp and document ID, so regenerating
    # the corpus produces byte-identical files
    pdf = canvas.Canvas(str(CORPUS_DIR / filename), pagesize=A4, invariant=1)
    pdf.setTitle(filename)
    pdf.setAuthor("doc-intelligence-pipeline eval corpus")
    pdf.setProducer("eval/generate_corpus.py")
    return pdf


def invoice(filename, vendor, address, number, date, due, items, vat_rate, currency):
    pdf = new_pdf(filename)
    w = Writer(pdf)
    w.line(vendor, size=16, bold=True, gap=7 * mm)
    w.line(address, size=9, gap=10 * mm)

    w.line("INVOICE", size=13, bold=True, gap=8 * mm)
    w.line(f"Invoice number: {number}")
    w.line(f"Invoice date: {date}")
    w.line(f"Payment due: {due}", gap=10 * mm)

    w.row("Description", f"Amount ({currency})", bold=True)
    w.rule()
    subtotal = 0.0
    for description, amount in items:
        w.row(description, f"{amount:,.2f}")
        subtotal += amount
    w.rule()

    vat = round(subtotal * vat_rate, 2)
    total = round(subtotal + vat, 2)
    w.row("Subtotal", f"{subtotal:,.2f}")
    w.row(f"VAT ({int(vat_rate * 100)}%)", f"{vat:,.2f}")
    w.row("Total due", f"{total:,.2f} {currency}", bold=True)

    w.y -= 8 * mm
    w.line(f"Please remit {total:,.2f} {currency} by {due}.", size=9)
    pdf.save()
    return total


def build_invoice_nordwind_2401():
    invoice(
        "invoice-nordwind-2401.pdf",
        vendor="Nordwind Logistik GmbH",
        address="Hafenstrasse 14, 20457 Hamburg, Germany",
        number="NW-2401",
        date="2024-01-15",
        due="2024-02-14",
        items=[
            ("Freight forwarding, Hamburg to Rotterdam", 620.00),
            ("Customs clearance handling", 245.50),
            ("Warehouse storage, 12 days", 176.52),
        ],
        vat_rate=0.19,
        currency="EUR",
    )


def build_invoice_kranich_2402():
    invoice(
        "invoice-kranich-2402.pdf",
        vendor="Kranich Software Ltd",
        address="42 Fenchurch Street, London EC3M 4AB, United Kingdom",
        number="KR-2402",
        date="2024-02-03",
        due="2024-03-04",
        items=[
            ("Platform licence, annual", 2100.00),
            ("Onboarding and configuration", 575.00),
            ("Priority support add-on", 200.00),
        ],
        vat_rate=0.20,
        currency="GBP",
    )


def build_invoice_nordwind_2403():
    invoice(
        "invoice-nordwind-2403.pdf",
        vendor="Nordwind Logistik GmbH",
        address="Hafenstrasse 14, 20457 Hamburg, Germany",
        number="NW-2403",
        date="2024-03-22",
        due="2024-04-21",
        items=[
            ("Freight forwarding, Hamburg to Antwerp", 540.00),
            ("Pallet handling surcharge", 207.90),
        ],
        vat_rate=0.19,
        currency="EUR",
    )


def build_contract_consulting():
    pdf = new_pdf("contract-consulting-services.pdf")
    w = Writer(pdf)
    w.line("CONSULTING SERVICES AGREEMENT", size=14, bold=True, gap=10 * mm)
    w.paragraph(
        "This Consulting Services Agreement is entered into between Meridian Analytics GmbH, "
        "Chausseestrasse 88, 10115 Berlin (the Client), and Ostwald Consulting UG, "
        "Lindenallee 3, 20144 Hamburg (the Consultant)."
    )
    w.y -= 4 * mm

    w.line("1. Term", bold=True, gap=6 * mm)
    w.paragraph(
        "This Agreement takes effect on 1 April 2024 and remains in force for an initial term "
        "of twelve (12) months. It renews automatically for successive twelve-month terms "
        "unless terminated in accordance with clause 4."
    )
    w.y -= 3 * mm

    w.line("2. Scope of services", bold=True, gap=6 * mm)
    w.paragraph(
        "The Consultant shall provide data engineering advisory services, including pipeline "
        "architecture review, vendor assessment, and quarterly capacity planning."
    )
    w.y -= 3 * mm

    w.line("3. Fees", bold=True, gap=6 * mm)
    w.paragraph(
        "The Client shall pay a day rate of EUR 950 per consultant day, exclusive of VAT. "
        "Total fees payable under the initial term shall not exceed EUR 76,000. Invoices are "
        "issued monthly and are payable within 30 days of receipt."
    )
    w.y -= 3 * mm

    w.line("4. Termination", bold=True, gap=6 * mm)
    w.paragraph(
        "Either party may terminate this Agreement by giving 60 days written notice. The "
        "Client may terminate immediately if the Consultant commits a material breach that "
        "remains uncured 14 days after written notice."
    )
    w.y -= 3 * mm

    w.line("5. Governing law", bold=True, gap=6 * mm)
    w.paragraph("This Agreement is governed by the laws of the Federal Republic of Germany.")
    pdf.save()


def build_contract_nda():
    pdf = new_pdf("contract-nda-mutual.pdf")
    w = Writer(pdf)
    w.line("MUTUAL NON-DISCLOSURE AGREEMENT", size=14, bold=True, gap=10 * mm)
    w.paragraph(
        "This Mutual Non-Disclosure Agreement is made on 14 February 2024 between Helios Data "
        "AG, Bahnhofstrasse 21, 8001 Zurich, and Brandt Ventures GmbH, Rosenthaler Strasse 40, "
        "10178 Berlin (each a Party)."
    )
    w.y -= 4 * mm

    w.line("1. Confidential Information", bold=True, gap=6 * mm)
    w.paragraph(
        "Confidential Information means any non-public technical, commercial, or financial "
        "information disclosed by one Party to the other, whether orally or in writing, that "
        "is marked confidential or would reasonably be understood to be confidential."
    )
    w.y -= 3 * mm

    w.line("2. Obligations", bold=True, gap=6 * mm)
    w.paragraph(
        "Each Party shall keep the other Party's Confidential Information strictly confidential "
        "and shall not disclose it to any third party without prior written consent, except to "
        "employees and advisers who need to know it and are bound by equivalent obligations."
    )
    w.y -= 3 * mm

    w.line("3. Duration", bold=True, gap=6 * mm)
    w.paragraph(
        "The obligations in this Agreement survive for a period of five (5) years from the date "
        "of disclosure, regardless of whether the discussions between the Parties result in a "
        "further agreement."
    )
    w.y -= 3 * mm

    w.line("4. Exclusions", bold=True, gap=6 * mm)
    w.paragraph(
        "This Agreement does not apply to information that is or becomes publicly available "
        "through no breach by the receiving Party, or that was lawfully known to the receiving "
        "Party before disclosure."
    )
    w.y -= 3 * mm

    w.line("5. Jurisdiction", bold=True, gap=6 * mm)
    w.paragraph(
        "The courts of Frankfurt am Main shall have exclusive jurisdiction over any dispute "
        "arising out of this Agreement."
    )
    pdf.save()


def build_receipt_office_supplies():
    pdf = new_pdf("receipt-office-supplies.pdf")
    w = Writer(pdf)
    w.line("Papierhaus Berlin", size=14, bold=True, gap=6 * mm)
    w.line("Torstrasse 112, 10119 Berlin", size=9, gap=8 * mm)

    w.line("RECEIPT", size=12, bold=True, gap=7 * mm)
    w.line("Receipt number: 88231")
    w.line("Date: 2024-05-07")
    w.line("Time: 14:32", gap=9 * mm)

    w.row("Item", "EUR", bold=True)
    w.rule()
    for description, amount in [
        ("Copy paper A4, 5 reams", 24.75),
        ("Whiteboard markers, pack of 8", 11.90),
        ("Document folders, 20 pack", 10.95),
    ]:
        w.row(description, f"{amount:,.2f}")
    w.rule()
    w.row("Total", "47.60 EUR", bold=True)
    w.y -= 6 * mm
    w.line("Paid by card (VISA ****4417)", size=9)
    w.line("Thank you for your purchase.", size=9)
    pdf.save()


def build_report_q3():
    pdf = new_pdf("report-q3-summary.pdf")
    w = Writer(pdf)
    w.line("Q3 2024 Operations Summary", size=14, bold=True, gap=10 * mm)

    w.line("Overview", bold=True, gap=6 * mm)
    w.paragraph(
        "The third quarter closed ahead of plan. Revenue grew 12 percent against Q2, driven "
        "largely by renewals in the logistics segment rather than new logo acquisition. Churn "
        "held flat at 3 percent. The board asked that we treat the renewal concentration as a "
        "risk rather than a success, and that framing is reflected in the priorities below."
    )
    w.y -= 3 * mm

    w.line("Headcount", bold=True, gap=6 * mm)
    w.paragraph(
        "We ended the quarter with 48 employees, up from 41 at the end of Q2. Engineering "
        "accounts for 22 of those. Two senior hires in the data platform team started in "
        "September and are still ramping."
    )
    w.y -= 3 * mm

    w.line("Berlin office", bold=True, gap=6 * mm)
    w.paragraph(
        "The Berlin office opened in August and now hosts the commercial team. The lease runs "
        "to 2027. Utilisation has been lower than expected, at roughly 40 percent of desks on "
        "an average day, and we will revisit the footprint in Q1."
    )
    w.y -= 3 * mm

    w.line("Priorities for Q4", bold=True, gap=6 * mm)
    w.paragraph(
        "First, reduce dependence on the logistics segment by closing at least four accounts "
        "outside it. Second, complete the migration off the legacy scheduler, which continues "
        "to cause weekend pages. Third, publish the internal service catalogue that was "
        "deferred from Q3."
    )
    pdf.save()


SCAN_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _scan_font(bold=False, size=26):
    path = SCAN_FONT_CANDIDATES[1 if bold else 0]
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        # Regeneration on a machine without DejaVu still produces a valid (uglier)
        # page; the committed PDF is the artefact that matters.
        return ImageFont.load_default()


def build_invoice_scanned_lowquality():
    # A genuine raster scan, not vector text made to look faint: the page is drawn
    # as an image, degraded, and embedded. There is no extractable text layer, so
    # this document exercises the model's vision path rather than PDF text parsing.
    rng = random.Random(20240517)
    scale = 150 / 72  # ~150 DPI
    width, height = int(PAGE_WIDTH * scale), int(PAGE_HEIGHT * scale)
    page = Image.new("L", (width, height), 250)
    draw = ImageDraw.Draw(page)

    x0, y = 150, 190

    def text(value, size=26, bold=False, gap=46, x=None):
        nonlocal y
        draw.text((x0 if x is None else x, y), value, font=_scan_font(bold, size), fill=70)
        y += gap

    def row(left, right, size=26, bold=False):
        nonlocal y
        font = _scan_font(bold, size)
        draw.text((x0, y), left, font=font, fill=70)
        right_width = draw.textlength(right, font=font)
        draw.text((width - 150 - right_width, y), right, font=font, fill=70)
        y += 44

    text("Vogel Druck & Medien", size=40, bold=True, gap=54)
    text("Gewerbering 7, 04435 Schkeuditz", size=22, gap=76)
    text("RECHNUNG / INVOICE", size=32, bold=True, gap=62)
    text("Invoice number: VD-2405")
    text("Invoice date: 2024-05-17")
    text("Payment due: 2024-06-16", gap=76)

    row("Description", "Amount (EUR)", bold=True)
    draw.line([(150, y), (width - 150, y)], fill=110, width=2)
    y += 18
    for description, amount in [
        ("Brochure printing, 500 copies", 148.00),
        ("Lamination and finishing", 33.90),
    ]:
        row(description, f"{amount:,.2f}")
    draw.line([(150, y), (width - 150, y)], fill=110, width=2)
    y += 18
    row("Subtotal", "181.90")
    row("VAT (19%)", "34.56")
    row("Total due", "216.46 EUR", bold=True)

    # Scanner artefacts: skew, speckle, soft focus, uneven exposure
    page = page.rotate(-1.8, resample=Image.BICUBIC, fillcolor=250)
    speckle = Image.new("L", (width, height), 255)
    speckle_draw = ImageDraw.Draw(speckle)
    for _ in range(4000):
        sx, sy = rng.randrange(width), rng.randrange(height)
        radius = rng.choice([1, 1, 1, 2])
        speckle_draw.ellipse(
            [sx - radius, sy - radius, sx + radius, sy + radius],
            fill=rng.randint(150, 215),
        )
    page = ImageChops.darker(page, speckle)
    page = page.filter(ImageFilter.GaussianBlur(radius=0.9))
    page = ImageEnhance.Contrast(page).enhance(0.62)
    page = ImageEnhance.Brightness(page).enhance(1.06)

    # Kept in memory rather than written next to the corpus: an intermediate .jpg left
    # behind by an interrupted run would fail test_corpus_contains_only_expected_files,
    # and a temp file's random name leaks into the PDF and breaks byte-determinism
    buffer = io.BytesIO()
    page.convert("RGB").save(buffer, "JPEG", quality=38, optimize=False)
    buffer.seek(0)

    pdf = new_pdf("invoice-scanned-lowquality.pdf")
    pdf.drawImage(ImageReader(buffer), 0, 0, width=PAGE_WIDTH, height=PAGE_HEIGHT)
    pdf.save()


BUILDERS = [
    build_invoice_nordwind_2401,
    build_invoice_kranich_2402,
    build_invoice_nordwind_2403,
    build_contract_consulting,
    build_contract_nda,
    build_receipt_office_supplies,
    build_report_q3,
    build_invoice_scanned_lowquality,
]


def main():
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    WRITTEN.clear()
    for build in BUILDERS:
        build()

    written = sorted(WRITTEN)
    print(f"Wrote {len(written)} PDFs to {CORPUS_DIR}:")
    for name in written:
        size = (CORPUS_DIR / name).stat().st_size
        print(f"  {name}  ({size:,} bytes)")

    # Regenerating only writes; it never deletes. Drop a builder and its PDF stays on
    # disk, where ingest_corpus.py globs *.pdf and would upload a document the dataset
    # knows nothing about. Say so loudly rather than silently leaving it.
    orphans = sorted({p.name for p in CORPUS_DIR.glob("*.pdf")} - WRITTEN)
    if orphans:
        print(f"\nWARNING: {len(orphans)} file(s) in the corpus were not written by any "
              "builder. Delete them, or they will be ingested and evaluated:")
        for name in orphans:
            print(f"  {name}")


if __name__ == "__main__":
    main()

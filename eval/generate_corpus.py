"""Generate the synthetic eval corpus.

Output is deterministic: same bytes on every run. The generated PDFs under
eval/corpus/ are committed, and eval/dataset.json asserts against the exact
values written here — change a number in this file and the dataset must change
with it.

    uv run python -m eval.generate_corpus
"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
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


def new_pdf(filename: str) -> canvas.Canvas:
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


BUILDERS = [
    build_invoice_nordwind_2401,
    build_invoice_kranich_2402,
    build_invoice_nordwind_2403,
    build_contract_consulting,
    build_receipt_office_supplies,
    build_report_q3,
]


def main():
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    for build in BUILDERS:
        build()
    written = sorted(p.name for p in CORPUS_DIR.glob("*.pdf"))
    print(f"Wrote {len(written)} PDFs to {CORPUS_DIR}:")
    for name in written:
        size = (CORPUS_DIR / name).stat().st_size
        print(f"  {name}  ({size:,} bytes)")


if __name__ == "__main__":
    main()

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import shutil
from dataclasses import asdict, dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import pdfplumber
import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

LOGGER = logging.getLogger("pdf-validator")

# Use an environment variable first. Fall back to Jessie's known local install.
_DEFAULT_TESSERACT = Path(
    r"C:\Users\MM04018\AppData\Local\Tesseract-OCR\tesseract.exe"
)
if os.getenv("TESSERACT_CMD"):
    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]
elif _DEFAULT_TESSERACT.exists():
    pytesseract.pytesseract.tesseract_cmd = str(_DEFAULT_TESSERACT)

FIELD_LABELS = {
    "insuredName": "Insured name",
    "plan": "Plan",
    "faceAmount": "Face amount",
    "apl": "Automatic Premium Loan",
    "loanRate": "Loan rate",
    "dividend": "Dividend option",
    "waiver": "Waiver of Premium",
    "gir": "GIR",
    "rtr": "RTR",
    "lisr": "LISR",
    "lisrTfa": "LISR Target Face Amount",
    "lisrModal": "LISR Modal Payment",
    "lisrLumpSum": "LISR Unscheduled Lump Sum",
    "alirModal": "ALIR Modal Payment",
    "alirDuration": "ALIR Payment Duration",
    "alirUnscheduled": "ALIR Unscheduled",
    "frequency": "Payment frequency",
    "signedDate": "Signature date",
    "signatureEvidence": "Signature evidence",
}

COMPARE_FIELDS = [
    "insuredName", "plan", "faceAmount", "apl", "loanRate", "dividend",
    "waiver", "gir", "rtr", "lisr", "lisrTfa", "lisrModal",
    "lisrLumpSum", "frequency",
]

TERM_EXCLUDED = {
    "apl", "loanRate", "dividend", "gir", "rtr", "lisr", "lisrTfa",
    "lisrModal", "lisrLumpSum", "alirModal", "alirDuration",
    "alirUnscheduled",
}


@dataclass
class PageText:
    page_number: int
    text: str
    source: str
    rotation: int = 0
    native_characters: int = 0
    warnings: list[str] = dataclass_field(default_factory=list)


@dataclass
class FieldValue:
    value: str = ""
    confidence: int = 0
    page_number: Optional[int] = None
    source: str = ""
    matched_label: str = ""
    excluded: bool = False
    warnings: list[str] = dataclass_field(default_factory=list)


@dataclass
class DocumentResult:
    path: str
    expected_type: str
    detected_type: str = "unknown"
    fields: dict[str, FieldValue] = dataclass_field(default_factory=dict)
    pages: list[PageText] = dataclass_field(default_factory=list)
    warnings: list[str] = dataclass_field(default_factory=list)
    errors: list[str] = dataclass_field(default_factory=list)


@dataclass
class ComparisonRow:
    field: str
    label: str
    application: str
    illustration: str
    status: str
    note: str


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def normalized(value: Any) -> str:
    text = clean(value).lower()
    text = re.sub(r"[,$%]", "", text)
    text = re.sub(r"[‐‑‒–—]", "-", text)
    text = re.sub(r"\bwl\s*(\d+)\b", r"whole life \1", text)
    text = re.sub(r"\bwholelife\s*(\d+)\b", r"whole life \1", text)
    text = re.sub(r"\bwhole life\s+(\d+)\s+pay\b", r"whole life \1", text)
    text = re.sub(r"paid[ -]?up additions?|\bpua\b", "paid-up additions", text)
    text = re.sub(r"supplemental insurance dividend(?:/flex)?", "lisr/flex", text)
    text = re.sub(r"semi annual", "semi-annual", text)
    return clean(text)


def make_field(
    value: Any = "",
    confidence: int = 0,
    page_number: Optional[int] = None,
    source: str = "",
    matched_label: str = "",
    excluded: bool = False,
    warnings: Optional[list[str]] = None,
) -> FieldValue:
    return FieldValue(
        value=clean(value),
        confidence=max(0, min(100, int(confidence))),
        page_number=page_number,
        source=source,
        matched_label=matched_label,
        excluded=excluded,
        warnings=list(warnings or []),
    )


def normalize_money(value: Any) -> str:
    match = re.search(r"\$?\s*([\d,]+(?:\.\d{1,2})?)", str(value or ""))
    if not match:
        return ""
    raw = match.group(1).replace(",", "")
    try:
        number = float(raw)
    except ValueError:
        return ""
    return f"${number:,.2f}" if "." in match.group(1) else f"${number:,.0f}"


def normalize_name(value: Any) -> str:
    text = clean(value)
    text = re.sub(r"\b\d{5,}\b", "", text)
    text = re.sub(r",\s*(?:Male|Female).*", "", text, flags=re.I)
    text = re.sub(r",\s*Age\s+\d+.*", "", text, flags=re.I)
    return clean(text)


def normalize_plan(value: Any) -> str:
    text = clean(value)
    match = re.search(r"\bWL\s*(\d+)\b", text, re.I)
    if match:
        return f"Whole Life {match.group(1)}"
    match = re.search(r"\bWhole\s*Life\s*(?:Legacy\s*)?(\d+)(?:\s*Pay)?\b", text, re.I)
    if match:
        return f"Whole Life {match.group(1)}"
    match = re.search(r"\bVantage\s+Term(?:\s+ART|\s+\d+)?\b", text, re.I)
    return clean(match.group(0)) if match else text


def normalize_frequency(value: Any) -> str:
    match = re.search(
        r"\b(Monthly|Quarterly|Semi[- ]annual|Annual|Single Premium)\b",
        clean(value), re.I,
    )
    if not match:
        return ""
    text = match.group(1).lower().replace("semi annual", "semi-annual")
    return " ".join(part.capitalize() for part in text.split())


def normalize_choice(value: Any) -> str:
    value = clean(value)
    if re.fullmatch(r"Y|Yes", value, re.I):
        return "Yes"
    if re.fullmatch(r"N|No", value, re.I):
        return "No"
    return ""


def parse_yes_no(value: Any) -> str:
    text = clean(value)
    if re.search(r"(?:\[x\]|☒|■|✓|✔|\bX\b)\s*Yes|\[(?:APL|WP|GIR|RTR|LISR)\]\s*Y\b", text, re.I):
        return "Yes"
    if re.search(r"(?:\[x\]|☒|■|✓|✔|\bX\b)\s*(?:No|1R)|\[(?:APL|WP|GIR|RTR|LISR)\]\s*N\b", text, re.I):
        return "No"
    return normalize_choice(text)


def normalize_dividend(value: Any, design: str = "") -> str:
    if re.search(r"base\s*\+\s*lisr", clean(design), re.I):
        return "LISR/Flex"
    text = re.sub(r"^\d+\s+to\s+\d+\s*[-:]\s*", "", clean(value), flags=re.I)
    if re.search(r"lisr\s*/?\s*flex|supplemental insurance dividend", text, re.I):
        return "LISR/Flex"
    if re.search(r"paid[- ]?up additions?|\bpua\b", text, re.I):
        return "Paid-Up Additions"
    if re.search(r"dividend accumulations?", text, re.I):
        return "Dividend Accumulations"
    if re.search(r"reduce premium", text, re.I):
        return "Reduce Premium"
    if re.search(r"\bcash\b", text, re.I):
        return "Cash"
    return text


def parse_date(value: Any) -> Optional[dt.date]:
    text = clean(value).replace("O", "0").replace("l", "1")
    match = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", text)
    if not match:
        return None
    month, day, year = map(int, match.groups())
    if year < 100:
        year += 2000 if year < 50 else 1900
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def first_match(
    pages: Iterable[PageText],
    patterns: Iterable[str],
    parser: Callable[[str], str] = clean,
    source: str = "regex",
    confidence: int = 85,
) -> FieldValue:
    for page in pages:
        for pattern in patterns:
            match = re.search(pattern, page.text, re.I | re.M | re.S)
            if not match:
                continue
            raw = match.group(1) if match.lastindex else match.group(0)
            parsed = parser(raw)
            if parsed:
                return make_field(parsed, confidence, page.page_number, source, pattern)
    return make_field()


def validate_pdf(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a PDF file: {path.name}")
    if path.stat().st_size < 100:
        raise ValueError(f"PDF is empty or too small: {path.name}")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ValueError(f"File does not have a PDF header: {path.name}")


def quality_score(text: str) -> float:

    if not text:
        return 0.0

    cid_count = len(
        re.findall(
            r"\(cid:\d+\)",
            text
        )
    )

    if cid_count > 10:
        return 0.0

    words = re.findall(
        r"[A-Za-z]{3,}",
        text
    )

    return min(
        len(words) / 70,
        1.0
    )

def prepare_ocr_image(image: Image.Image) -> tuple[Image.Image, int]:
    image = ImageOps.exif_transpose(image).convert("RGB")
    rotation = 0
    try:
        osd = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
        rotation = int(osd.get("rotate", 0) or 0)
        if rotation:
            image = image.rotate(rotation, expand=True)
    except Exception as exc:
        LOGGER.debug("Orientation detection skipped: %s", exc)
    gray = ImageOps.grayscale(image)
    gray = ImageEnhance.Contrast(gray).enhance(1.6)
    return gray.filter(ImageFilter.SHARPEN), rotation


def ocr_page(path: Path, page_number: int) -> tuple[str, int]:
    images = convert_from_path(
        str(path), dpi=250, first_page=page_number, last_page=page_number,
        fmt="png", thread_count=1,
    )
    if not images:
        return "", 0
    image, rotation = prepare_ocr_image(images[0])
    return pytesseract.image_to_string(image, config="--oem 3 --psm 6"), rotation


def extract_pages(pdf_path: str, max_pages: int = 25) -> list[PageText]:
    path = Path(pdf_path)
    validate_pdf(path)
    pages: list[PageText] = []
    with pdfplumber.open(path) as pdf:
        for index, page in enumerate(pdf.pages[:max_pages], start=1):
            warnings: list[str] = []
            try:
                native = page.extract_text(layout=True) or ""
            except Exception as exc:
                native = ""
                warnings.append(f"Native extraction failed: {exc}")
            text, source, rotation = native, "native", 0
            if (
    len(clean(native)) < 500
    or quality_score(native) < 0.5
    or "(cid:" in native
):
                try:
                    ocr_text, rotation = ocr_page(path, index)
                    if quality_score(ocr_text) >= quality_score(native):
                        text, source = ocr_text, "ocr"
                except Exception as exc:
                    warnings.append(f"OCR failed: {exc}")
            pages.append(PageText(index, text, source, rotation, len(native), warnings))
    if not any(clean(page.text) for page in pages):
        raise RuntimeError("No readable text could be extracted from the PDF")
    return pages


def detect_document_type(pages: list[PageText]) -> str:
    text = "\n".join(page.text for page in pages[:6])
    if re.search(r"Application for Individual Life|Part 1|Life Product Information", text, re.I):
        return "app"
    if re.search(r"Life Insurance Illustration|Client Input Summary|Narrative Summary", text, re.I):
        return "ill"
    return "unknown"


def page_lines(page: PageText) -> list[str]:
    return [clean(line) for line in page.text.splitlines() if clean(line)]


def line_value(pages: list[PageText], labels: Iterable[str], source: str) -> FieldValue:
    for page in pages:
        lines = page_lines(page)
        for index, line in enumerate(lines):
            for label in labels:
                position = line.lower().find(label.lower())
                if position < 0:
                    continue
                same_line = clean(line[position + len(label):]).lstrip(":-| ")
                if same_line:
                    return make_field(same_line, 95, page.page_number, source, label)
                for nearby in lines[index + 1:index + 4]:
                    if nearby:
                        return make_field(nearby, 88, page.page_number, source, label)
    return make_field()


def parse_application_name(pages: list[PageText]) -> FieldValue:
    page_one = next((page for page in pages if page.page_number == 1), None)
    if page_one:
        lines = page_lines(page_one)
        q1 = next((i for i, line in enumerate(lines) if re.match(r"^1\s*\.", line) and re.search(r"legal name", line, re.I)), -1)
        if q1 >= 0:
            for candidate in reversed(lines[max(0, q1 - 8):q1]):
                if re.fullmatch(r"[A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*){1,4}", candidate):
                    if not re.search(r"owner|juvenile|coverage|supplement|taxpayer", candidate, re.I):
                        return make_field(candidate, 98, 1, "A1 question", "Full legal name")
    for page in reversed(pages):
        lines = page_lines(page)
        signature = next((i for i, line in enumerate(lines) if re.search(r"Signature of Proposed Insured", line, re.I)), -1)
        if signature >= 0:
            for candidate in lines[signature + 1:signature + 14]:
                if re.fullmatch(r"[A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*){1,4}", candidate):
                    if not re.search(r"printed name|date|city|state|signature", candidate, re.I):
                        return make_field(candidate, 94, page.page_number, "signature fallback", "Printed name")
    return make_field(warnings=["Insured name not found"])


def extract_signature_date(pages: list[PageText]) -> FieldValue:
    return first_match(
        pages,
        [
            r"(?:Signature of Proposed Insured|Proposed Insured Signature)[^\n]{0,220}?Date\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
            r"(?:Signature of Owner|Owner Signature)[^\n]{0,220}?Date\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        ],
        clean, "signature date", 94,
    )


def signature_evidence(pages: list[PageText], signed_date: FieldValue) -> FieldValue:
    text = "\n".join(page.text for page in pages)
    if re.search(r"DocuSign Envelope ID|Adobe Sign|electronically signed|digital signature", text, re.I):
        return make_field("Signed", 96, source="electronic signature evidence")
    if signed_date.value and parse_date(signed_date.value):
        return make_field("Signed", 94, signed_date.page_number, "completed signature date")
    return make_field("Unsigned", 95, source="no electronic signature or completed signature date")


def parse_application(pages: list[PageText]) -> dict[str, FieldValue]:

    print("\n")
    print("=" * 80)
    print("APPLICATION TEXT SAMPLE")
    print("=" * 80)

    for page in pages[:5]:
        print(f"\nPAGE {page.page_number}\n")
        print(page.text[:3000])

    result = {key: make_field() for key in FIELD_LABELS}
    result["insuredName"] = parse_application_name(pages)
    whole = first_match(pages, [r"(?:^|\n)\s*((?:Survivorship\s+)?Whole\s*Life\s*(?:Legacy\s*)?\d+|WholeLife\d+|WL\s*\d+)\s*\n\s*2\s*\."], normalize_plan, "C2", 94)
    term = first_match(pages, [r"(?:^|\n)\s*(Vantage\s+Term(?:\s+[A-Za-z0-9-]+)*)\s*\n\s*16\s*\."], normalize_plan, "C16", 94)
    result["plan"] = whole if whole.value else term
    is_term = bool(term.value and not whole.value)
    face_pattern = r"(?:^|\n)\s*\$?\s*([1-9][\d,]*(?:\.\d{2})?)\s*\n\s*17\s*\." if is_term else r"(?:^|\n)\s*\$?\s*([1-9][\d,]*(?:\.\d{2})?)\s*\n\s*3\s*\."
    result["faceAmount"] = first_match(pages, [face_pattern], normalize_money, "C17/C3", 94)
    result["frequency"] = first_match(pages, [r"Frequency[^\n]{0,160}?(Monthly|Quarterly|Semi[- ]annual|Annual|Single Premium)"], normalize_frequency, "Payment section", 84)
    if is_term:
        result["waiver"] = first_match(pages, [r"18\s*\.[^\n]{0,180}"], parse_yes_no, "C18", 88)
        for key in TERM_EXCLUDED:
            result[key] = make_field("N/A", 100, source="Vantage Term rule", excluded=True)
    else:
        result["apl"] = first_match(pages, [r"Automatic Premium Loan[^\n]{0,140}"], parse_yes_no, "C4", 80)
        result["loanRate"] = first_match(pages, [r"Loan rate[^\n]{0,120}?(Adjustable|Fixed)"], clean, "C5", 82)
        result["dividend"] = first_match(pages, [r"Dividend Option[^\n]{0,180}"], normalize_dividend, "C6", 78)
        result["waiver"] = first_match(pages, [r"7\s*\.[^\n]{0,180}"], parse_yes_no, "C7", 78)
        result["gir"] = first_match(pages, [r"Guaranteed Insurability Rider[^\n]{0,100}"], parse_yes_no, "C10", 75)
        result["rtr"] = first_match(pages, [r"Renewable Term Rider[^\n]{0,100}"], parse_yes_no, "C11", 75)
        result["lisrTfa"] = first_match(pages, [r"Life Insurance Supplement Rider[\s\S]{0,260}?Face Amount\s*\$?\s*([\d,]+(?:\.\d{2})?)"], normalize_money, "C13a", 80)
        result["lisrModal"] = first_match(pages, [r"Life Insurance Supplement Rider[\s\S]{0,380}?Modal Payment\s*\$?\s*([\d,]+(?:\.\d{2})?)"], normalize_money, "C13b", 80)
        result["lisrLumpSum"] = first_match(pages, [r"Life Insurance Supplement Rider[\s\S]{0,500}?Unscheduled Lump Sum\s*\$?\s*([\d,]+(?:\.\d{2})?)"], normalize_money, "C13c", 80)
        result["lisr"] = make_field("Yes" if any(result[k].value for k in ("lisrTfa", "lisrModal", "lisrLumpSum")) else "No", 85, source="C13 presence")
    result["signedDate"] = extract_signature_date(pages)
    result["signatureEvidence"] = signature_evidence(pages, result["signedDate"])
    return result


def parse_illustration_fallback(pages: list[PageText]) -> dict[str, FieldValue]:
    result = {key: make_field() for key in FIELD_LABELS}
    text = "\n".join(page.text for page in pages)
    result["insuredName"] = first_match(pages, [r"Prepared for\s+([^\n]{2,100})"], normalize_name, "Prepared for", 92)
    plan = line_value(pages, ["Policy", "Product"], "Illustration narrative")
    result["plan"] = make_field(normalize_plan(plan.value), 90 if plan.value else 0, plan.page_number, plan.source, plan.matched_label)
    face = line_value(pages, ["Base Policy Face Amount", "Base Face Amount"], "Initial Coverage Information")
    result["faceAmount"] = make_field(normalize_money(face.value), 95 if face.value else 0, face.page_number, face.source, face.matched_label)
    mode = line_value(pages, ["Premium Payment Mode", "Premium Mode"], "Initial Premium Information")
    result["frequency"] = make_field(normalize_frequency(mode.value), 94 if mode.value else 0, mode.page_number, mode.source, mode.matched_label)
    dividend = line_value(pages, ["Initial Base Dividend Option", "Initial Dividend Option"], "Policy Information")
    result["dividend"] = make_field(normalize_dividend(dividend.value, text), 94 if dividend.value else 0, dividend.page_number, dividend.source, dividend.matched_label)
    result["apl"] = make_field("Yes", 92, source="Loan Interest Option", matched_label="Borrow from Policy") if re.search(r"Borrow from Policy", text, re.I) else make_field()
    result["loanRate"] = make_field("Adjustable", 92, source="Policy loan disclosure") if re.search(r"This rate may change on each policy anniversary date", text, re.I) else make_field()
    result["lisrModal"] = first_match(pages, [r"LISR Premium First Year[^\d$]*\$?\s*([\d,]+(?:\.\d{2})?)"], normalize_money, "LISR Premium First Year", 94)
    result["lisrTfa"] = first_match(pages, [r"LISR Target Face Amount(?:\s*\(TFA\))?[^\d$]*\$?\s*([\d,]+(?:\.\d{2})?)"], normalize_money, "LISR TFA", 94)
    has_lisr = bool(re.search(r"Base\s*\+\s*LISR|LISR/Flex|LISR Premium First Year|LISR Target Face Amount", text, re.I))
    result["lisr"] = make_field("Yes" if has_lisr else "No", 92, source="LISR indicators")
    alir = re.search(r"initial payment of\s*\$?\s*([\d,]+(?:\.\d{2})?)\s*(?:monthly|quarterly|semi[- ]annual|annual)?\s*for\s*(\d+)\s*years?\s*under the ALIR", text, re.I)
    
    if alir:
        result["alirModal"] = make_field(normalize_money(alir.group(1)), 94, source="Additional Information About This Illustration")
        result["alirDuration"] = make_field(f"{alir.group(2)} years", 94, source="Additional Information About This Illustration")
    result["waiver"] = first_match(pages, [r"Waiver of Premium\s*\[WP\]\s*([YN]|Yes|No)"], normalize_choice, "Rider listing", 90)
    result["gir"] = first_match(pages, [r"Guaranteed Insurability Rider\s*\[GIR\]\s*([YN]|Yes|No)"], normalize_choice, "Rider listing", 90)
    result["rtr"] = first_match(pages, [r"Renewable Term Rider\s*\[RTR\]\s*([YN]|Yes|No)"],
                                normalize_choice, "Rider listing", 90)
    return result


def parse_illustration(pages: list[PageText]) -> dict[str, FieldValue]:
    fallback = parse_illustration_fallback(pages)
    summary_pages = [page for page in pages if re.search(r"Client Input Summary", page.text, re.I)]
    result = fallback
    if summary_pages:
        result = {key: make_field() for key in FIELD_LABELS}
        first = line_value(summary_pages, ["First Name"], "Client Input Summary")
        last = line_value(summary_pages, ["Last Name"], "Client Input Summary")
        result["insuredName"] = make_field(normalize_name(f"{first.value} {last.value}"), 98, first.page_number or last.page_number, "Client Input Summary", "First Name + Last Name")
        product = line_value(summary_pages, ["Product"], "Client Input Summary")
        result["plan"] = make_field(normalize_plan(product.value), 98, product.page_number, product.source, product.matched_label)
        design = line_value(summary_pages, ["Design"], "Client Input Summary")
        face = line_value(summary_pages, ["Base Face Amount"], "Client Input Summary")
        result["faceAmount"] = make_field(normalize_money(face.value), 98, face.page_number, face.source, face.matched_label)
        mode = line_value(summary_pages, ["Premium Mode"], "Client Input Summary")
        result["frequency"] = make_field(normalize_frequency(mode.value), 98, mode.page_number, mode.source, mode.matched_label)
        dividend = line_value(summary_pages, ["Dividend Option"], "Client Input Summary")
        result["dividend"] = make_field(normalize_dividend(dividend.value, design.value), 98, dividend.page_number or design.page_number, "Client Input Summary", "Dividend Option/Design")
        loan_option = line_value(summary_pages, ["Loan Interest Option"], "Client Input Summary")
        result["apl"] = make_field("Yes" if re.search(r"Borrow from Policy", loan_option.value, re.I) else "", 96 if loan_option.value else 0, loan_option.page_number, loan_option.source, loan_option.matched_label)
        loan_rate = line_value(summary_pages, ["Loan Rate"], "Client Input Summary")
        rate = "Adjustable" if re.search(r"Adjustable", loan_rate.value, re.I) else ("Fixed" if re.search(r"Fixed", loan_rate.value, re.I) else "")
        result["loanRate"] = make_field(rate, 98 if rate else 0, loan_rate.page_number, loan_rate.source, loan_rate.matched_label)
        for key, labels in {"waiver": ["Waiver of Premium [WP]", "Waiver of Premium"], "gir": ["Guaranteed Insurability Rider [GIR]"], "rtr": ["Renewable Term Rider [RTR]"]}.items():
            found = line_value(summary_pages, labels, "Client Input Summary")
            result[key] = make_field(normalize_choice(found.value), 98 if found.value else 0, found.page_number, found.source, found.matched_label)
        tfa = line_value(summary_pages, ["LISR Target Face Amount (TFA)", "LISR Target Face Amount"], "Client Input Summary")
        lump = line_value(summary_pages, ["LISR Lump Sum"], "Client Input Summary")
        result["lisrTfa"] = make_field(normalize_money(tfa.value), 98 if tfa.value else 0, tfa.page_number, tfa.source, tfa.matched_label)
        result["lisrLumpSum"] = make_field(normalize_money(lump.value) or ("$0" if clean(lump.value) == "0" else ""), 98 if lump.value else 0, lump.page_number, lump.source, lump.matched_label)
        result["lisrModal"] = fallback["lisrModal"]
        has_lisr = bool(re.search(r"Base\s*\+\s*LISR", design.value, re.I) or result["lisrTfa"].value or re.search(r"LISR/Flex", result["dividend"].value, re.I))
        result["lisr"] = make_field("Yes" if has_lisr else "No", 98, design.page_number, "Client Input Summary", "Design")
        result["alirModal"] = fallback["alirModal"]
        result["alirDuration"] = fallback["alirDuration"]
        for key in result:
            if not result[key].value and fallback.get(key) and fallback[key].value:
                result[key] = fallback[key]
    result["signedDate"] = extract_signature_date(pages)
    result["signatureEvidence"] = signature_evidence(pages, result["signedDate"])
    return result


def parse_document(pdf_path: str, expected_type: str, max_pages: int = 25) -> DocumentResult:
    result = DocumentResult(path=pdf_path, expected_type=expected_type)
    try:
        result.pages = extract_pages(pdf_path, max_pages)
        result.detected_type = detect_document_type(result.pages)
        if result.detected_type != "unknown" and result.detected_type != expected_type:
            result.errors.append(f"Wrong file type: expected {expected_type}, detected {result.detected_type}")
            return result
        result.fields = parse_application(result.pages) if expected_type == "app" else parse_illustration(result.pages)
        for page in result.pages:
            result.warnings.extend(f"Page {page.page_number}: {warning}" for warning in page.warnings)
    except Exception as exc:
        result.errors.append(str(exc))
        LOGGER.exception("Failed to parse %s", pdf_path)
    return result


def compare_documents(app: DocumentResult, ill: DocumentResult, state: str = "") -> list[ComparisonRow]:
    if app.errors or ill.errors or not app.fields or not ill.fields:
        return [ComparisonRow("SYSTEM", "System", "; ".join(app.errors) or "Extraction failed", "; ".join(ill.errors) or "Extraction failed", "Failed", "One or both documents could not be validated.")]
    rows: list[ComparisonRow] = []
    is_term = bool(re.match(r"^Vantage\s+Term\b", app.fields.get("plan", make_field()).value, re.I))
    for key in COMPARE_FIELDS:
        app_field = app.fields.get(key, make_field())
        ill_field = ill.fields.get(key, make_field())
        if is_term and key in TERM_EXCLUDED:
            rows.append(ComparisonRow(key, FIELD_LABELS[key], "N/A", "N/A", "Excluded", "Not applicable for Vantage Term."))
            continue
        if key.startswith("lisr") and key != "lisr" and normalized(app.fields.get("lisr", make_field()).value) != "yes":
            rows.append(ComparisonRow(key, FIELD_LABELS[key], "N/A", "N/A", "Excluded", "No LISR on the application."))
            continue
        if not app_field.value or not ill_field.value:
            status, note = "Review", "Could not extract a value from one or both documents."
        elif normalized(app_field.value) == normalized(ill_field.value):
            status, note = "Match", "Values match."
        else:
            status, note = "Mismatch", "Values differ."
        rows.append(ComparisonRow(key, FIELD_LABELS[key], app_field.value or "Not Found", ill_field.value or "Not Found", status, note))
    if state.upper() == "NY":
        app_date_value = app.fields.get("signedDate", make_field()).value
        ill_date_value = ill.fields.get("signedDate", make_field()).value
        app_date, ill_date = parse_date(app_date_value), parse_date(ill_date_value)
        if not app_date or not ill_date:
            rows.append(ComparisonRow("nySignatureTiming", "NY signature timing", app_date_value or "Not Found", ill_date_value or "Not Found", "Review", "Could not parse one or both dates for Reg 74 validation."))
        elif ill_date > app_date:
            rows.append(ComparisonRow("nySignatureTiming", "NY signature timing", app_date_value, ill_date_value, "Mismatch", "Illustration signature date is after the Part 1 date."))
        else:
            rows.append(ComparisonRow("nySignatureTiming", "NY signature timing", app_date_value, ill_date_value, "Match", "Illustration was signed on or before the Part 1 date."))
    return rows


def document_to_dict(document: DocumentResult, include_text: bool = True) -> dict[str, Any]:
    payload = asdict(document)
    if not include_text:
        for page in payload["pages"]:
            page.pop("text", None)
    return payload


def build_report(app: DocumentResult, ill: DocumentResult, rows: list[ComparisonRow], state: str = "") -> dict[str, Any]:
    return {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "state": state.upper(),
        "application": document_to_dict(app),
        "illustration": document_to_dict(ill),
        "comparison": [asdict(row) for row in rows],
        "summary": {
            "matches": sum(row.status == "Match" for row in rows),
            "mismatches": sum(row.status == "Mismatch" for row in rows),
            "reviews": sum(row.status == "Review" for row in rows),
            "excluded": sum(row.status == "Excluded" for row in rows),
            "failed": sum(row.status == "Failed" for row in rows),
        },
        "runtime": {
            "tesseract": pytesseract.pytesseract.tesseract_cmd,
            "tesseractAvailable": Path(pytesseract.pytesseract.tesseract_cmd).exists() or shutil.which("tesseract") is not None,
            "popplerAvailable": shutil.which("pdftoppm") is not None,
        },
    }

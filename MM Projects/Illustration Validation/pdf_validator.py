#!/usr/bin/env python3
"""Part 1 application vs. sales illustration PDF validator.

Features
- Native PDF text extraction with page-level OCR fallback
- Rotation correction for upside-down or sideways scans
- File-type validation (application vs. illustration)
- Question-aware Part 1 parsing
- Client Input Summary-first illustration parsing with narrative fallbacks
- Term/Whole Life applicability rules
- LISR/ALIR extraction
- Signature and NY Reg 74 safeguards
- JSON output suitable for a browser/API integration

Install Python dependencies:
    pip install pdfplumber pytesseract pdf2image pillow

System dependencies:
    Tesseract OCR and Poppler (pdftoppm)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import pdfplumber
import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

LOGGER = logging.getLogger("pdf-validator")

TERM_EXCLUDED_FIELDS = {
    "apl",
    "loanRate",
    "dividend",
    "gir",
    "rtr",
    "lisr",
    "lisrTfa",
    "lisrModal",
    "lisrLumpSum",
    "alirModal",
    "alirDuration",
    "alirUnscheduled",
}

COMPARISON_FIELDS = [
    "insuredName",
    "plan",
    "faceAmount",
    "apl",
    "loanRate",
    "dividend",
    "waiver",
    "gir",
    "rtr",
    "lisr",
    "lisrTfa",
    "lisrModal",
    "lisrLumpSum",
    "frequency",
    "signedDate",
    "signatureEvidence",
]

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


@dataclass
class PageText:
    page_number: int
    text: str
    source: str
    rotation: int = 0
    native_characters: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class FieldValue:
    value: str = ""
    confidence: int = 0
    page_number: Optional[int] = None
    source: str = ""
    matched_label: str = ""
    excluded: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class DocumentResult:
    path: str
    expected_type: str
    detected_type: str
    fields: dict[str, FieldValue]
    pages: list[PageText]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ComparisonRow:
    field: str
    label: str
    application: str
    illustration: str
    status: str
    note: str


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalized(value: Any) -> str:
    text = clean(value).lower()
    text = re.sub(r"[,$%]", "", text)
    text = re.sub(r"[‐‑‒–—]", "-", text)
    text = re.sub(r"\bwl\s*(\d+)\b", r"whole life \1", text)
    text = re.sub(r"\bwholelife\s*(\d+)\b", r"whole life \1", text)
    text = re.sub(r"\bwhole life\s+(\d+)\s+pay\b", r"whole life \1", text)
    text = re.sub(r"paid[ -]?up additions?|\bpua\b", "paid-up additions", text)
    text = re.sub(r"semi annual", "semi-annual", text)
    text = re.sub(r"monthly pac", "monthly", text)
    text = re.sub(r"annual individual direct bill|annual direct bill", "annual", text)
    text = re.sub(r"pre[- ]?authorized check", "pac", text)
    text = re.sub(r"supplemental insurance dividend(?:/flex)?", "lisr/flex", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


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
    text = re.sub(r",\s*(?:Male|Female).*?$", "", text, flags=re.I)
    text = re.sub(r",\s*Age\s+\d+.*?$", "", text, flags=re.I)
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
    if match:
        return clean(match.group(0))
    return text


def normalize_frequency(value: Any) -> str:
    match = re.search(
        r"\b(Monthly|Quarterly|Semi[- ]annual|Annual|Single Premium)\b",
        clean(value),
        re.I,
    )
    if not match:
        return ""
    result = match.group(1).lower().replace("semi annual", "semi-annual")
    return " ".join(word.capitalize() for word in result.split())


def normalize_choice(value: Any) -> str:
    text = clean(value)
    if re.fullmatch(r"Y|Yes", text, re.I):
        return "Yes"
    if re.fullmatch(r"N|No", text, re.I):
        return "No"
    return ""


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


def field(
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
                return field(parsed, confidence, page.page_number, source, pattern)
    return field()


def quality_score(text: str) -> float:
    if not text:
        return 0.0
    printable = sum(ch.isprintable() for ch in text) / max(1, len(text))
    words = re.findall(r"[A-Za-z]{3,}", text)
    replacement_penalty = text.count("�") / max(1, len(text))
    return max(0.0, printable + min(len(words) / 80, 1.0) - replacement_penalty * 10)


def validate_pdf(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a PDF file: {path}")
    if path.stat().st_size < 100:
        raise ValueError(f"PDF is empty or too small: {path}")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ValueError(f"File does not have a PDF header: {path}")


def deskew_for_ocr(image: Image.Image) -> tuple[Image.Image, int]:
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
    gray = gray.filter(ImageFilter.SHARPEN)
    return gray, rotation


def ocr_page(pdf_path: Path, page_number: int, dpi: int = 250) -> tuple[str, int]:
    images = convert_from_path(
        str(pdf_path),
        dpi=dpi,
        first_page=page_number,
        last_page=page_number,
        fmt="png",
        thread_count=1,
    )
    if not images:
        return "", 0
    image, rotation = deskew_for_ocr(images[0])
    text = pytesseract.image_to_string(image, config="--oem 3 --psm 6")
    return text, rotation


def extract_pages(pdf_path: str, max_pages: int = 25) -> list[PageText]:
    path = Path(pdf_path)
    validate_pdf(path)
    pages: list[PageText] = []

    try:
        with pdfplumber.open(path) as pdf:
            page_count = min(len(pdf.pages), max_pages)
            for index in range(page_count):
                page_number = index + 1
                warnings: list[str] = []
                native = ""
                try:
                    native = pdf.pages[index].extract_text(layout=True) or ""
                except Exception as exc:
                    warnings.append(f"Native extraction failed: {exc}")

                use_ocr = len(clean(native)) < 120 or quality_score(native) < 0.9
                final_text = native
                source = "native"
                rotation = 0

                if use_ocr:
                    LOGGER.info("OCR fallback on page %s of %s", page_number, path.name)
                    try:
                        ocr_text, rotation = ocr_page(path, page_number)
                        if quality_score(ocr_text) >= quality_score(native):
                            final_text = ocr_text
                            source = "ocr"
                        else:
                            warnings.append("OCR did not improve text quality; native text retained")
                    except Exception as exc:
                        warnings.append(f"OCR failed: {exc}")

                pages.append(
                    PageText(
                        page_number=page_number,
                        text=final_text,
                        source=source,
                        rotation=rotation,
                        native_characters=len(native),
                        warnings=warnings,
                    )
                )
    except Exception as native_error:
        LOGGER.warning("Native PDF open failed, using OCR only: %s", native_error)
        try:
            images = convert_from_path(str(path), dpi=250, first_page=1, last_page=max_pages)
        except Exception as ocr_error:
            raise RuntimeError(
                f"PDF could not be opened natively or rendered for OCR: {ocr_error}"
            ) from native_error
        for index, image in enumerate(images, start=1):
            processed, rotation = deskew_for_ocr(image)
            text = pytesseract.image_to_string(processed, config="--oem 3 --psm 6")
            pages.append(PageText(index, text, "ocr", rotation, 0))

    if not any(clean(page.text) for page in pages):
        raise RuntimeError("No readable text could be extracted from the PDF")
    return pages


def detect_document_type(pages: list[PageText]) -> str:
    text = "\n".join(page.text for page in pages[:5])
    if re.search(r"Application for Individual Life|Part 1|Life Product Information", text, re.I):
        return "app"
    if re.search(r"Life Insurance Illustration|Client Input Summary|Narrative Summary", text, re.I):
        return "ill"
    return "unknown"


def page_lines(page: PageText) -> list[str]:
    return [clean(line) for line in page.text.splitlines() if clean(line)]


def parse_application_name(pages: list[PageText]) -> FieldValue:
    page_one = next((page for page in pages if page.page_number == 1), None)
    if page_one:
        lines = page_lines(page_one)
        q1 = next(
            (
                index
                for index, line in enumerate(lines)
                if re.match(r"^1\s*\.", line) and re.search(r"Full legal name|legal name", line, re.I)
            ),
            -1,
        )
        if q1 >= 0:
            for index in range(q1 - 1, max(-1, q1 - 9), -1):
                candidate = lines[index]
                if re.fullmatch(r"[A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*){1,4}", candidate):
                    if not re.search(r"owner|juvenile|coverage|supplement|taxpayer", candidate, re.I):
                        return field(candidate, 98, 1, "A1 question", "Full legal name")

    for page in reversed(pages):
        lines = page_lines(page)
        signature = next(
            (i for i, line in enumerate(lines) if re.search(r"Signature of Proposed Insured", line, re.I)),
            -1,
        )
        if signature >= 0:
            for candidate in lines[signature + 1 : signature + 14]:
                if re.fullmatch(r"[A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*){1,4}", candidate):
                    if not re.search(r"printed name|date|city|state|signature", candidate, re.I):
                        return field(candidate, 94, page.page_number, "signature fallback", "Printed name")
    return field(warnings=["Insured name not found"])


def parse_application(pages: list[PageText]) -> dict[str, FieldValue]:
    fields = {key: field() for key in FIELD_LABELS}
    full_text = "\n".join(page.text for page in pages)
    fields["insuredName"] = parse_application_name(pages)

    whole_life = first_match(
        pages,
        [r"(?:^|\n)\s*((?:Survivorship\s+)?Whole\s*Life\s*(?:Legacy\s*)?\d+|WholeLife\d+|WL\s*\d+)\s*\n\s*2\s*\."],
        normalize_plan,
        "C2 value-before-question",
        94,
    )
    term = first_match(
        pages,
        [r"(?:^|\n)\s*(Vantage\s+Term(?:\s+[A-Za-z0-9-]+)*)\s*\n\s*16\s*\."],
        normalize_plan,
        "C16 value-before-question",
        94,
    )
    fields["plan"] = whole_life if whole_life.value else term
    is_term = bool(term.value and not whole_life.value)

    fields["faceAmount"] = first_match(
        pages,
        [
            r"(?:^|\n)\s*\$?\s*([1-9][\d,]*(?:\.\d{2})?)\s*\n\s*17\s*\." if is_term else r"(?:^|\n)\s*\$?\s*([1-9][\d,]*(?:\.\d{2})?)\s*\n\s*3\s*\."
        ],
        normalize_money,
        "C17/C3 value-before-question",
        94,
    )

    term_waiver = first_match(
        pages,
        [r"18\s*\.[^\n]{0,180}"],
        lambda value: "No" if re.search(r"Yes.{0,40}✔\s*(?:No|1R)", value, re.I) else ("Yes" if re.search(r"✔\s*Yes", value, re.I) else ""),
        "C18 checkbox",
        88,
    )
    fields["waiver"] = term_waiver if is_term else first_match(
        pages,
        [r"7\s*\.[^\n]{0,180}"],
        lambda value: "Insured 1" if re.search(r"Insured\s*1", value, re.I) else ("Insured 2" if re.search(r"Insured\s*2", value, re.I) else ""),
        "C7 checkbox",
        80,
    )

    fields["frequency"] = first_match(
        pages,
        [
            r"(?:Frequency|Premium Mode)[^\n]{0,160}?(Monthly|Quarterly|Semi[- ]annual|Annual|Single Premium)",
            r"(?:Monthly|Quarterly|Semi[- ]annual|Annual|Single Premium)\s*(?:\n|$)",
        ],
        normalize_frequency,
        "payment section",
        82,
    )

    if is_term:
        for key in TERM_EXCLUDED_FIELDS:
            fields[key] = field("N/A", 100, source="Term product rule", excluded=True)
    else:
        fields["apl"] = first_match(pages, [r"Automatic Premium Loan[^\n]{0,140}"], parse_yes_no_text, "C4", 80)
        fields["loanRate"] = first_match(pages, [r"Loan rate[^\n]{0,120}?(Adjustable|Fixed)"], clean, "C5", 82)
        fields["dividend"] = first_match(pages, [r"Dividend Option[^\n]{0,180}"], normalize_dividend, "C6", 78)
        fields["gir"] = first_match(pages, [r"Guaranteed Insurability Rider[^\n]{0,100}"], parse_yes_no_text, "C10", 75)
        fields["rtr"] = first_match(pages, [r"Renewable Term Rider[^\n]{0,100}"], parse_yes_no_text, "C11", 75)
        fields["lisrTfa"] = first_match(pages, [r"(?:LISR|Life Insurance Supplement Rider)[\s\S]{0,240}?Face Amount\s*\$?\s*([\d,]+(?:\.\d{2})?)"], normalize_money, "C13a", 80)
        fields["lisrModal"] = first_match(pages, [r"(?:LISR|Life Insurance Supplement Rider)[\s\S]{0,360}?Modal Payment\s*\$?\s*([\d,]+(?:\.\d{2})?)"], normalize_money, "C13b", 80)
        fields["lisrLumpSum"] = first_match(pages, [r"(?:LISR|Life Insurance Supplement Rider)[\s\S]{0,480}?Unscheduled Lump Sum\s*\$?\s*([\d,]+(?:\.\d{2})?)"], normalize_money, "C13c", 80)
        has_lisr = any(fields[key].value for key in ("lisrTfa", "lisrModal", "lisrLumpSum"))
        fields["lisr"] = field("Yes" if has_lisr else "No", 85, source="C13 presence")

    fields["signedDate"] = extract_signature_date(pages)
    fields["signatureEvidence"] = signature_evidence(pages, fields["signedDate"])
    return fields


def parse_yes_no_text(value: Any) -> str:
    text = clean(value)
    if re.search(r"(?:\[x\]|☒|■|✓|✔|\bX\b)\s*Yes|\[(?:APL|WP|GIR|RTR|LISR)\]\s*Y\b", text, re.I):
        return "Yes"
    if re.search(r"(?:\[x\]|☒|■|✓|✔|\bX\b)\s*(?:No|1R)|\[(?:APL|WP|GIR|RTR|LISR)\]\s*N\b", text, re.I):
        return "No"
    return normalize_choice(text)


def line_value(pages: list[PageText], labels: Iterable[str], source_name: str) -> FieldValue:
    for page in pages:
        lines = page_lines(page)
        for index, line in enumerate(lines):
            for label in labels:
                position = line.lower().find(label.lower())
                if position < 0:
                    continue
                value = clean(line[position + len(label) :]).lstrip(":-| ")
                if value:
                    return field(value, 95, page.page_number, source_name, label)
                for nearby in lines[index + 1 : index + 4]:
                    if nearby:
                        return field(nearby, 88, page.page_number, source_name, label)
    return field()


def parse_client_input_summary(summary_pages: list[PageText], all_pages: list[PageText]) -> dict[str, FieldValue]:
    result = {key: field() for key in FIELD_LABELS}
    full_text = "\n".join(page.text for page in all_pages)

    first_name = line_value(summary_pages, ["First Name"], "Client Input Summary")
    last_name = line_value(summary_pages, ["Last Name"], "Client Input Summary")
    result["insuredName"] = field(
        normalize_name(f"{first_name.value} {last_name.value}"),
        98,
        first_name.page_number or last_name.page_number,
        "Client Input Summary",
        "First Name + Last Name",
    )
    product = line_value(summary_pages, ["Product"], "Client Input Summary")
    result["plan"] = field(normalize_plan(product.value), 98, product.page_number, product.source, product.matched_label)

    design = line_value(summary_pages, ["Design"], "Client Input Summary")
    face = line_value(summary_pages, ["Base Face Amount"], "Client Input Summary")
    result["faceAmount"] = field(normalize_money(face.value), 98, face.page_number, face.source, face.matched_label)

    mode = line_value(summary_pages, ["Premium Mode"], "Client Input Summary")
    result["frequency"] = field(normalize_frequency(mode.value), 98, mode.page_number, mode.source, mode.matched_label)

    dividend = line_value(summary_pages, ["Dividend Option"], "Client Input Summary")
    result["dividend"] = field(normalize_dividend(dividend.value, design.value), 98, dividend.page_number or design.page_number, "Client Input Summary", "Dividend Option/Design")

    loan_option = line_value(summary_pages, ["Loan Interest Option"], "Client Input Summary")
    result["apl"] = field("Yes" if re.search(r"Borrow from Policy", loan_option.value, re.I) else "", 96 if loan_option.value else 0, loan_option.page_number, loan_option.source, loan_option.matched_label)

    loan_rate = line_value(summary_pages, ["Loan Rate"], "Client Input Summary")
    loan_rate_value = "Adjustable" if re.search(r"Adjustable", loan_rate.value, re.I) else ("Fixed" if re.search(r"Fixed", loan_rate.value, re.I) else "")
    result["loanRate"] = field(loan_rate_value, 98 if loan_rate_value else 0, loan_rate.page_number, loan_rate.source, loan_rate.matched_label)

    for key, labels in {
        "waiver": ["Waiver of Premium [WP]", "Waiver of Premium"],
        "gir": ["Guaranteed Insurability Rider [GIR]", "Guaranteed Insurability Rider"],
        "rtr": ["Renewable Term Rider [RTR]", "Renewable Term Rider"],
    }.items():
        source_value = line_value(summary_pages, labels, "Client Input Summary")
        result[key] = field(normalize_choice(source_value.value), 98 if source_value.value else 0, source_value.page_number, source_value.source, source_value.matched_label)

    lisr_tfa = line_value(summary_pages, ["LISR Target Face Amount (TFA)", "LISR Target Face Amount"], "Client Input Summary")
    lisr_lump = line_value(summary_pages, ["LISR Lump Sum"], "Client Input Summary")
    result["lisrTfa"] = field(normalize_money(lisr_tfa.value), 98 if lisr_tfa.value else 0, lisr_tfa.page_number, lisr_tfa.source, lisr_tfa.matched_label)
    lump_value = normalize_money(lisr_lump.value)
    if lisr_lump.value and not lump_value and clean(lisr_lump.value) == "0":
        lump_value = "$0"
    result["lisrLumpSum"] = field(lump_value, 98 if lisr_lump.value else 0, lisr_lump.page_number, lisr_lump.source, lisr_lump.matched_label)

    fallback = parse_illustration_fallback(all_pages)
    result["lisrModal"] = fallback["lisrModal"]
    has_lisr = bool(re.search(r"Base\s*\+\s*LISR", design.value, re.I) or result["lisrTfa"].value or re.search(r"LISR/Flex", result["dividend"].value, re.I))
    result["lisr"] = field("Yes" if has_lisr else "No", 98, design.page_number, "Client Input Summary", "Design")

    alir_amount = line_value(summary_pages, ["ALIR Amount"], "Client Input Summary")
    match = re.search(r"1\s+to\s+(\d+)\s*[-:]\s*\$?\s*([\d,]+(?:\.\d{2})?)", alir_amount.value, re.I)
    if match:
        result["alirModal"] = field(normalize_money(match.group(2)), 96, alir_amount.page_number, "Client Input Summary", "ALIR Amount")
        result["alirDuration"] = field(f"{match.group(1)} years", 96, alir_amount.page_number, "Client Input Summary", "ALIR Amount")
    else:
        result["alirModal"] = fallback["alirModal"]
        result["alirDuration"] = fallback["alirDuration"]

    unscheduled = line_value(summary_pages, ["Additional Life Ins. Rider [ALIR] - Unscheduled"], "Client Input Summary")
    result["alirUnscheduled"] = field(normalize_choice(unscheduled.value), 96 if unscheduled.value else 0, unscheduled.page_number, unscheduled.source, unscheduled.matched_label)

    result["signedDate"] = extract_signature_date(all_pages)
    result["signatureEvidence"] = signature_evidence(all_pages, result["signedDate"])
    return result


def parse_illustration_fallback(pages: list[PageText]) -> dict[str, FieldValue]:
    result = {key: field() for key in FIELD_LABELS}
    full_text = "\n".join(page.text for page in pages)

    prepared = first_match(
        pages,
        [r"Prepared for\s+([^\n]{2,100})", r"Client Information[\s\S]{0,180}?Prepared for\s+([^\n]{2,100})"],
        normalize_name,
        "Narrative/Client Information",
        92,
    )
    result["insuredName"] = prepared

    policy = line_value(pages, ["Policy", "Product"], "Narrative/Client Information")
    result["plan"] = field(normalize_plan(policy.value), 90 if policy.value else 0, policy.page_number, policy.source, policy.matched_label)

    face = line_value(pages, ["Base Policy Face Amount", "Base Face Amount"], "Initial Coverage Information")
    result["faceAmount"] = field(normalize_money(face.value), 95 if face.value else 0, face.page_number, face.source, face.matched_label)

    mode = line_value(pages, ["Premium Payment Mode", "Premium Mode"], "Initial Premium Information")
    result["frequency"] = field(normalize_frequency(mode.value), 94 if mode.value else 0, mode.page_number, mode.source, mode.matched_label)

    dividend = line_value(pages, ["Initial Base Dividend Option", "Initial Dividend Option"], "Policy Information")
    result["dividend"] = field(normalize_dividend(dividend.value, full_text), 94 if dividend.value else 0, dividend.page_number, dividend.source, dividend.matched_label)

    lisr_modal = line_value(pages, ["LISR Premium First Year"], "Initial Premium Information")
    lisr_tfa = line_value(pages, ["LISR Target Face Amount (TFA)", "LISR Target Face Amount"], "Initial Coverage Information")
    result["lisrModal"] = field(normalize_money(lisr_modal.value), 94 if lisr_modal.value else 0, lisr_modal.page_number, lisr_modal.source, lisr_modal.matched_label)
    result["lisrTfa"] = field(normalize_money(lisr_tfa.value), 94 if lisr_tfa.value else 0, lisr_tfa.page_number, lisr_tfa.source, lisr_tfa.matched_label)
    has_lisr = bool(re.search(r"Base\s*\+\s*LISR|LISR/Flex|LISR Premium First Year|LISR Target Face Amount", full_text, re.I))
    result["lisr"] = field("Yes" if has_lisr else "No", 92, lisr_tfa.page_number, "Illustration fallback", "LISR indicators")

    if re.search(r"This rate may change on each policy anniversary date", full_text, re.I):
        result["loanRate"] = field("Adjustable", 92, source="Policy loan disclosure", matched_label="rate may change")
    elif re.search(r"\bfixed\b.{0,50}loan interest rate", full_text, re.I):
        result["loanRate"] = field("Fixed", 90, source="Policy loan disclosure")

    if re.search(r"Borrow from Policy", full_text, re.I):
        result["apl"] = field("Yes", 92, source="Loan Interest Option", matched_label="Borrow from Policy")

    alir_match = re.search(
        r"initial payment of\s*\$?\s*([\d,]+(?:\.\d{2})?)\s*(monthly|quarterly|semi[- ]annual|annual)?\s*for\s*(\d+)\s*years?\s*under the ALIR",
        full_text,
        re.I,
    )
    if alir_match:
        result["alirModal"] = field(normalize_money(alir_match.group(1)), 94, source="Additional Information About This Illustration")
        result["alirDuration"] = field(f"{alir_match.group(3)} years", 94, source="Additional Information About This Illustration")

    result["waiver"] = first_match(pages, [r"Waiver of Premium\s*\[WP\]\s*([YN]|Yes|No)"], normalize_choice, "Illustration rider listing", 90)
    result["gir"] = first_match(pages, [r"Guaranteed Insurability Rider\s*\[GIR\]\s*([YN]|Yes|No)"], normalize_choice, "Illustration rider listing", 90)
    result["rtr"] = first_match(pages, [r"Renewable Term Rider\s*\[RTR\]\s*([YN]|Yes|No)"], normalize_choice, "Illustration rider listing", 90)
    return result


def parse_illustration(pages: list[PageText]) -> dict[str, FieldValue]:
    summary_pages = [page for page in pages if re.search(r"Client Input Summary", page.text, re.I)]
    if summary_pages:
        LOGGER.info("Illustration source: Client Input Summary with narrative fallbacks")
        return parse_client_input_summary(summary_pages, pages)
    LOGGER.info("Illustration source: narrative/policy information fallbacks")
    result = parse_illustration_fallback(pages)
    result["signedDate"] = extract_signature_date(pages)
    result["signatureEvidence"] = signature_evidence(pages, result["signedDate"])
    return result


def extract_signature_date(pages: list[PageText]) -> FieldValue:
    return first_match(
        pages,
        [
            r"(?:Signature of Proposed Insured|Proposed Insured Signature)[^\n]{0,200}?Date\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
            r"(?:Signature of Owner|Owner Signature)[^\n]{0,200}?Date\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        ],
        clean,
        "signature date",
        94,
    )


def signature_evidence(pages: list[PageText], signed_date: FieldValue) -> FieldValue:
    text = "\n".join(page.text for page in pages)
    electronic = bool(re.search(r"DocuSign Envelope ID|Adobe Sign|electronically signed|digital signature", text, re.I))
    if electronic:
        return field("Signed", 96, source="electronic signature evidence")
    if signed_date.value and parse_date(signed_date.value):
        return field("Signed", 94, signed_date.page_number, "completed signature date")
    return field("Unsigned", 95, source="no electronic signature or completed signature date")


def parse_document(pdf_path: str, expected_type: str, max_pages: int = 25) -> DocumentResult:
    result = DocumentResult(pdf_path, expected_type, "unknown", {}, [])
    try:
        result.pages = extract_pages(pdf_path, max_pages=max_pages)
        result.detected_type = detect_document_type(result.pages)
        if result.detected_type != "unknown" and result.detected_type != expected_type:
            result.errors.append(
                f"Wrong file type: expected {expected_type}, detected {result.detected_type}"
            )
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
        return [
            ComparisonRow(
                "SYSTEM",
                "System",
                "; ".join(app.errors) or "Extraction failed",
                "; ".join(ill.errors) or "Extraction failed",
                "Failed",
                "One or both documents could not be validated.",
            )
        ]

    rows: list[ComparisonRow] = []
    app_plan = app.fields.get("plan", field()).value
    is_term = bool(re.match(r"^Vantage\s+Term\b", app_plan, re.I))

    for key in COMPARISON_FIELDS:
        app_field = app.fields.get(key, field())
        ill_field = ill.fields.get(key, field())

        if is_term and key in TERM_EXCLUDED_FIELDS:
            rows.append(
                ComparisonRow(key, FIELD_LABELS.get(key, key), "N/A", "N/A", "Excluded", "Not applicable for Vantage Term.")
            )
            continue

        if key.startswith("lisr") and key != "lisr" and normalized(app.fields.get("lisr", field()).value) != "yes":
            rows.append(
                ComparisonRow(key, FIELD_LABELS.get(key, key), "N/A", "N/A", "Excluded", "No LISR on the application.")
            )
            continue

        if not app_field.value or not ill_field.value:
            status = "Review"
            note = "Could not extract a value from one or both documents."
        elif normalized(app_field.value) == normalized(ill_field.value):
            status = "Match"
            note = "Values match."
        else:
            status = "Mismatch"
            note = "Values differ."

        rows.append(
            ComparisonRow(
                key,
                FIELD_LABELS.get(key, key),
                app_field.value or "Not Found",
                ill_field.value or "Not Found",
                status,
                note,
            )
        )

    if state.upper() == "NY":
        app_date_value = app.fields.get("signedDate", field()).value
        ill_date_value = ill.fields.get("signedDate", field()).value
        app_date = parse_date(app_date_value)
        ill_date = parse_date(ill_date_value)
        if not app_date or not ill_date:
            rows.append(
                ComparisonRow(
                    "nySignatureTiming",
                    "NY signature timing",
                    app_date_value or "Not Found",
                    ill_date_value or "Not Found",
                    "Review",
                    "Could not parse one or both signature dates for Reg 74 validation.",
                )
            )
        elif ill_date > app_date:
            rows.append(
                ComparisonRow(
                    "nySignatureTiming",
                    "NY signature timing",
                    app_date_value,
                    ill_date_value,
                    "Mismatch",
                    "Illustration signature date is after the Part 1 signature date.",
                )
            )
        else:
            rows.append(
                ComparisonRow(
                    "nySignatureTiming",
                    "NY signature timing",
                    app_date_value,
                    ill_date_value,
                    "Match",
                    "Illustration was signed on or before the Part 1 date.",
                )
            )

    return rows


def serializable_document(document: DocumentResult, include_text: bool = False) -> dict[str, Any]:
    payload = asdict(document)
    if not include_text:
        for page in payload["pages"]:
            page.pop("text", None)
    return payload


def build_report(
    app: DocumentResult,
    ill: DocumentResult,
    rows: list[ComparisonRow],
    state: str,
    include_text: bool,
) -> dict[str, Any]:
    return {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "state": state.upper(),
        "application": serializable_document(app, include_text),
        "illustration": serializable_document(ill, include_text),
        "comparison": [asdict(row) for row in rows],
        "summary": {
            "matches": sum(row.status == "Match" for row in rows),
            "mismatches": sum(row.status == "Mismatch" for row in rows),
            "reviews": sum(row.status == "Review" for row in rows),
            "excluded": sum(row.status == "Excluded" for row in rows),
            "failed": sum(row.status == "Failed" for row in rows),
        },
        "runtime": {
            "tesseractAvailable": shutil.which("tesseract") is not None,
            "popplerAvailable": shutil.which("pdftoppm") is not None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Part 1 application against a sales illustration")
    parser.add_argument("application", help="Path to the Part 1 application PDF")
    parser.add_argument("illustration", help="Path to the sales illustration PDF")
    parser.add_argument("--state", default="", help="Contract state, such as NY")
    parser.add_argument("--output", default="validation-report.json", help="JSON report path")
    parser.add_argument("--max-pages", type=int, default=25, help="Maximum pages per PDF")
    parser.add_argument("--include-text", action="store_true", help="Include extracted PDF text in JSON output")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    app = parse_document(args.application, "app", args.max_pages)
    ill = parse_document(args.illustration, "ill", args.max_pages)
    rows = compare_documents(app, ill, args.state)
    report = build_report(app, ill, rows, args.state, args.include_text)

    output = Path(args.output)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"Report written to {output}")
    return 1 if report["summary"]["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())

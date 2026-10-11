"""Decide where Memobrick files in the intake folders belong.

Each item gets a destination under the business folder, a confidence, and a
plain-language reason. Anything uncertain gets no destination and goes to the
"needs your decision" list instead of being moved somewhere wrong.

Text is peeked locally from PDF, Word, Excel, PowerPoint, CSV and text files
(first few thousand characters only). Nothing leaves the Mac.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

A = "A Brand & Identity"
B = "B Product Development"
C = "C Website & Technology"
D = "D Marketing"
E = "E Social Media"
F = "F Creative Assets"
G = "G Customers & Orders"
H = "H Operations"
I = "I Finance & Legal"

HIGH, MEDIUM = 0.85, 0.6
PEEK_CHARS = 20000
PEEK_MAX_BYTES = 25 * 1024 * 1024


@dataclass
class Decision:
    dest: str          # relative to the business folder; "" = needs your decision
    confidence: float
    reason: str


REVIEW = lambda why: Decision("", 0.0, why)  # noqa: E731


# --- local text peeking -------------------------------------------------------

def _strip_xml(data: bytes) -> str:
    return re.sub(r"<[^>]+>", " ", data.decode("utf-8", "ignore"))


def _peek_office(path: str) -> str:
    parts = ("word/document.xml", "xl/sharedStrings.xml", "ppt/slides/slide1.xml",
             "ppt/slides/slide2.xml")
    out = []
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        for p in parts:
            if p in names:
                out.append(_strip_xml(z.read(p)[: PEEK_CHARS * 4]))
    return " ".join(out)


_PDF_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
_PDF_TEXT = re.compile(rb"\(((?:\\.|[^\\()])*)\)")


def _peek_pdf(path: str) -> str:
    with open(path, "rb") as f:
        data = f.read(min(os.path.getsize(path), 5 * 1024 * 1024))
    chunks = []
    for i, m in enumerate(_PDF_STREAM.finditer(data)):
        if i > 40:
            break
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        chunks.extend(_PDF_TEXT.findall(raw))
        if sum(map(len, chunks)) > PEEK_CHARS:
            break
    # Metadata (Title/Subject/Producer) often names the document too.
    chunks.extend(re.findall(rb"/(?:Title|Subject|Creator|Producer)\s*\(([^)]{0,200})\)", data))
    return b" ".join(chunks).decode("latin-1", "ignore")


def peek_text(path: str, ext: str, size: int) -> str:
    if size > PEEK_MAX_BYTES:
        return ""
    try:
        if ext in (".docx", ".xlsx", ".pptx"):
            text = _peek_office(path)
        elif ext == ".pdf":
            text = _peek_pdf(path)
        elif ext in (".csv", ".tsv", ".txt", ".md", ".html", ".htm", ".rtf"):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read(PEEK_CHARS)
        else:
            return ""
    except (OSError, zipfile.BadZipFile, ValueError, KeyError):
        return ""
    return re.sub(r"\s+", " ", text)[:PEEK_CHARS].lower()


# --- rules ------------------------------------------------------------------

def _has(text: str, *patterns: str) -> Optional[str]:
    """First pattern found as a whole word; returns the text that matched."""
    for pat in patterns:
        m = re.search(r"(?<![a-z])(?:" + pat + r")(?![a-z])", text)
        if m:
            return re.sub(r"[\s_\-]+", " ", m.group(0))
    return None


def _year(mtime: float) -> str:
    return str(dt.datetime.fromtimestamp(mtime).year)


# Customer upload IDs look like "S-6SUKU6NGJR": capital S, 8+ capitals/digits, a digit.
CUSTOMER_ID = re.compile(r"^(S-(?=[A-Z0-9]*\d)[A-Z0-9]{8,})(?![a-z])")
AI_TOOLS = (r"dall[·\-\s_]?e", "dalle", "midjourney", r"chatgpt[\s_\-]*image",
            r"gemini[\s_\-]*generated", "firefly", "leonardo", "ideogram", "runway",
            "kling", "sora", "flux", r"stable[\s_\-]*diffusion", "magnific")


def classify_file(row, text: str, loose_photos_are_business: bool) -> Decision:
    """row: an index row (name, kind, ext, mtime, business)."""
    name = row["name"]
    n = name.lower()
    kind = row["kind"]
    is_media = kind in ("image", "video")

    m = CUSTOMER_ID.match(name)
    if m and kind in ("image", "pdf", "design"):
        oid = m.group(1).upper()
        return Decision(f"{G}/Customer Photos/{oid}", 0.9,
                        f"customer upload (ID {oid} in the name)")

    if _has(n, r"orders?[\s_\-]*export"):
        return Decision(f"{G}/Order Exports", 0.95, "Shopify order export")
    if _has(n, r"customers?[\s_\-]*export"):
        return Decision(f"{G}/Customer Lists", 0.95, "Shopify customer export")
    if _has(n, r"products?[\s_\-]*export", r"inventory[\s_\-]*export"):
        return Decision(f"{C}/Shopify Exports", 0.9, "Shopify product export")
    if _has(n, r"theme[\s_\-]*export") or (kind in ("archive", "code") and _has(n, "theme")
                                           and _has(n, "shopify", "dawn", "memobrick")):
        return Decision(f"{C}/Shopify Themes", 0.9, "Shopify theme file")
    if kind in ("archive", "code") and _has(n, "memobrick", "editor", "studio", "mosaic"):
        return Decision(f"{C}/Editor & Studio Builds", 0.85, "Memobrick editor/studio build")
    if kind == "installer":
        return REVIEW("installer — covered by the installers clean-up")
    if kind == "archive":
        return REVIEW("compressed archive — covered by the archives clean-up")

    if kind == "font":
        return Decision(f"{A}/Fonts", 0.85, "font file")
    if w := _has(n, "logo", "logos", "logotipo", "isotipo"):
        return Decision(f"{A}/Logos", 0.9, f"'{w}' in the name")
    if w := _has(n, r"brand[\s_\-]*(guide|guidelines|book|kit)", r"style[\s_\-]*guide"):
        return Decision(f"{A}/Brand Guidelines", 0.9, "brand guidelines")

    if w := _has(n, "invoice", "invoices", "factura", "receipt", "recibo", "bill"):
        return Decision(f"{I}/Invoices & Receipts", 0.85, f"'{w}' in the name")
    if w := _has(n, "contract", "agreement", "nda", "contrato", "trademark", "license"):
        return Decision(f"{I}/Contracts & Legal", 0.8, f"'{w}' in the name")

    if w := _has(n, r"packing[\s_\-]*slip", "tracking", r"shipping", "usps", "fedex", "dhl"):
        return Decision(f"{H}/Shipping", 0.85, f"'{w}' in the name")
    if w := _has(n, "inventory", "stock", "inventario"):
        return Decision(f"{H}/Inventory", 0.85, f"'{w}' in the name")
    if w := _has(n, "supplier", "suppliers", "alibaba", r"purchase[\s_\-]*order", "quote",
                 "quotation", "cotizacion", "proforma"):
        return Decision(f"{H}/Suppliers & Purchasing", 0.85, f"'{w}' in the name")
    if w := _has(n, "sop", "procedure", "checklist", "fulfillment", "fulfilment"):
        return Decision(f"{H}/Procedures", 0.8, f"'{w}' in the name")

    if w := _has(n, "instructions?", "assembly", "manual", "guia", "booklet", r"how[\s_\-]*to[\s_\-]*build"):
        return Decision(f"{B}/Assembly Instructions", 0.85, f"'{w}' in the name")
    if w := _has(n, "mockups?", r"mock[\s_\-]*up"):
        return Decision(f"{B}/Mockups", 0.85, f"'{w}' in the name")
    if w := _has(n, "packaging", "box", "insert", "sticker", "stickers", "dieline"):
        return Decision(f"{B}/Packaging", 0.8, f"'{w}' in the name")
    if w := _has(n, "palette", r"colou?r[\s_\-]*chart", r"brick[\s_\-]*colou?rs?", "bricklink",
                 "ldraw", r"parts?[\s_\-]*list", "baseplates?", r"brick[\s_\-]*codes?"):
        return Decision(f"{B}/Brick Colors & Parts", 0.85, f"'{w}' in the name")

    if is_media and (w := _has(n, *AI_TOOLS)):
        return Decision(f"{F}/AI-Generated", 0.85, f"made with {w}")
    if w := _has(n, "reels?", "tiktok", "stories", "story", r"ig[\s_\-]*post", "instagram",
                 "youtube", "shorts", "captions?", r"content[\s_\-]*calendar"):
        sub = "Content Calendar & Captions" if kind in ("document", "spreadsheet", "pdf") else "Posts & Videos"
        return Decision(f"{E}/{sub}", 0.8, f"'{w}' in the name")
    if w := _has(n, "ads?", "creative", "creatives", "campaign", "promo", "discount", "sale",
                 r"black[\s_\-]*friday", "christmas", "holiday", "navidad", r"mother'?s[\s_\-]*day",
                 r"valentine'?s?", r"early[\s_\-]*bird"):
        if is_media:
            return Decision(f"{F}/Ad Creatives", 0.8, f"'{w}' in the name")
        return Decision(f"{D}/Campaigns", 0.8, f"'{w}' in the name")
    if w := _has(n, "analytics", "report", r"marketing[\s_\-]*plan", "strategy"):
        if kind in ("pdf", "document", "spreadsheet", "presentation"):
            return Decision(f"{D}/Strategy & Reports", 0.75, f"'{w}' in the name")

    # Content clues for documents whose names don't say much.
    if text:
        if _has(text, r"let'?s build your memobrick", "step 1", r"assembly instructions"):
            return Decision(f"{B}/Assembly Instructions", 0.75, "the document reads like instructions")
        if _has(text, "invoice", "factura", r"amount due", r"total due") and _has(text, "total"):
            return Decision(f"{I}/Invoices & Receipts", 0.75, "the document reads like an invoice")
        if _has(text, "usps", r"postage paid", "fedex", "tracking number"):
            return Decision(f"{H}/Shipping", 0.75, "the document mentions postage/tracking")

    if kind == "design" and row["business"]:
        return Decision(f"{F}/Design Files", 0.75, "design file with Memobrick in its path")
    if kind == "image" and _has(n, r"untitled[\s_\-]*design", r"copy[\s_\-]*of"):
        return Decision(f"{F}/Designs (Canva)", 0.7, "Canva-style export")

    if kind == "image" and loose_photos_are_business:
        return Decision(f"{F}/Photos/{_year(row['mtime'])}", 0.65,
                        "loose photo (personal photos live in Apple Photos)")
    if kind == "video" and row["business"]:
        return Decision(f"{F}/Videos/{_year(row['mtime'])}", 0.65, "video with Memobrick in its path")

    if row["business"]:
        return REVIEW("mentions Memobrick, but what kind of file it is isn't clear")
    return REVIEW("doesn't look like a Memobrick file")


def classify_folder(name: str, child_kinds: Counter, is_project: bool, business: bool,
                    mtime: float, loose_photos_are_business: bool) -> Decision:
    """A top-level folder in an intake folder, moved as one unit."""
    n = name.lower()
    m = CUSTOMER_ID.match(name)
    if m:
        return Decision(f"{G}/Customer Photos/{m.group(1).upper()}", 0.9,
                        "customer upload folder (ID in the name)")
    if is_project or child_kinds.get("code", 0) > 0:
        if _has(n, "memobrick", "editor", "studio", "mosaic", "theme", "shopify"):
            return Decision(f"{C}/Editor & Studio Builds", 0.85, "Memobrick code/build folder")
        return REVIEW("software folder — not touched without your say-so")
    total = sum(child_kinds.values()) or 1
    media = child_kinds.get("image", 0) + child_kinds.get("video", 0)
    if _has(n, *AI_TOOLS):
        return Decision(f"{F}/AI-Generated", 0.8, "AI image folder")
    if _has(n, "logo", "logos", "brand"):
        return Decision(f"{A}/Logos", 0.8, "logo/brand folder")
    if _has(n, "ads?", "campaign", "creatives?", "promo"):
        return Decision(f"{F}/Ad Creatives", 0.8, "ad creatives folder")
    if media / total >= 0.8 and (business or loose_photos_are_business):
        return Decision(f"{F}/Photos/{_year(mtime)}", 0.65,
                        f"folder of photos/videos ({media} of {total} files)")
    if business:
        return REVIEW("Memobrick folder with mixed contents")
    return REVIEW("doesn't look like a Memobrick folder")


Peek = Callable[[str, str, int], str]


def summarize_reasons(decisions: List[Decision]) -> Dict[str, int]:
    return dict(Counter(d.dest.split("/")[0] if d.dest else "review" for d in decisions))

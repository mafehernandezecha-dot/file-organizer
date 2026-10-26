"""Homes for everything that isn't a Memobrick file.

- Personal documents → 02 PERSONAL DOCUMENTS/<section>, recognized by name or
  by text peeked locally from the document. Sensitive by nature: nothing is
  uploaded, and the folder is never shared by this tool.
- Anything else from Downloads/Desktop → 05 INTAKE/<type>, so the intake
  folders end up empty and nothing is lost in an uncertain place.

Destinations here start with "@personal/" or "@intake/"; sorting.py maps them
to the configured folders.
"""

from __future__ import annotations

from typing import Optional

from organizer.classify import Decision, _has

PERSONAL = "@personal/"
INTAKE = "@intake/"

ID = "Identification & Official Documents"
IMM = "Immigration & Visas"
TRAVEL = "Travel Documents"
FAMILY = "Family Documents"
HOME = "Home & Household"
INS = "Insurance"
BANK = "Banking & Finances"
EDU = "Education & Hebrew Studies"
MED = "Medical Records"
RCPT = "Receipts & Warranties"
PROJ = "Personal Projects"

# (section, name patterns, text patterns). Order matters: first match wins.
RULES = [
    (IMM, [r"visas?", "uscis", r"i[\s_\-]?797", r"i[\s_\-]?485", r"i[\s_\-]?130", r"i[\s_\-]?765",
           r"i[\s_\-]?94", r"green[\s_\-]*card", "ead", "immigration", "inmigracion", "residency",
           "residencia", "aliyah", r"teudat", r"misrad[\s_\-]*hapnim", "nefesh", r"work[\s_\-]*permit",
           "naturalization", "citizenship", "ciudadania", "apostille", "apostilla"],
     ["uscis", r"department of homeland security", "receipt notice", "approval notice",
      r"misrad hapnim", r"population and immigration"]),
    (ID, ["passport", "pasaporte", r"id[\s_\-]*card", "cedula", "dni", "teudat zehut",
          r"driver'?s?[\s_\-]*licen[cs]e", r"licencia[\s_\-]*de[\s_\-]*conducir", r"social[\s_\-]*security",
          r"ssn", r"birth[\s_\-]*certificate", r"acta[\s_\-]*de[\s_\-]*nacimiento", "registro civil"],
     [r"passport no", r"date of expiry", r"nationality", r"driver license", r"social security number"]),
    (FAMILY, ["wedding", "boda", "chuppah", r"marriage", "matrimonio", "divorce", "divorcio", r"custody", r"family[\s_\-]*tree",
              r"ketubah"], []),
    (TRAVEL, [r"boarding[\s_\-]*pass", "itinerary", "itinerario", r"e[\s_\-]*ticket", "flight",
              "vuelo", "booking", "reservation", "reserva", "hotel", "airbnb", r"esta[\s_\-]*application",
              "elal", r"el[\s_\-]*al", "avianca", "ryanair"],
     [r"boarding pass", r"booking confirmation", r"flight number", r"check[- ]in", "itinerary"]),
    (MED, ["medical", "medico", r"lab[\s_\-]*results", r"blood[\s_\-]*test",
           "prescription", "receta", "doctor", "hospital", "clinic", "clinica", "vaccine",
           "vaccination", "vacuna", "dental", "dentist", "kupat", "maccabi", "clalit",
           "meuhedet", "leumit", "mri", r"x[\s_\-]*ray", "ultrasound"],
     [r"patient name", "diagnosis", "prescription", r"lab results", r"date of birth.*physician"]),
    (INS, ["insurance", "seguro", r"insurance[\s_\-]*policy", "poliza", r"insurance[\s_\-]*claim", r"bituach"],
     [r"policy number", "insured", "premium", "coverage"]),
    (BANK, [r"bank[\s_\-]*statement", "statement", r"estado[\s_\-]*de[\s_\-]*cuenta", "tax", "taxes",
            "impuestos", r"w[\s_\-]?2", r"w[\s_\-]?9", r"1099", r"1040", "irs", r"credit[\s_\-]*card",
            "loan", "prestamo", "pension", "payslip", "paystub", r"pay[\s_\-]*stub", "salary",
            "nomina", "tlush", "mas hachnasa"],
     [r"account number", r"statement period", r"opening balance", r"internal revenue service",
      r"form 1040", r"wage and tax statement"]),
    (HOME, ["lease", r"rental[\s_\-]*agreement", "rent", "arriendo", "alquiler", "mortgage",
            "hipoteca", "utility", r"electric(ity)?[\s_\-]*bill", r"water[\s_\-]*bill",
            "landlord", "arnona", "vaad bayit", "mudanza"],
     [r"lease agreement", "landlord", "tenant", r"monthly rent"]),
    (EDU, ["hebrew", "ivrit", "ulpan", "עברית", "אולפן", "diploma", "transcript", "certificate",
           "certificado", "course", "curso", "homework", "tarea", "university",
           "universidad", "school", "colegio", "exam", "examen", "grammar"],
     ["hebrew", "ulpan", r"transcript of records", "diploma", "homework"]),
    (RCPT, ["warranty", "garantia", "guarantee", r"user[\s_\-]*guide", r"owner'?s[\s_\-]*manual",
            r"order[\s_\-]*confirmation", "amazon", "apple store", "bestbuy"],
     ["warranty", r"order confirmation", r"order #"]),
    ("Contracts & Agreements", ["contract", "contrato", "agreement", "acuerdo", "nda",
                                r"terms[\s_\-]*(and|&)[\s_\-]*conditions"],
     [r"this agreement", r"the parties agree"]),
    (PROJ, ["resume", "cv", "curriculum", r"cover[\s_\-]*letter", "portfolio", "diary",
            "recipe"],
     [r"work experience", r"professional summary"]),
]

DOC_KINDS = ("pdf", "document", "spreadsheet", "presentation", "image", "email", "other")


def classify_personal(row, text: str) -> Optional[Decision]:
    """Personal document section, or None if nothing points to one."""
    if row["kind"] not in DOC_KINDS:
        return None
    n = row["name"].lower()
    for section, name_pats, text_pats in RULES:
        if w := _has(n, *name_pats):
            return Decision(f"{PERSONAL}{section}", 0.8, f"'{w}' in the name")
    if text:
        for section, _, text_pats in RULES:
            if text_pats and (w := _has(text, *text_pats)):
                return Decision(f"{PERSONAL}{section}", 0.7, f"the document mentions '{w}'")
    return None


INTAKE_BY_KIND = {
    "pdf": "Documents to Review", "document": "Documents to Review",
    "spreadsheet": "Documents to Review", "presentation": "Documents to Review",
    "email": "Documents to Review",
    "image": "Photos", "video": "Videos", "audio": "Audio",
    "archive": "Compressed Files", "installer": "Installers",
    "screenshot": "Photos", "screen_recording": "Videos",
}


def intake_file(row, business_hint: bool = False) -> Decision:
    if business_hint:
        return Decision(f"{INTAKE}Memobrick to Sort", 0.6,
                        "mentions Memobrick, but which folder isn't clear")
    sub = INTAKE_BY_KIND.get(row["kind"], "Needs Manual Review")
    return Decision(f"{INTAKE}{sub}", 0.6, "no clear home yet — parked for you to look at")


def intake_folder(business: bool) -> Decision:
    if business:
        return Decision(f"{INTAKE}Memobrick to Sort", 0.6, "Memobrick folder with mixed contents")
    return Decision(f"{INTAKE}Folders to Review", 0.6, "folder with no clear home yet")

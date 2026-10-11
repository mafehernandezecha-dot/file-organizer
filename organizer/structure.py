"""Create the full folder structure from the organization plan (empty folders
only; existing folders and files are never touched). Undo removes the
folders it created, if they are still empty.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Dict, List

from organizer.config import Config
from organizer.personal import (BANK, EDU, FAMILY, HOME, ID, IMM, INS, MED, PROJ, RCPT,
                                TRAVEL)

BUSINESS: Dict[str, List[str]] = {
    "A Brand & Identity": ["Logos & Graphics", "Brand Guidelines", "Brand Colors",
                           "Product Descriptions", "Brand Photography", "Fonts"],
    "B Product Development": ["Mosaic Designs", "Product Sizes", "Brick Colors & Parts",
                              "Baseplates", "Assembly Instructions", "Packaging", "Mockups",
                              "Product Photography"],
    "C Website & Technology": ["Shopify Themes", "Shopify Exports", "Website Backups",
                               "Memobrick Studio", "Editor & Studio Builds", "Source Code",
                               "Development Documents", "API Documentation", "Inventory System",
                               "Technical Assets"],
    "D Marketing": ["Facebook Ads", "Instagram Ads", "TikTok Ads", "Google Ads",
                    "Email Marketing", "Campaigns", "Holiday Campaigns", "Discount Promotions",
                    "Strategy & Reports"],
    "E Social Media": ["Instagram Posts", "Instagram Reels", "TikTok Videos", "Facebook Content",
                       "YouTube Content", "Stories", "Content Calendar & Captions",
                       "Ready to Publish", "Published", "Posts & Videos"],
    "F Creative Assets": ["Photos", "Videos", "Product Photos", "Photos with People",
                          "Ad Creatives", "AI-Generated", "Graphics & Flyers", "Designs (Canva)",
                          "Design Files", "Customer Testimonials", "Exported Content"],
    "G Customers & Orders": ["Customer Photos", "Design Proofs", "Approved Designs",
                             "Order Documentation", "Order Exports", "Customer Lists",
                             "Custom Projects", "Corporate Orders", "Wedding Mosaics",
                             "Customer Correspondence"],
    "H Operations": ["Inventory", "Suppliers & Purchasing", "Shipping", "Packaging Supplies",
                     "Production", "Fulfillment", "Procedures"],
    "I Finance & Legal": ["Invoices & Receipts", "Expenses", "Sales Reports", "Accounting",
                          "Contracts & Legal", "Business Registrations", "Intellectual Property",
                          "Marketplace Compliance"],
    "J Archive": ["Previous Campaigns", "Retired Designs", "Old Product Versions",
                  "Historical Reports", "Completed Projects"],
}
PERSONAL = [ID, IMM, TRAVEL, FAMILY, HOME, INS, BANK, EDU, MED, RCPT, PROJ, "Archive"]
INTAKE = ["Documents to Review", "Photos", "Videos", "Audio", "Compressed Files", "Installers",
          "Memobrick to Sort", "Folders to Review", "Needs Manual Review"]


def wanted_folders(cfg: Config) -> List[Path]:
    out = [cfg.business_root]
    for section, subs in BUSINESS.items():
        out.append(cfg.business_root / section)
        out.extend(cfg.business_root / section / s for s in subs)
    out.append(cfg.personal_root)
    out.extend(cfg.personal_root / s for s in PERSONAL)
    out.append(cfg.intake_root)
    out.extend(cfg.intake_root / s for s in INTAKE)
    return out


def missing_folders(cfg: Config) -> List[Path]:
    return [p for p in wanted_folders(cfg) if not p.exists()]


def create(folders: List[Path], journal: Path) -> int:
    journal.parent.mkdir(parents=True, exist_ok=True)
    made = 0
    with open(journal, "a", encoding="utf-8") as j:
        os.chmod(journal, 0o600)
        for p in folders:  # parents come before children in wanted_folders()
            if p.exists():
                continue
            p.mkdir()
            made += 1
            j.write(json.dumps({"status": "created_dir", "path": str(p), "at": time.time()}) + "\n")
    return made


def undo_create(entries: List[Dict], say: Callable[[str], None] = print) -> Dict[str, int]:
    counts = {"restored": 0, "failed": 0}
    dirs = [e["path"] for e in entries if e.get("status") == "created_dir"]
    for d in sorted(dirs, key=len, reverse=True):  # deepest first
        try:
            os.rmdir(d)  # only succeeds if empty
            counts["restored"] += 1
        except FileNotFoundError:
            pass
        except OSError:
            counts["failed"] += 1
            say(f"! Kept {d} — it has files in it now.")
    return counts

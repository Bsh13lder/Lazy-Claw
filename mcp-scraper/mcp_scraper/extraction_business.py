"""Structured business-info extractor.

Solves the "8 of 10 addresses wrong" bug by parsing schema.org JSON-LD —
the structured data Google Search Console requires every legitimate
business page to publish — instead of regex-ing over the document body
(which pulls phone/email/address fragments from cookie banners, footer
"registered office" blocks, and unrelated nav links).

Extraction priority:
  1. ``<script type="application/ld+json">`` LocalBusiness / Organization /
     Restaurant / Store / Hotel / MedicalBusiness / etc. with nested
     PostalAddress + ContactPoint  →  confidence: high
  2. ``<address>`` HTML tag content                                →  confidence: medium
  3. tel: / mailto: links + visually-near address-shaped text      →  confidence: low
  4. Nothing                                                        →  confidence: none

Regex on the full document body is deliberately NOT used — that is the
soup-pollution bug. If JSON-LD says nothing and ``<address>`` says
nothing and there are no tel:/mailto: links, we return empty rather
than fabricating data from random text matches.

Pure Python, no external libs beyond ``html.parser`` (stdlib). No
scrapling import — JSON-LD parsing is a W3C-standard pattern.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any


# Schema.org "@type" values that indicate a business / place / org. List
# is built from https://schema.org/LocalBusiness subtypes — mirroring
# Google's structured-data eligibility list.
_BUSINESS_TYPES = frozenset({
    "LocalBusiness", "Organization", "Corporation", "Restaurant", "Store",
    "Hotel", "Motel", "BedAndBreakfast", "Lodging",
    "Cafe", "CoffeeShop", "Bakery", "FastFoodRestaurant", "FoodEstablishment",
    "BarOrPub", "Brewery", "Winery", "Bar", "NightClub",
    "MedicalBusiness", "Dentist", "Physician", "MedicalClinic", "Hospital",
    "Pharmacy", "VeterinaryCare", "HealthAndBeautyBusiness",
    "BeautySalon", "HairSalon", "DaySpa", "NailSalon",
    "AutoDealer", "AutoRepair", "GasStation",
    "ProfessionalService", "FinancialService", "AccountingService",
    "LegalService", "Attorney", "InsuranceAgency", "RealEstateAgent",
    "TravelAgency", "Plumber", "Electrician", "RoofingContractor",
    "HousePainter", "MovingCompany", "GeneralContractor",
    "Florist", "ClothingStore", "JewelryStore", "ShoeStore", "BookStore",
    "ComputerStore", "ElectronicsStore", "FurnitureStore", "HardwareStore",
    "HomeGoodsStore", "MobilePhoneStore", "MusicStore", "OfficeEquipmentStore",
    "PetStore", "SportingGoodsStore", "ToyStore", "WholesaleStore",
    "ConvenienceStore", "DepartmentStore", "GroceryStore", "Supermarket",
    "ShoppingCenter",
    "ChildCare", "PreSchool", "School", "EducationalOrganization",
    "FitnessCenter", "GymClass", "Gym", "ExerciseGym",
    "Park", "TouristAttraction", "Place",
})

# Confidence levels — exposed as strings so tool-using LLMs can branch on them.
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_NONE = "none"


@dataclass
class BusinessInfo:
    name: str = ""
    address_street: str = ""
    address_city: str = ""
    address_region: str = ""
    address_postal: str = ""
    address_country: str = ""
    address_full: str = ""  # rendered display string
    phone: str = ""
    email: str = ""
    website: str = ""
    hours: list[str] = field(default_factory=list)
    geo_lat: float | None = None
    geo_lon: float | None = None
    business_type: str = ""
    confidence: str = CONFIDENCE_NONE
    source_url: str = ""
    raw_jsonld: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def has_address(self) -> bool:
        return bool(self.address_street or self.address_full)

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "address": {
                "street": self.address_street,
                "city": self.address_city,
                "region": self.address_region,
                "postal_code": self.address_postal,
                "country": self.address_country,
                "full": self.address_full,
            },
            "phone": self.phone,
            "email": self.email,
            "website": self.website,
            "hours": list(self.hours),
            "geo": (
                {"lat": self.geo_lat, "lon": self.geo_lon}
                if self.geo_lat is not None else None
            ),
            "business_type": self.business_type,
            "confidence": self.confidence,
            "source_url": self.source_url,
            "notes": list(self.notes),
        }
        if self.raw_jsonld:
            d["raw_jsonld"] = self.raw_jsonld
        return d


# ─── HTML scanner ──────────────────────────────────────────────────────

class _BusinessHTMLScanner(HTMLParser):
    """Single-pass HTML scan that captures every signal we care about.

    Stdlib's html.parser is forgiving about malformed markup which is
    what we want for live business sites. We avoid lxml/bs4 to keep
    the dep surface small inside the mcp-scraper subprocess.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.jsonld_blocks: list[str] = []
        self.address_tag_text: list[str] = []
        self.tel_links: list[str] = []
        self.mailto_links: list[str] = []
        self.title: str = ""

        self._in_jsonld = False
        self._in_address = False
        self._in_title = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._in_jsonld = True
            self._buf = []
        elif tag == "address":
            self._in_address = True
            self._buf = []
        elif tag == "title" and not self.title:
            self._in_title = True
            self._buf = []
        elif tag == "a":
            href = (a.get("href") or "").strip()
            if href.lower().startswith("tel:"):
                self.tel_links.append(href[4:].strip())
            elif href.lower().startswith("mailto:"):
                # strip query params (?subject=...)
                self.mailto_links.append(href[7:].split("?", 1)[0].strip())

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            block = "".join(self._buf).strip()
            if block:
                self.jsonld_blocks.append(block)
            self._buf = []
        elif tag == "address" and self._in_address:
            self._in_address = False
            text = " ".join("".join(self._buf).split())
            if text:
                self.address_tag_text.append(text)
            self._buf = []
        elif tag == "title" and self._in_title:
            self._in_title = False
            self.title = " ".join("".join(self._buf).split())
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld or self._in_address or self._in_title:
            self._buf.append(data)


# ─── JSON-LD walker ────────────────────────────────────────────────────

def _normalize_type(t: Any) -> set[str]:
    """schema.org @type can be a string OR a list. Normalize to set."""
    if isinstance(t, str):
        return {t}
    if isinstance(t, list):
        return {str(x) for x in t if isinstance(x, str)}
    return set()


def _is_business_node(node: dict) -> bool:
    types = _normalize_type(node.get("@type"))
    return bool(types & _BUSINESS_TYPES)


def _walk(node: Any, out: list[dict]) -> None:
    """Recursively walk JSON-LD tree, collecting every business-typed node.

    Handles the four real-world shapes we hit:
      - flat object with @type
      - top-level @graph: [...]  (Yoast SEO, popular WordPress emit this)
      - array of objects at the root
      - nested subOrganization / department / branchOf / hasPart
    """
    if isinstance(node, dict):
        if _is_business_node(node):
            out.append(node)
        # @graph wrapper
        graph = node.get("@graph")
        if isinstance(graph, list):
            for child in graph:
                _walk(child, out)
        # Recurse into other dict values (handles nested branches, hasPart…)
        for k, v in node.items():
            if k in ("@graph",):
                continue
            if isinstance(v, (dict, list)):
                _walk(v, out)
    elif isinstance(node, list):
        for item in node:
            _walk(item, out)


def _str_or_empty(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dict):
        # Schema.org @value pattern (i18n strings)
        return str(v.get("@value") or v.get("name") or "").strip()
    return ""


def _extract_postal_address(addr: Any) -> dict:
    """schema.org PostalAddress → flat dict.

    Sites sometimes inline the address as a string. We accept both shapes.
    """
    if isinstance(addr, str):
        return {"full": addr.strip()}
    if not isinstance(addr, dict):
        return {}
    return {
        "street": _str_or_empty(addr.get("streetAddress")),
        "city": _str_or_empty(addr.get("addressLocality")),
        "region": _str_or_empty(addr.get("addressRegion")),
        "postal": _str_or_empty(addr.get("postalCode")),
        "country": _str_or_empty(addr.get("addressCountry")),
    }


def _extract_phone(node: dict) -> str:
    """Phone can sit on the node itself OR inside contactPoint."""
    direct = _str_or_empty(node.get("telephone"))
    if direct:
        return direct
    cp = node.get("contactPoint")
    if isinstance(cp, dict):
        return _str_or_empty(cp.get("telephone"))
    if isinstance(cp, list):
        for item in cp:
            if isinstance(item, dict):
                t = _str_or_empty(item.get("telephone"))
                if t:
                    return t
    return ""


def _extract_email(node: dict) -> str:
    direct = _str_or_empty(node.get("email"))
    if direct:
        return direct
    cp = node.get("contactPoint")
    if isinstance(cp, dict):
        return _str_or_empty(cp.get("email"))
    if isinstance(cp, list):
        for item in cp:
            if isinstance(item, dict):
                e = _str_or_empty(item.get("email"))
                if e:
                    return e
    return ""


def _extract_hours(node: dict) -> list[str]:
    """openingHours: can be string (Mo-Fr 09:00-17:00) or
    openingHoursSpecification: list of {dayOfWeek, opens, closes}."""
    raw_hours: list[str] = []
    direct = node.get("openingHours")
    if isinstance(direct, str):
        raw_hours.append(direct.strip())
    elif isinstance(direct, list):
        raw_hours.extend(str(x).strip() for x in direct if x)

    spec = node.get("openingHoursSpecification")
    if isinstance(spec, dict):
        spec = [spec]
    if isinstance(spec, list):
        for s in spec:
            if not isinstance(s, dict):
                continue
            day = s.get("dayOfWeek")
            opens = _str_or_empty(s.get("opens"))
            closes = _str_or_empty(s.get("closes"))
            if isinstance(day, list):
                day_str = "/".join(_str_or_empty(d).split("/")[-1] for d in day)
            else:
                day_str = _str_or_empty(day).split("/")[-1]
            if day_str and opens and closes:
                raw_hours.append(f"{day_str} {opens}-{closes}")
    return [h for h in raw_hours if h]


def _extract_geo(node: dict) -> tuple[float | None, float | None]:
    geo = node.get("geo")
    if not isinstance(geo, dict):
        return (None, None)
    try:
        lat = float(geo.get("latitude"))
        lon = float(geo.get("longitude"))
        return (lat, lon)
    except (TypeError, ValueError):
        return (None, None)


def _render_full_address(parts: dict) -> str:
    """Pretty-print the postal address parts in a human-readable order."""
    if "full" in parts and parts["full"]:
        return parts["full"]
    pieces = []
    if parts.get("street"):
        pieces.append(parts["street"])
    cityregion = " ".join(p for p in (parts.get("postal", ""), parts.get("city", "")) if p).strip()
    if cityregion:
        pieces.append(cityregion)
    if parts.get("region"):
        pieces.append(parts["region"])
    if parts.get("country"):
        pieces.append(parts["country"])
    return ", ".join(pieces)


def _populate_from_jsonld_node(node: dict, info: BusinessInfo) -> None:
    """Fill an empty BusinessInfo from a single business-typed JSON-LD node."""
    info.name = info.name or _str_or_empty(node.get("name"))
    info.business_type = info.business_type or "/".join(sorted(_normalize_type(node.get("@type"))))
    info.website = info.website or _str_or_empty(node.get("url"))

    addr = _extract_postal_address(node.get("address"))
    if addr:
        info.address_street = info.address_street or addr.get("street", "")
        info.address_city = info.address_city or addr.get("city", "")
        info.address_region = info.address_region or addr.get("region", "")
        info.address_postal = info.address_postal or addr.get("postal", "")
        info.address_country = info.address_country or addr.get("country", "")
        info.address_full = info.address_full or _render_full_address(addr)

    info.phone = info.phone or _extract_phone(node)
    info.email = info.email or _extract_email(node)
    if not info.hours:
        info.hours = _extract_hours(node)
    if info.geo_lat is None:
        lat, lon = _extract_geo(node)
        if lat is not None:
            info.geo_lat, info.geo_lon = lat, lon


# ─── Fallback heuristics (when JSON-LD says nothing) ───────────────────

# Phone digit count threshold — anything fewer is probably an extension or
# a tracking pixel. Real international numbers are 7–15 digits per E.164.
_MIN_PHONE_DIGITS = 7
_MAX_PHONE_DIGITS = 15

# Loose email shape — real validation happens server-side, here we just
# need to filter out malformed mailto: hrefs that some sites stuff with
# "?subject=…&body=…".
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _clean_phone(p: str) -> str:
    """Strip common decorations (spaces, parens, dashes) and validate length.
    Returns the original (decorated) phone if it has the right digit count,
    or empty string if it's noise."""
    digits = re.sub(r"\D", "", p)
    if _MIN_PHONE_DIGITS <= len(digits) <= _MAX_PHONE_DIGITS:
        return p.strip()
    return ""


def _clean_email(e: str) -> str:
    e = e.strip().lower()
    if _EMAIL_RE.match(e):
        return e
    return ""


# ─── Top-level extractor ───────────────────────────────────────────────

def extract_business_info(html: str, source_url: str = "") -> BusinessInfo:
    """Extract structured business info from HTML.

    Pipeline runs all four passes but stops at the first that yields a
    non-empty address. Confidence is set to the highest pass that fired.
    """
    info = BusinessInfo(source_url=source_url)
    if not html or not isinstance(html, str):
        return info

    scanner = _BusinessHTMLScanner()
    try:
        scanner.feed(html)
        scanner.close()
    except Exception as exc:
        info.notes.append(f"html_parse_error: {exc!r}")
        return info

    # ── Pass 1: JSON-LD (high confidence) ────────────────────────────
    business_nodes: list[dict] = []
    for block in scanner.jsonld_blocks:
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            info.notes.append("jsonld_parse_error")
            continue
        info.raw_jsonld.append(data)
        _walk(data, business_nodes)

    if business_nodes:
        # Prefer the first node that has a real address — sites often emit
        # both an Organization (no address) and a LocalBusiness (with address).
        with_addr = [n for n in business_nodes if isinstance(n.get("address"), (dict, str))]
        primary = with_addr[0] if with_addr else business_nodes[0]
        _populate_from_jsonld_node(primary, info)
        # Backfill from other nodes if the primary missed fields
        for node in business_nodes:
            if node is primary:
                continue
            _populate_from_jsonld_node(node, info)
        if info.has_address():
            info.confidence = CONFIDENCE_HIGH

    # Phone/email may live ONLY in tel:/mailto: links (some sites). Fill
    # those in even when JSON-LD provided the address — same source.
    if not info.phone:
        for raw in scanner.tel_links:
            cleaned = _clean_phone(raw)
            if cleaned:
                info.phone = cleaned
                break
    if not info.email:
        for raw in scanner.mailto_links:
            cleaned = _clean_email(raw)
            if cleaned:
                info.email = cleaned
                break

    if info.confidence == CONFIDENCE_HIGH:
        return info

    # ── Pass 2: <address> tag (medium confidence) ────────────────────
    if scanner.address_tag_text:
        # Pick the longest <address> — short ones are usually just
        # "© 2026 Company Inc." footer attribution.
        candidate = max(scanner.address_tag_text, key=len)
        if len(candidate) >= 12:  # arbitrary floor; "Av. X 1" minimum
            info.address_full = info.address_full or candidate
            if not info.name:
                info.name = scanner.title or info.name
            info.confidence = CONFIDENCE_MEDIUM
            return info

    # ── Pass 3: tel:/mailto: only (low confidence) ───────────────────
    if info.phone or info.email:
        info.name = info.name or scanner.title
        info.confidence = CONFIDENCE_LOW
        info.notes.append(
            "no_address_data: page exposes tel/mailto but no JSON-LD or "
            "<address> tag — agent should NOT report a street address from "
            "this source"
        )
        return info

    # ── Pass 4: nothing ───────────────────────────────────────────────
    info.name = scanner.title
    info.confidence = CONFIDENCE_NONE
    info.notes.append(
        "no_structured_business_data: page lacks JSON-LD, <address>, "
        "and tel:/mailto: links — try a different URL (about/contact "
        "subpage often has it) or accept that this site does not "
        "publish ground-truth contact info"
    )
    return info

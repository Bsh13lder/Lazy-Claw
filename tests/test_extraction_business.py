"""Tests for the JSON-LD-first business-info extractor.

Pinned contract — fixes the "8 of 10 addresses wrong" bug. The extractor
must never fabricate an address from cookie-banner / footer / nav text.
It only reports an address when one of:
  - JSON-LD LocalBusiness/Organization/Restaurant/etc. with PostalAddress
  - <address> HTML tag with non-trivial content
  - tel:/mailto: hint (LOW confidence — explicitly flagged "no address")

Anything else returns confidence='none'.
"""

from __future__ import annotations

import sys
from pathlib import Path

# mcp-scraper is a sibling package — add it to sys.path for tests.
sys.path.insert(0, str(Path(__file__).parent.parent / "mcp-scraper"))

from mcp_scraper.extraction_business import (  # noqa: E402
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_NONE,
    extract_business_info,
)


# ── Fixture: clean Restaurant LocalBusiness JSON-LD (Spain) ────────────

RESTAURANT_HTML = """
<!doctype html><html><head><title>Bar Pinotxo — La Boqueria</title>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Restaurant",
  "name": "Bar Pinotxo",
  "url": "https://barpinotxo.com",
  "telephone": "+34 933 17 17 31",
  "email": "info@barpinotxo.com",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "Mercat de la Boqueria, La Rambla 91",
    "addressLocality": "Barcelona",
    "addressRegion": "Catalonia",
    "postalCode": "08001",
    "addressCountry": "ES"
  },
  "openingHoursSpecification": [
    {"@type": "OpeningHoursSpecification", "dayOfWeek": "Monday",
     "opens": "07:00", "closes": "16:00"}
  ],
  "geo": {"@type": "GeoCoordinates", "latitude": 41.3819, "longitude": 2.1722}
}
</script>
</head><body>
<!-- random body junk that would trip a regex extractor -->
<div id="cookie-banner">By using this site you agree... Calle False 1, Madrid</div>
<footer>© Bar Pinotxo 2026 · Headquarters: Avenida Diagonal 99, Barcelona</footer>
</body></html>
"""


def test_restaurant_jsonld_high_confidence():
    info = extract_business_info(RESTAURANT_HTML, source_url="https://barpinotxo.com")
    assert info.confidence == CONFIDENCE_HIGH
    assert info.name == "Bar Pinotxo"
    assert info.business_type == "Restaurant"
    assert info.address_street == "Mercat de la Boqueria, La Rambla 91"
    assert info.address_city == "Barcelona"
    assert info.address_postal == "08001"
    assert info.address_country == "ES"
    assert info.phone == "+34 933 17 17 31"
    assert info.email == "info@barpinotxo.com"
    assert info.geo_lat == 41.3819
    assert info.geo_lon == 2.1722
    # Critical: footer's "Avenida Diagonal 99" must NOT have leaked in
    assert "Diagonal" not in info.address_full
    assert "Calle False" not in info.address_full


# ── Fixture: Yoast-style @graph wrapper (very common on WordPress) ─────

YOAST_GRAPH_HTML = """
<!doctype html><html><head><title>Peluqueria Elite</title>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    {"@type": "WebSite", "@id": "https://peluqueriaelite.es/#website",
     "url": "https://peluqueriaelite.es/", "name": "Peluqueria Elite"},
    {"@type": "WebPage", "@id": "https://peluqueriaelite.es/#webpage",
     "url": "https://peluqueriaelite.es/"},
    {"@type": "HairSalon",
     "@id": "https://peluqueriaelite.es/#localbusiness",
     "name": "Peluqueria Elite",
     "telephone": "+34 963 12 34 56",
     "address": {"@type": "PostalAddress",
       "streetAddress": "Calle Ruzafa 12",
       "addressLocality": "Valencia",
       "postalCode": "46004",
       "addressCountry": "ES"}}
  ]
}
</script></head><body></body></html>
"""


def test_yoast_graph_walks_into_local_business():
    info = extract_business_info(YOAST_GRAPH_HTML)
    assert info.confidence == CONFIDENCE_HIGH
    assert info.name == "Peluqueria Elite"
    assert info.business_type == "HairSalon"
    assert info.address_street == "Calle Ruzafa 12"
    assert info.address_city == "Valencia"
    assert info.address_postal == "46004"
    assert info.phone == "+34 963 12 34 56"


# ── Fixture: Organization (no address) + nested LocalBusiness branch ───
# Real-world case: chain page where Organization has no address but each
# branch has its own LocalBusiness with PostalAddress. We must walk into
# them and pick the one that has an address.

CHAIN_HTML = """
<!doctype html><html><head><title>Toni&Guy Italia</title>
<script type="application/ld+json">
[
  {"@type": "Organization", "name": "Toni&Guy Italia", "url": "https://toniandguy.it"},
  {"@type": "BeautySalon", "name": "Toni&Guy Milano",
   "telephone": "+39 02 123 456 78",
   "address": {"@type": "PostalAddress",
     "streetAddress": "Via Brera 10",
     "addressLocality": "Milano",
     "postalCode": "20121",
     "addressCountry": "IT"}}
]
</script></head><body></body></html>
"""


def test_array_root_picks_node_with_address():
    info = extract_business_info(CHAIN_HTML)
    assert info.confidence == CONFIDENCE_HIGH
    # Pass should pick BeautySalon (has address) over Organization (no address)
    assert info.business_type == "BeautySalon"
    assert info.name == "Toni&Guy Milano"
    assert info.address_city == "Milano"
    assert info.phone == "+39 02 123 456 78"


# ── Fixture: contactPoint instead of direct telephone ─────────────────

CONTACTPOINT_HTML = """
<!doctype html><html><head>
<script type="application/ld+json">
{"@type": "Dentist", "name": "Clinica Dental Madrid",
 "address": {"@type": "PostalAddress", "streetAddress": "Calle Goya 50",
   "addressLocality": "Madrid", "postalCode": "28001", "addressCountry": "ES"},
 "contactPoint": [
   {"@type": "ContactPoint", "telephone": "+34 91 555 12 34",
    "email": "citas@clinicadental.es", "contactType": "appointments"}
 ]}
</script></head><body></body></html>
"""


def test_contactpoint_array_phone_email():
    info = extract_business_info(CONTACTPOINT_HTML)
    assert info.confidence == CONFIDENCE_HIGH
    assert info.phone == "+34 91 555 12 34"
    assert info.email == "citas@clinicadental.es"


# ── Fixture: <address> HTML tag fallback (no JSON-LD) ──────────────────

ADDRESS_TAG_HTML = """
<!doctype html><html><head><title>Café Central</title></head>
<body><h1>Café Central</h1>
<address>Plaza del Ángel 10<br>28012 Madrid<br>Spain</address>
<p>Tel: <a href="tel:+34915214763">+34 915 21 47 63</a></p>
</body></html>
"""


def test_address_tag_medium_confidence():
    info = extract_business_info(ADDRESS_TAG_HTML)
    assert info.confidence == CONFIDENCE_MEDIUM
    assert "Plaza del Ángel 10" in info.address_full
    assert "28012 Madrid" in info.address_full
    # Phone scraped from tel: href — RFC 3966 hrefs are digit-only
    assert info.phone == "+34915214763"
    assert info.name == "Café Central"


# ── Fixture: tel: + mailto: only, no address ──────────────────────────

TEL_MAILTO_ONLY_HTML = """
<!doctype html><html><head><title>Some Company</title></head>
<body>
<a href="tel:+34611223344">Call us</a>
<a href="mailto:hello@company.com?subject=Hi">Email</a>
</body></html>
"""


def test_tel_mailto_only_returns_low_confidence_no_address():
    info = extract_business_info(TEL_MAILTO_ONLY_HTML)
    assert info.confidence == CONFIDENCE_LOW
    assert info.phone == "+34611223344"
    assert info.email == "hello@company.com"
    # Critical: no address must be returned, and notes must explain why
    assert info.address_full == ""
    assert info.address_street == ""
    assert any("no_address_data" in n for n in info.notes)


# ── Fixture: cookie-banner-only page (the soup-pollution bug) ─────────
# This is the case that USED to give us the "8/10 wrong addresses"
# behavior — page has zero structured data but has body text that looks
# address-shaped. We must return confidence=none, not fabricate.

COOKIE_BANNER_SOUP_HTML = """
<!doctype html><html><head><title>Loading…</title></head>
<body>
<div class="cookiebot-banner">
  This site uses cookies. Cookiebot ApS · Havnegade 39, 1058 Copenhagen, Denmark.
  Read our privacy policy. Phone: +45 11 22 33 44
</div>
<script>window.location = '/loading';</script>
</body></html>
"""


def test_cookie_banner_only_returns_no_data():
    """The pollution bug — page has body text that looks like contact data
    but ALL of it is from a cookie banner / disclaimer footer. Extractor
    must return confidence=none and NOT fabricate."""
    info = extract_business_info(COOKIE_BANNER_SOUP_HTML)
    assert info.confidence == CONFIDENCE_NONE
    assert info.address_full == ""
    assert info.address_street == ""
    # Cookiebot's phone number must NOT leak in
    assert info.phone == ""
    assert info.email == ""
    assert any("no_structured_business_data" in n for n in info.notes)


# ── Fixture: malformed JSON-LD (real-world: some sites emit broken JSON) ──

BROKEN_JSONLD_HTML = """
<!doctype html><html><head>
<script type="application/ld+json">
{ "@type": "Restaurant", "name": "Broken
</script>
<script type="application/ld+json">
{"@type": "Restaurant", "name": "Recovers OK",
 "address": {"@type": "PostalAddress", "streetAddress": "Main St 1",
   "addressLocality": "Anywhere", "addressCountry": "US"}}
</script>
</head><body></body></html>
"""


def test_broken_jsonld_recovers_from_subsequent_block():
    info = extract_business_info(BROKEN_JSONLD_HTML)
    assert info.confidence == CONFIDENCE_HIGH
    assert info.name == "Recovers OK"
    assert info.address_street == "Main St 1"
    assert any("jsonld_parse_error" in n for n in info.notes)


# ── Fixture: openingHours (string form, popular older syntax) ──────────

HOURS_STRING_FORM_HTML = """
<!doctype html><html><head>
<script type="application/ld+json">
{"@type": "Restaurant", "name": "Old Sample",
 "openingHours": ["Mo-Fr 09:00-17:00", "Sa 10:00-14:00"],
 "address": {"@type": "PostalAddress", "streetAddress": "Sample 1",
   "addressLocality": "X", "addressCountry": "ES"}}
</script></head><body></body></html>
"""


def test_opening_hours_string_form():
    info = extract_business_info(HOURS_STRING_FORM_HTML)
    assert info.confidence == CONFIDENCE_HIGH
    assert "Mo-Fr 09:00-17:00" in info.hours
    assert "Sa 10:00-14:00" in info.hours


# ── Defensive: empty input, weird input ───────────────────────────────

def test_empty_html_returns_none_confidence():
    info = extract_business_info("")
    assert info.confidence == CONFIDENCE_NONE
    assert info.address_full == ""


def test_non_string_input_returns_none_confidence():
    info = extract_business_info(None)  # type: ignore[arg-type]
    assert info.confidence == CONFIDENCE_NONE


def test_to_dict_round_trip():
    info = extract_business_info(RESTAURANT_HTML)
    d = info.to_dict()
    assert d["confidence"] == "high"
    assert d["address"]["city"] == "Barcelona"
    assert d["geo"]["lat"] == 41.3819
    # raw_jsonld preserved for inspection
    assert "raw_jsonld" in d

"""
Entity extraction using regex patterns and LLM.

Contains _regex_worker, _safe_regex_findall, _internal_extract_entities,
_internal_llm_extract_entities, and extract_entities wrapper
extracted from tools/web_crawling.py.
"""

import json
import re as _re
from typing import Any, Dict, List, Optional

from ..models import CrawlRequest


# Canonical plural keys for the regex `patterns` dict below. The MCP tool
# field used to advertise singular forms ("email", "phone", …) which silently
# matched nothing — this alias map fixes that without breaking existing
# callers that already pass plurals.
_ENTITY_KEY_ALIASES: Dict[str, str] = {
    "email": "emails",
    "phone": "phones",
    "url": "urls",
    "date": "dates",
    "ip": "ips",
    "price": "prices",
    "credit_card": "credit_cards",
    "coordinate": "coordinates",
}


def _canonicalize_entity_types(types: List[str]) -> List[str]:
    """Map singular aliases to the plural keys the regex dict uses."""
    return [_ENTITY_KEY_ALIASES.get(t, t) for t in types]


_PHONE_DIGITS_RE = _re.compile(r"\D+")


# JS snippet that auto-dismisses the most common cookie/consent banners on
# European sites (Cookiebot, OneTrust, Iubenda, Quantcast/IAB-TCF, Complianz,
# generic ARIA labels). Without this, sites like toniandguy.it/saloni return
# only the cookie-wall HTML and the salon directory never renders, so
# extract_entities sees zero contact data even after a successful crawl.
# The snippet is idempotent, runs once per page-load, and is safe to inject
# everywhere — it does nothing on pages without a banner.
#
# IMPORTANT: crawl4ai's `robust_execute_user_script` already wraps our code
# in `(async () => { <body> })()` and awaits it. So we ship BARE STATEMENTS
# here — wrapping in our own IIFE would leave the inner Promise un-awaited
# and Playwright would capture HTML before the consent click took effect.
#
_ACCEPT_COOKIE_BANNERS_JS = r"""
try {
  const SELECTORS = [
    // Cookiebot
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "#CybotCookiebotDialogBodyButtonAccept",
    "#CybotCookiebotDialogBodyLevelButtonAccept",
    // OneTrust
    "#onetrust-accept-btn-handler",
    "button#accept-recommended-btn-handler",
    // Iubenda
    "button.iubenda-cs-accept-btn",
    ".iubenda-cs-accept-btn",
    // Quantcast / IAB TCF
    "button.qc-cmp2-summary-buttons > button[mode=primary]",
    "button[aria-label='AGREE']",
    // Complianz / RGPD
    "button.cmplz-accept",
    "button.cc-accept-all",
    // Italian-language fallbacks
    "button[aria-label*='Accetta' i]",
    "button[title*='Accetta' i]",
    // English-language fallbacks
    "button[aria-label*='Accept' i]",
    "button[title*='Accept' i]",
    // Generic class hints (last resort)
    "button[class*='accept-all' i]",
    "button[class*='accept_all' i]",
  ];
  let clicked = false;
  for (const s of SELECTORS) {
    const el = document.querySelector(s);
    if (el && el.offsetParent !== null) { el.click(); clicked = true; break; }
  }
  // Give the page a beat to fetch real content after consent.
  await new Promise(r => setTimeout(r, 2200));
  // Lazy-load scroll: many SPA contact widgets fetch only after scroll.
  window.scrollTo(0, document.body.scrollHeight);
  await new Promise(r => setTimeout(r, 800));
  window.scrollTo(0, 0);
  return { consent_clicked: clicked };
} catch (e) {
  // Never let consent JS break extraction.
  return { error: String(e) };
}
"""


def _looks_like_real_phone(candidate: str) -> bool:
    """Reject obvious non-phone matches (cookie IDs, VAT numbers, hashes).

    A candidate must:
      - have between 8 and 15 digits (E.164 range)
      - not be all-same-digit (0000000000)
      - not start with three zeros (typical product/order code)
    """
    digits = _PHONE_DIGITS_RE.sub("", candidate)
    if not (8 <= len(digits) <= 15):
        return False
    if len(set(digits)) <= 1:
        return False
    if digits.startswith("000"):
        return False
    return True


def _regex_worker(pattern: str, text: str, flags: int, result_queue) -> None:
    """
    Worker function for regex execution in separate process.

    This runs in an isolated process to enable true timeout via process termination.
    """
    import re
    try:
        compiled = re.compile(pattern, flags)
        matches = compiled.findall(text)
        result_queue.put(("success", matches))
    except Exception as e:
        result_queue.put(("error", str(e)))


def _safe_regex_findall(pattern: str, text: str, timeout: float = 5.0) -> List[str]:
    """
    Execute re.findall with true timeout protection using multiprocessing.
    """
    import re
    import multiprocessing

    # Validate pattern first
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"Invalid regex pattern: {e}")

    # Safety: limit pattern complexity to mitigate DoS risk
    if len(pattern) > 500:
        raise ValueError(f"Pattern too long ({len(pattern)} chars, max 500)")

    # Use multiprocessing for true timeout with process termination
    result_queue = multiprocessing.Queue()
    process = multiprocessing.Process(
        target=_regex_worker,
        args=(pattern, text, re.IGNORECASE, result_queue)
    )

    process.start()
    process.join(timeout=timeout)

    if process.is_alive():
        process.terminate()
        process.join(timeout=1.0)
        if process.is_alive():
            process.kill()
            process.join()
        raise TimeoutError(f"Regex execution timed out after {timeout}s (possible ReDoS pattern)")

    if result_queue.empty():
        raise RuntimeError("Regex worker returned no result")

    status, result = result_queue.get_nowait()
    if status == "error":
        raise ValueError(f"Regex execution error: {result}")

    return result


async def _internal_extract_entities(
    url: str,
    entity_types: List[str],
    custom_patterns: Optional[Dict[str, str]] = None,
    include_context: bool = True,
    deduplicate: bool = True,
    use_undetected_browser: bool = False,
    simulate_user: bool = False,
) -> Dict[str, Any]:
    """
    Internal extract entities implementation using regex patterns.
    """
    from .crawler_core import _internal_crawl_url

    try:
        # Entity extraction always needs the page fully rendered, otherwise
        # SPA-rendered contact widgets (store locators, dental clinics,
        # salon directories) finish their fetch *after* the crawl returns
        # and the page text never contains the email/phone. wait_for_js
        # turns on networkidle + scan_full_page in `_internal_crawl_url`,
        # which forces a full-page scroll so lazy content loads.
        request = CrawlRequest(
            url=url,
            generate_markdown=True,
            include_cleaned_html=True,
            wait_for_js=True,
            execute_js=_ACCEPT_COOKIE_BANNERS_JS,
            use_undetected_browser=use_undetected_browser,
            simulate_user=simulate_user,
        )
        crawl_result = await _internal_crawl_url(request)

        if not crawl_result.success:
            return {
                "url": url,
                "success": False,
                "error": f"Failed to crawl URL: {crawl_result.error}"
            }

        content = crawl_result.content or crawl_result.markdown or ""
        entities = {}
        pattern_errors = {}

        # Phone pattern accepts:
        #   - international form (+CC + 8-16 digit-or-separator chars)
        #   - structured local form (digit groups separated by space/-/.)
        # plus a post-filter (`_looks_like_real_phone`) drops noise such as
        # cookie IDs, VAT numbers and asset hashes that the old US-shaped
        # regex produced as false positives.
        patterns = {
            "emails": r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
            "phones": (
                r'(?:\+\d[\d\s\-./()]{7,18}\d)'
                r'|'
                r'(?<![\d./])(?:\(?\d{2,5}\)?[\s\-.]\d{2,5}[\s\-.]\d{2,5}(?:[\s\-.]\d{2,5})?)(?![\d./])'
            ),
            "urls": r'https?://(?:[-\w.])+(?:[:\d]+)?(?:/(?:[\w/_.])*(?:\?(?:[\w&=%.])*)?(?:#(?:[\w.])*)?)?',
            "dates": r'\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{2,4}[/-]\d{1,2}[/-]\d{1,2})\b',
            "ips": r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b',
            "prices": r'[$£€¥]?\s?\d+(?:[.,]\d{2,3})*(?:[.,]\d{2})?',
            "credit_cards": r'\b(?:\d{4}[-\s]?){3}\d{4}\b',
            "coordinates": r'[-+]?(?:[1-8]?\d(?:\.\d+)?|90(?:\.0+)?),\s*[-+]?(?:180(?:\.0+)?|(?:(?:1[0-7]\d)|(?:[1-9]?\d))(?:\.\d+)?)'
        }

        if custom_patterns:
            patterns.update(custom_patterns)

        canonical_types = _canonicalize_entity_types(entity_types)

        for entity_type in canonical_types:
            if entity_type in patterns:
                try:
                    matches = _safe_regex_findall(patterns[entity_type], content, timeout=5.0)
                    if entity_type == "phones" and matches:
                        matches = [m.strip() for m in matches if _looks_like_real_phone(m)]
                    if matches:
                        if deduplicate:
                            matches = list(set(matches))
                        entities[entity_type] = matches
                except ValueError as e:
                    pattern_errors[entity_type] = f"Invalid pattern: {str(e)}"
                    entities[entity_type] = []
                except TimeoutError as e:
                    pattern_errors[entity_type] = f"Pattern timeout: {str(e)}"
                    entities[entity_type] = []

        result = {
            "url": url,
            "success": True,
            "entities": entities,
            "entity_types_requested": entity_types,
            "processing_method": "regex_extraction",
            "content_length": len(content),
            "total_entities_found": sum(len(v) for v in entities.values())
        }

        if pattern_errors:
            result["pattern_errors"] = pattern_errors

        return result

    except Exception as e:
        return {
            "url": url,
            "success": False,
            "error": f"Entity extraction error: {str(e)}"
        }


async def _internal_llm_extract_entities(
    url: str,
    entity_types: List[str],
    provider: Optional[str] = None,
    model: Optional[str] = None,
    custom_instructions: Optional[str] = None,
    include_context: bool = True,
    deduplicate: bool = True,
    use_undetected_browser: bool = False,
    simulate_user: bool = False,
) -> Dict[str, Any]:
    """
    Internal LLM extract entities implementation using AI-powered named entity recognition.
    """
    from .crawler_core import _internal_crawl_url

    try:
        request = CrawlRequest(
            url=url,
            generate_markdown=True,
            include_cleaned_html=True,
            wait_for_js=True,
            execute_js=_ACCEPT_COOKIE_BANNERS_JS,
            use_undetected_browser=use_undetected_browser,
            simulate_user=simulate_user,
        )
        crawl_result = await _internal_crawl_url(request)

        if not crawl_result.success:
            return {
                "url": url,
                "success": False,
                "error": f"Failed to crawl URL: {crawl_result.error}"
            }

        content = crawl_result.content or crawl_result.markdown or ""
        if not content.strip():
            return {
                "url": url,
                "success": True,
                "entities": {},
                "entity_types_requested": entity_types,
                "processing_method": "llm_extraction",
                "content_length": 0,
                "total_entities_found": 0,
                "note": "No content found to extract entities from"
            }

        from ..utils.llm_extraction import LLMExtractionClient

        client = LLMExtractionClient.from_config(provider, model)

        entity_descriptions = {
            "emails": "Email addresses (e.g., user@example.com)",
            "phones": "Phone numbers in various formats",
            "urls": "Web URLs and links",
            "dates": "Dates in various formats",
            "ips": "IP addresses",
            "prices": "Prices and monetary amounts",
            "credit_cards": "Credit card numbers",
            "coordinates": "Geographic coordinates (latitude, longitude)",
            "social_media": "Social media handles and profiles",
            "people": "Names of people, individuals, persons",
            "organizations": "Company names, institutions, organizations",
            "locations": "Places, cities, countries, geographic locations",
            "products": "Product names, brands, models",
            "events": "Events, conferences, meetings, occasions"
        }

        requested_entities = []
        for entity_type in entity_types:
            description = entity_descriptions.get(entity_type, f"Custom entity type: {entity_type}")
            requested_entities.append(f"- {entity_type}: {description}")

        entity_types_text = "\n".join(requested_entities)

        extraction_prompt = f"""
You are an expert entity extraction specialist. Extract all instances of the specified entity types from the given web content.

ENTITY TYPES TO EXTRACT:
{entity_types_text}

EXTRACTION INSTRUCTIONS:
- Extract ALL instances of each specified entity type from the content
- Maintain exact accuracy - extract entities exactly as they appear in the source
- For each entity type, provide a list of unique entities found
- If context is requested, include a brief surrounding text snippet for each entity
- Remove duplicates within each entity type
- If no entities of a specific type are found, return an empty list for that type
- Return results in valid JSON format

{f"ADDITIONAL INSTRUCTIONS: {custom_instructions}" if custom_instructions else ""}

Please provide a JSON response with the following structure:
{{
    "entities": {{
        "entity_type_1": [
            {{
                "value": "extracted_entity_text",
                "context": "surrounding text context (if requested)",
                "confidence": "High/Medium/Low"
            }}
        ],
        "entity_type_2": [...]
    }},
    "extraction_summary": {{
        "total_entities_found": number,
        "entity_types_found": ["list", "of", "types", "with", "results"],
        "entity_types_empty": ["list", "of", "types", "with", "no", "results"],
        "extraction_confidence": "High/Medium/Low"
    }}
}}

WEB CONTENT TO ANALYZE:
{content[:40000]}  # Limit content to prevent token overflow
"""

        system_message = "You are an expert entity extraction specialist focused on accuracy and comprehensive extraction."

        extracted_content = await client.call_llm(
            prompt=extraction_prompt,
            system_message=system_message,
            temperature=0.1,
            max_tokens=4000
        )

        if extracted_content:
            try:
                extraction_result = client.parse_json_response(extracted_content)

                processed_entities = {}
                for entity_type, entities_list in extraction_result.get("entities", {}).items():
                    if entity_type in entity_types:
                        if include_context and isinstance(entities_list, list) and entities_list:
                            processed_entities[entity_type] = entities_list
                        else:
                            if isinstance(entities_list, list):
                                values = []
                                for entity in entities_list:
                                    if isinstance(entity, dict):
                                        values.append(entity.get('value', str(entity)))
                                    else:
                                        values.append(str(entity))
                                processed_entities[entity_type] = list(set(values)) if deduplicate else values
                            else:
                                processed_entities[entity_type] = entities_list

                summary = extraction_result.get("extraction_summary", {})

                return {
                    "url": url,
                    "success": True,
                    "entities": processed_entities,
                    "entity_types_requested": entity_types,
                    "processing_method": "llm_extraction",
                    "llm_provider": client.provider,
                    "llm_model": client.model,
                    "content_length": len(content),
                    "total_entities_found": summary.get("total_entities_found", sum(len(v) for v in processed_entities.values())),
                    "extraction_confidence": summary.get("extraction_confidence", "Medium"),
                    "entity_types_found": summary.get("entity_types_found", list(processed_entities.keys())),
                    "entity_types_empty": summary.get("entity_types_empty", [et for et in entity_types if et not in processed_entities]),
                    "include_context": include_context,
                    "deduplicated": deduplicate
                }

            except (json.JSONDecodeError, AttributeError) as e:
                return {
                    "url": url,
                    "success": True,
                    "entities": {"raw_extraction": [str(extracted_content)]},
                    "entity_types_requested": entity_types,
                    "processing_method": "llm_extraction_fallback",
                    "llm_provider": client.provider,
                    "llm_model": client.model,
                    "content_length": len(content),
                    "total_entities_found": 1,
                    "extraction_confidence": "Low",
                    "json_parse_error": str(e),
                    "note": f"JSON parsing failed, returned raw LLM output: {str(e)}"
                }
        else:
            return {
                "url": url,
                "success": False,
                "error": "LLM entity extraction returned empty result"
            }

    except Exception as e:
        return {
            "url": url,
            "success": False,
            "error": f"LLM entity extraction error: {str(e)}"
        }


async def extract_entities(
    url: str,
    entity_types: List[str],
    custom_patterns: Optional[Dict[str, str]] = None,
    include_context: bool = True,
    deduplicate: bool = True,
    use_llm: bool = False,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    use_undetected_browser: bool = False,
    simulate_user: bool = False,
) -> Dict[str, Any]:
    """Extract entities (emails, phones, etc.) from web pages."""
    if use_llm:
        return await _internal_llm_extract_entities(
            url=url,
            entity_types=entity_types,
            provider=llm_provider,
            model=llm_model,
            custom_instructions=None,
            include_context=include_context,
            deduplicate=deduplicate,
            use_undetected_browser=use_undetected_browser,
            simulate_user=simulate_user,
        )
    else:
        return await _internal_extract_entities(
            url=url,
            entity_types=entity_types,
            custom_patterns=custom_patterns,
            include_context=include_context,
            deduplicate=deduplicate,
            use_undetected_browser=use_undetected_browser,
            simulate_user=simulate_user,
        )

"""
Google Search Processing Module
Handles Google search queries and result processing with multiple backends:
auto (Serper → SerpAPI → googlesearch-python → Custom Search API), serper-only, serpapi-only.
"""

import asyncio
import re
import os
import logging
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse
from googlesearch import search
import httpx

from .google_search_helpers import RateLimiter
from .google_custom_search import CustomSearchAPIClient
from .google_search_analysis import GoogleSearchAnalysisMixin


SERPER_ENDPOINT = "https://google.serper.dev/search"
SERPAPI_ENDPOINT = "https://serpapi.com/search.json"


class GoogleSearchProcessor(GoogleSearchAnalysisMixin):
    """Process Google search queries with backend fallback chain.

    Backends, in priority order when mode == 'auto':
      1. Serper (paid, fastest, ~$0.30/1k, 2,500 free credits) — needs SERPER_KEY
      2. SerpAPI (paid, ~3x Serper) — needs SERPAPI_KEY
      3. googlesearch-python (free, often blocked by Google)
      4. Google Custom Search API — needs GOOGLE_SEARCH_API_KEY + GOOGLE_SEARCH_ENGINE_ID

    Mode is read from SCRAPER_SEARCH_BACKEND on every call (so changing env at
    runtime takes effect without restart). Falls back to GOOGLE_SEARCH_MODE for
    legacy compatibility.
    """

    def __init__(self):
        self.rate_limiter = RateLimiter()
        self.custom_search_client = CustomSearchAPIClient()

        self.max_retries = int(os.getenv('GOOGLE_SEARCH_MAX_RETRIES', '3'))
        self.fallback_delay = float(os.getenv('GOOGLE_SEARCH_FALLBACK_DELAY', '2.0'))

        self.search_patterns = [
            r'site:([^\s]+)',
            r'filetype:([^\s]+)',
            r'"([^"]+)"',
            r'after:(\d{4})',
            r'before:(\d{4})'
        ]

    @property
    def search_mode(self) -> str:
        """Resolve the search backend mode at call time (re-read each search).

        Priority: SCRAPER_SEARCH_BACKEND > GOOGLE_SEARCH_MODE > 'auto'.
        Legacy 'hybrid' is treated as 'auto'.
        """
        raw = os.getenv('SCRAPER_SEARCH_BACKEND') or os.getenv('GOOGLE_SEARCH_MODE') or 'auto'
        mode = raw.lower().strip()
        if mode == 'hybrid':
            mode = 'auto'
        valid_modes = {'auto', 'serper', 'serpapi', 'googlesearch_only', 'custom_search_only'}
        if mode not in valid_modes:
            logging.warning(f"Invalid search backend '{mode}'. Using 'auto'")
            mode = 'auto'
        return mode

        self.search_patterns = [
            r'site:([^\s]+)',
            r'filetype:([^\s]+)',
            r'"([^"]+)"',
            r'after:(\d{4})',
            r'before:(\d{4})'
        ]

    def validate_query(self, query: str) -> Dict[str, Any]:
        """Validate and analyze search query"""
        try:
            if not query or not query.strip():
                return {
                    'valid': False,
                    'error': 'Search query cannot be empty'
                }

            query = query.strip()
            if len(query) > 500:
                return {
                    'valid': False,
                    'error': 'Search query too long (max 500 characters)'
                }

            patterns_found = {}
            for pattern in self.search_patterns:
                matches = re.findall(pattern, query, re.IGNORECASE)
                if matches:
                    pattern_name = pattern.split('(')[0].replace(':', '').replace('[', '').replace('^', '')
                    patterns_found[pattern_name] = matches

            return {
                'valid': True,
                'query': query,
                'length': len(query),
                'patterns': patterns_found,
                'is_advanced': len(patterns_found) > 0
            }

        except Exception as e:
            return {
                'valid': False,
                'error': f'Query validation error: {str(e)}'
            }

    async def search_google(
        self,
        query: str,
        num_results: int = 10,
        language: str = 'en',
        region: str = 'us',
        safe_search: bool = True,
        search_genre: Optional[str] = None,
        include_snippets: bool = True,
        backend: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Perform Google search with flexible API selection and fallback.

        ``backend`` (optional, per-call override): one of
        'auto' / 'serper' / 'serpapi' / 'googlesearch_only' / 'custom_search_only'.
        When omitted, falls back to the env-derived ``search_mode`` property.
        """
        try:
            validation = self.validate_query(query)
            if not validation['valid']:
                return {
                    'success': False,
                    'error': validation['error'],
                    'query': query
                }

            enhanced_query = self._enhance_query_with_genre(query, search_genre)
            num_results = max(1, min(100, num_results))

            # Per-call override beats env. Normalize legacy 'hybrid' → 'auto'.
            if backend:
                mode = str(backend).lower().strip()
                if mode == 'hybrid':
                    mode = 'auto'
                if mode not in {'auto', 'serper', 'serpapi', 'googlesearch_only', 'custom_search_only'}:
                    logging.warning(f"Invalid per-call backend '{backend}'. Falling back to env mode.")
                    mode = self.search_mode
            else:
                mode = self.search_mode
            if mode == 'serper':
                return await self._search_with_serper(
                    enhanced_query, num_results, language, region, search_genre, validation
                )
            elif mode == 'serpapi':
                return await self._search_with_serpapi(
                    enhanced_query, num_results, language, region, search_genre, validation
                )
            elif mode == 'custom_search_only':
                return await self._search_with_custom_api(
                    enhanced_query, num_results, language, region, search_genre, validation
                )
            elif mode == 'googlesearch_only':
                return await self._search_with_googlesearch(
                    enhanced_query, num_results, language, region, search_genre, validation
                )
            else:  # auto: Serper → SerpAPI → googlesearch → CSE
                return await self._search_with_fallback(
                    enhanced_query, num_results, language, region, search_genre, validation
                )

        except Exception as e:
            return {
                'success': False,
                'error': f'Search processing error: {str(e)}',
                'query': query
            }

    async def _search_with_fallback(
        self,
        query: str,
        num_results: int,
        language: str,
        region: str,
        search_genre: Optional[str],
        validation: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Auto search with fallback chain: Serper → SerpAPI → googlesearch-python → CSE.

        Each backend is only tried if (a) its API key is configured AND (b) the
        previous backends returned an empty/failed/rate-limited result. First
        successful result wins.
        """
        attempts = []

        # 1. Serper (preferred — fastest, cheapest, you have key)
        if os.getenv('SERPER_KEY') or os.getenv('SERPER_API_KEY'):
            serper_result = await self._search_with_serper(
                query, num_results, language, region, search_genre, validation
            )
            attempts.append(('serper', serper_result))
            if serper_result['success'] and serper_result.get('total_results', 0) > 0:
                serper_result['fallback_info'] = {'attempts': [a[0] for a in attempts]}
                return serper_result
            logging.info(f"Serper returned empty/failed for '{query[:60]}', trying SerpAPI")

        # 2. SerpAPI (fallback — paid, more features)
        if os.getenv('SERPAPI_KEY') or os.getenv('SERPAPI_API_KEY'):
            serpapi_result = await self._search_with_serpapi(
                query, num_results, language, region, search_genre, validation
            )
            attempts.append(('serpapi', serpapi_result))
            if serpapi_result['success'] and serpapi_result.get('total_results', 0) > 0:
                serpapi_result['fallback_info'] = {'attempts': [a[0] for a in attempts]}
                return serpapi_result
            logging.info(f"SerpAPI returned empty/failed for '{query[:60]}', trying googlesearch-python")

        # 3. googlesearch-python (free, often blocked)
        if self.rate_limiter.can_make_request('googlesearch'):
            googlesearch_result = await self._search_with_googlesearch(
                query, num_results, language, region, search_genre, validation,
                record_rate_limit=True
            )
            attempts.append(('googlesearch-python', googlesearch_result))

            if googlesearch_result['success'] or not self._is_rate_limit_error(googlesearch_result):
                if googlesearch_result['success']:
                    googlesearch_result['fallback_info'] = {'attempts': [a[0] for a in attempts]}
                return googlesearch_result

            logging.warning(f"429 error detected with googlesearch-python, falling back to Custom Search API")
            await asyncio.sleep(self.fallback_delay)
        else:
            wait_time = self.rate_limiter.get_wait_time('googlesearch')
            attempts.append(('googlesearch-python', {
                'success': False,
                'error': f'Rate limit reached. Wait {wait_time:.1f} seconds',
                'rate_limited': True
            }))

        if self.custom_search_client.is_configured() and self.rate_limiter.can_make_request('custom_search'):
            custom_search_result = await self._search_with_custom_api(
                query, num_results, language, region, search_genre, validation,
                record_rate_limit=True
            )
            attempts.append(('google_custom_search_api', custom_search_result))

            if custom_search_result['success']:
                custom_search_result['fallback_info'] = {
                    'primary_method': 'googlesearch-python',
                    'fallback_method': 'google_custom_search_api',
                    'fallback_reason': 'Rate limit or 429 error',
                    'attempts': attempts
                }
                return custom_search_result
        else:
            if not self.custom_search_client.is_configured():
                attempts.append(('google_custom_search_api', {
                    'success': False,
                    'error': 'Custom Search API not configured',
                    'suggestion': 'Set GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_ENGINE_ID'
                }))
            else:
                wait_time = self.rate_limiter.get_wait_time('custom_search')
                attempts.append(('google_custom_search_api', {
                    'success': False,
                    'error': f'Rate limit reached. Wait {wait_time:.1f} seconds',
                    'rate_limited': True
                }))

        # Distinguish "everything was rate-limited" (transient, retry later)
        # from "everything returned 0 results" (query too narrow). Callers
        # substring-match this error to decide whether to trip a cooldown,
        # so the wording matters — don't conflate the two.
        any_rate_limited = any(
            self._is_rate_limit_error(a[1]) or a[1].get('rate_limited')
            for a in attempts
        )
        all_zero_results = all(
            (not a[1].get('success', False))
            and not (self._is_rate_limit_error(a[1]) or a[1].get('rate_limited'))
            and ('No search results found' in (a[1].get('error') or '')
                 or a[1].get('total_results', 0) == 0)
            for a in attempts
        ) if attempts else False

        if all_zero_results:
            error_msg = 'All search backends returned 0 results — query too narrow'
            suggestion = 'Try a simpler query or remove quoted phrases'
        elif any_rate_limited:
            error_msg = 'All search methods failed or rate limited'
            suggestion = 'Wait for rate limits to reset or configure missing API credentials'
        else:
            error_msg = 'All search backends failed (no rate limit)'
            suggestion = 'Check API key configuration; see fallback_info.attempts for details'

        return {
            'success': False,
            'error': error_msg,
            'query': query,
            'fallback_info': {
                'search_mode': 'auto',
                'attempts': attempts,
                'suggestion': suggestion,
            }
        }

    async def _search_with_googlesearch(
        self,
        query: str,
        num_results: int,
        language: str,
        region: str,
        search_genre: Optional[str],
        validation: Dict[str, Any],
        record_rate_limit: bool = False
    ) -> Dict[str, Any]:
        """Search using googlesearch-python library with 429 error detection"""
        try:
            search_results = []

            loop = asyncio.get_event_loop()

            def do_search():
                return list(search(
                    query,
                    num_results=num_results,
                    lang=language,
                    sleep_interval=1.0,
                    region=region,
                    safe='active'
                ))

            urls = await loop.run_in_executor(None, do_search)

            if record_rate_limit:
                self.rate_limiter.record_request('googlesearch', 'success')

            for i, url in enumerate(urls):
                if not url:
                    continue

                try:
                    parsed_url = urlparse(url)
                    domain = parsed_url.netloc

                    title, snippet = await self._extract_title_and_snippet(url)

                    result = {
                        'rank': i + 1,
                        'url': url,
                        'domain': domain,
                        'title': title,
                        'snippet': snippet,
                        'type': self._classify_url(url)
                    }

                    search_results.append(result)

                except Exception:
                    continue

            if not search_results:
                suggestions = self._generate_simplified_query_suggestions(query)
                suggestion_text = 'Try a broader or different search query'

                if suggestions:
                    quoted_suggestions = [f"'{s}'" for s in suggestions]
                    suggestion_text = f"Try these simpler searches: {', '.join(quoted_suggestions)}"

                return {
                    'success': False,
                    'error': 'No search results found',
                    'query': query,
                    'suggestion': suggestion_text,
                    'alternative_queries': suggestions
                }

            domains = [result['domain'] for result in search_results]
            unique_domains = list(set(domains))
            domain_counts = {domain: domains.count(domain) for domain in unique_domains}

            type_counts = {}
            for result in search_results:
                result_type = result['type']
                type_counts[result_type] = type_counts.get(result_type, 0) + 1

            return {
                'success': True,
                'query': query,
                'enhanced_query': query,
                'total_results': len(search_results),
                'results': search_results,
                'search_metadata': {
                    'query_info': validation,
                    'search_params': {
                        'num_results_requested': num_results,
                        'language': language,
                        'region': region,
                        'safe_search': True,
                        'search_genre': search_genre,
                        'enhanced_query': query
                    },
                    'result_stats': {
                        'total_results': len(search_results),
                        'unique_domains': len(unique_domains),
                        'domain_distribution': domain_counts,
                        'result_types': type_counts
                    }
                },
                'processing_method': 'googlesearch-python'
            }

        except Exception as e:
            error_msg = str(e).lower()

            if record_rate_limit:
                status = '429' if '429' in error_msg or 'too many requests' in error_msg else 'error'
                self.rate_limiter.record_request('googlesearch', status)

            if '429' in error_msg or 'too many requests' in error_msg or 'rate limit' in error_msg:
                return {
                    'success': False,
                    'error': f'Rate limit exceeded: {str(e)}',
                    'status_code': 429,
                    'query': query,
                    'suggestion': 'Wait before retrying or use Custom Search API'
                }

            return {
                'success': False,
                'error': f'googlesearch-python error: {str(e)}',
                'query': query,
                'suggestion': 'Try a different search query or check your internet connection'
            }

    async def _search_with_serper(
        self,
        query: str,
        num_results: int,
        language: str,
        region: str,
        search_genre: Optional[str],
        validation: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Search via Serper.dev API. Reads SERPER_KEY (or SERPER_API_KEY) at call time."""
        api_key = os.getenv('SERPER_KEY') or os.getenv('SERPER_API_KEY')
        if not api_key:
            return {
                'success': False,
                'error': 'SERPER_KEY not configured',
                'query': query,
                'suggestion': 'Set SERPER_KEY in env or use a different SCRAPER_SEARCH_BACKEND',
            }

        payload = {
            'q': query,
            'num': max(1, min(num_results, 100)),
            'gl': region.lower() if region else 'us',
            'hl': language.lower() if language else 'en',
        }
        headers = {'X-API-KEY': api_key, 'Content-Type': 'application/json'}

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(SERPER_ENDPOINT, json=payload, headers=headers)
            if resp.status_code == 401 or resp.status_code == 403:
                return {
                    'success': False,
                    'error': f'Serper auth failed (HTTP {resp.status_code}): {resp.text[:120]}',
                    'query': query,
                    'suggestion': 'SERPER_KEY may be invalid or revoked',
                }
            if resp.status_code == 429:
                return {
                    'success': False,
                    'error': 'Serper rate limited or quota exhausted',
                    'status_code': 429,
                    'query': query,
                    'suggestion': 'Wait or top up Serper credits',
                }
            if resp.status_code >= 400:
                return {
                    'success': False,
                    'error': f'Serper HTTP {resp.status_code}: {resp.text[:200]}',
                    'query': query,
                }

            data = resp.json()
            organic = data.get('organic', []) or []
            knowledge_box = data.get('knowledgeGraph') or {}
            answer_box = data.get('answerBox') or {}

            search_results = []
            for i, item in enumerate(organic[:num_results]):
                url = item.get('link') or ''
                if not url:
                    continue
                domain = ''
                try:
                    domain = urlparse(url).netloc
                except Exception:
                    pass
                search_results.append({
                    'rank': i + 1,
                    'url': url,
                    'domain': domain,
                    'title': item.get('title') or '',
                    'snippet': item.get('snippet') or '',
                    'type': self._classify_url(url),
                })

            if not search_results:
                suggestions = self._generate_simplified_query_suggestions(query)
                return {
                    'success': False,
                    'error': 'No search results found',
                    'query': query,
                    'suggestion': (
                        f"Try simpler searches: {', '.join(repr(s) for s in suggestions)}"
                        if suggestions else 'Try a different query'
                    ),
                    'alternative_queries': suggestions,
                    'processing_method': 'serper',
                }

            domains = [r['domain'] for r in search_results]
            unique_domains = list(set(domains))
            domain_counts = {d: domains.count(d) for d in unique_domains}
            type_counts: Dict[str, int] = {}
            for r in search_results:
                type_counts[r['type']] = type_counts.get(r['type'], 0) + 1

            return {
                'success': True,
                'query': query,
                'enhanced_query': query,
                'total_results': len(search_results),
                'results': search_results,
                'search_metadata': {
                    'query_info': validation,
                    'search_params': {
                        'num_results_requested': num_results,
                        'language': language,
                        'region': region,
                        'safe_search': True,
                        'search_genre': search_genre,
                        'enhanced_query': query,
                    },
                    'result_stats': {
                        'total_results': len(search_results),
                        'unique_domains': len(unique_domains),
                        'domain_distribution': domain_counts,
                        'result_types': type_counts,
                    },
                    'extras': {
                        'has_knowledge_graph': bool(knowledge_box),
                        'has_answer_box': bool(answer_box),
                    },
                },
                'processing_method': 'serper',
            }
        except httpx.TimeoutException:
            return {'success': False, 'error': 'Serper timeout (>15s)', 'query': query}
        except Exception as e:
            return {'success': False, 'error': f'Serper error: {str(e)}', 'query': query}

    async def _search_with_serpapi(
        self,
        query: str,
        num_results: int,
        language: str,
        region: str,
        search_genre: Optional[str],
        validation: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Search via SerpAPI. Reads SERPAPI_KEY (or SERPAPI_API_KEY) at call time."""
        api_key = os.getenv('SERPAPI_KEY') or os.getenv('SERPAPI_API_KEY')
        if not api_key:
            return {
                'success': False,
                'error': 'SERPAPI_KEY not configured',
                'query': query,
                'suggestion': 'Set SERPAPI_KEY in env or use a different SCRAPER_SEARCH_BACKEND',
            }

        params = {
            'engine': 'google',
            'q': query,
            'num': max(1, min(num_results, 100)),
            'gl': region.lower() if region else 'us',
            'hl': language.lower() if language else 'en',
            'api_key': api_key,
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(SERPAPI_ENDPOINT, params=params)
            if resp.status_code == 401:
                return {
                    'success': False,
                    'error': f'SerpAPI auth failed: {resp.text[:120]}',
                    'query': query,
                    'suggestion': 'SERPAPI_KEY may be invalid',
                }
            if resp.status_code == 429:
                return {
                    'success': False,
                    'error': 'SerpAPI rate limited or quota exhausted',
                    'status_code': 429,
                    'query': query,
                }
            if resp.status_code >= 400:
                return {
                    'success': False,
                    'error': f'SerpAPI HTTP {resp.status_code}: {resp.text[:200]}',
                    'query': query,
                }

            data = resp.json()
            organic = data.get('organic_results', []) or []

            search_results = []
            for i, item in enumerate(organic[:num_results]):
                url = item.get('link') or ''
                if not url:
                    continue
                domain = ''
                try:
                    domain = urlparse(url).netloc
                except Exception:
                    pass
                search_results.append({
                    'rank': item.get('position') or (i + 1),
                    'url': url,
                    'domain': domain,
                    'title': item.get('title') or '',
                    'snippet': item.get('snippet') or '',
                    'type': self._classify_url(url),
                })

            if not search_results:
                suggestions = self._generate_simplified_query_suggestions(query)
                return {
                    'success': False,
                    'error': 'No search results found',
                    'query': query,
                    'suggestion': (
                        f"Try simpler searches: {', '.join(repr(s) for s in suggestions)}"
                        if suggestions else 'Try a different query'
                    ),
                    'alternative_queries': suggestions,
                    'processing_method': 'serpapi',
                }

            domains = [r['domain'] for r in search_results]
            unique_domains = list(set(domains))
            domain_counts = {d: domains.count(d) for d in unique_domains}
            type_counts: Dict[str, int] = {}
            for r in search_results:
                type_counts[r['type']] = type_counts.get(r['type'], 0) + 1

            return {
                'success': True,
                'query': query,
                'enhanced_query': query,
                'total_results': len(search_results),
                'results': search_results,
                'search_metadata': {
                    'query_info': validation,
                    'search_params': {
                        'num_results_requested': num_results,
                        'language': language,
                        'region': region,
                        'safe_search': True,
                        'search_genre': search_genre,
                        'enhanced_query': query,
                    },
                    'result_stats': {
                        'total_results': len(search_results),
                        'unique_domains': len(unique_domains),
                        'domain_distribution': domain_counts,
                        'result_types': type_counts,
                    },
                },
                'processing_method': 'serpapi',
            }
        except httpx.TimeoutException:
            return {'success': False, 'error': 'SerpAPI timeout (>20s)', 'query': query}
        except Exception as e:
            return {'success': False, 'error': f'SerpAPI error: {str(e)}', 'query': query}

    async def _search_with_custom_api(
        self,
        query: str,
        num_results: int,
        language: str,
        region: str,
        search_genre: Optional[str],
        validation: Dict[str, Any],
        record_rate_limit: bool = False
    ) -> Dict[str, Any]:
        """Search using Google Custom Search API"""
        result = await self.custom_search_client.search(
            query, num_results, language, region
        )

        if record_rate_limit:
            status = '429' if result.get('status_code') == 429 else ('success' if result['success'] else 'error')
            self.rate_limiter.record_request('custom_search', status)

        if result['success']:
            results = result['results']
            domains = [r['domain'] for r in results]
            unique_domains = list(set(domains))
            domain_counts = {domain: domains.count(domain) for domain in unique_domains}

            type_counts = {}
            for r in results:
                result_type = r['type']
                type_counts[result_type] = type_counts.get(result_type, 0) + 1

            result['search_metadata'] = {
                'query_info': validation,
                'search_params': {
                    'num_results_requested': num_results,
                    'language': language,
                    'region': region,
                    'safe_search': True,
                    'search_genre': search_genre,
                    'enhanced_query': query
                },
                'result_stats': {
                    'total_results': len(results),
                    'unique_domains': len(unique_domains),
                    'domain_distribution': domain_counts,
                    'result_types': type_counts
                }
            }
        else:
            if ('No search results found' in result.get('error', '') or
                result.get('total_results', 0) == 0):
                suggestions = self._generate_simplified_query_suggestions(query)
                if suggestions:
                    quoted_suggestions = [f"'{s}'" for s in suggestions]
                    result['suggestion'] = f"Try these simpler searches: {', '.join(quoted_suggestions)}"
                    result['alternative_queries'] = suggestions

        return result

    def _is_rate_limit_error(self, result: Dict[str, Any]) -> bool:
        """Check if result indicates a rate limiting error"""
        if not result.get('success', True):
            error_msg = result.get('error', '').lower()
            return (
                result.get('status_code') == 429 or
                '429' in error_msg or
                'rate limit' in error_msg or
                'too many requests' in error_msg
            )
        return False

    def _enhance_query_with_genre(self, query: str, genre: Optional[str]) -> str:
        """Enhance search query based on specified genre"""
        if not genre:
            return query

        genre_enhancements = {
            'pdf': 'filetype:pdf',
            'documents': 'filetype:pdf OR filetype:doc OR filetype:docx',
            'presentations': 'filetype:ppt OR filetype:pptx',
            'spreadsheets': 'filetype:xls OR filetype:xlsx',
            'japanese': 'site:jp OR lang:ja',
            'english': 'lang:en'
        }

        enhancement = genre_enhancements.get(genre.lower())
        if enhancement:
            enhanced_query = f"{query} ({enhancement})"
            return enhanced_query
        else:
            return query

    def get_available_genres(self) -> Dict[str, str]:
        """Get list of available search genres with descriptions"""
        return {
            'pdf': 'PDF documents only',
            'documents': 'Document files (PDF, Word, etc.)',
            'presentations': 'Presentation files (PowerPoint, etc.)',
            'spreadsheets': 'Spreadsheet files (Excel, etc.)',
            'japanese': 'Japanese language content and .jp domains',
            'english': 'English language content'
        }

    # _extract_title_and_snippet and _classify_url are inherited from GoogleSearchAnalysisMixin

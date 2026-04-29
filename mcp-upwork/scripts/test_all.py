#!/usr/bin/env python
"""Test script for Upwork MCP server."""

import asyncio
import sys
from pathlib import Path

# Add src to path for local testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from upwork_mcp.browser.client import UpworkBrowser, PROFILE_DIR


async def run_tests():
    """Run all tests and report results."""
    results = []
    browser = None

    print("=" * 60)
    print("UPWORK MCP SERVER - TEST SUITE")
    print("=" * 60)
    print()

    # Test 1: Profile directory
    print("Test 1: Profile Directory...")
    try:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        if PROFILE_DIR.exists():
            results.append(("Profile Directory", "PASS", f"Path: {PROFILE_DIR}"))
        else:
            results.append(("Profile Directory", "FAIL", "Could not create directory"))
    except Exception as e:
        results.append(("Profile Directory", "FAIL", str(e)))

    # Test 2: Browser Launch
    print("Test 2: Browser Launch...")
    try:
        browser = UpworkBrowser(headless=True, timeout=30000)
        page = await browser.start()
        if page:
            results.append(("Browser Launch", "PASS", "Page object returned"))
        else:
            results.append(("Browser Launch", "FAIL", "No page returned"))
    except Exception as e:
        results.append(("Browser Launch", "FAIL", str(e)))
        print_results(results)
        return results

    # Test 3: Navigation
    print("Test 3: Navigation...")
    try:
        page = await browser.get_page()
        await page.goto("https://www.upwork.com", wait_until="domcontentloaded")
        title = await page.title()
        if "Upwork" in title:
            results.append(("Navigation", "PASS", f"Title: {title[:50]}"))
        else:
            results.append(("Navigation", "WARN", f"Unexpected title: {title[:50]}"))
    except Exception as e:
        results.append(("Navigation", "FAIL", str(e)))

    # Test 4: Login Check
    print("Test 4: Login Check...")
    logged_in = False
    try:
        logged_in = await browser.is_logged_in()
        if logged_in:
            results.append(("Login Check", "PASS", "User is logged in"))
        else:
            results.append(("Login Check", "WARN", "Not logged in - run 'uvx upwork-mcp --login' first"))
    except Exception as e:
        results.append(("Login Check", "FAIL", str(e)))

    # Tests requiring login
    if logged_in:
        # Test 5: Job Search Page
        print("Test 5: Job Search Page...")
        try:
            page = await browser.navigate("https://www.upwork.com/nx/find-work/best-matches")
            await asyncio.sleep(2)
            # Check for job tiles
            jobs = await page.query_selector_all('article, [data-test="job-tile-list"]')
            if len(jobs) > 0:
                results.append(("Job Search Page", "PASS", f"Found {len(jobs)} elements"))
            else:
                results.append(("Job Search Page", "WARN", "No job elements found (page structure may have changed)"))
        except Exception as e:
            results.append(("Job Search Page", "FAIL", str(e)))

        # Test 6: Proposals Page
        print("Test 6: Proposals Page...")
        try:
            page = await browser.navigate("https://www.upwork.com/nx/proposals/")
            await asyncio.sleep(2)
            current_url = page.url
            if "proposals" in current_url:
                results.append(("Proposals Page", "PASS", "Page loaded"))
            else:
                results.append(("Proposals Page", "WARN", f"Redirected to: {current_url}"))
        except Exception as e:
            results.append(("Proposals Page", "FAIL", str(e)))

        # Test 7: Messages Page
        print("Test 7: Messages Page...")
        try:
            page = await browser.navigate("https://www.upwork.com/nx/messages")
            await asyncio.sleep(2)
            current_url = page.url
            if "messages" in current_url:
                results.append(("Messages Page", "PASS", "Page loaded"))
            else:
                results.append(("Messages Page", "WARN", f"Redirected to: {current_url}"))
        except Exception as e:
            results.append(("Messages Page", "FAIL", str(e)))

    # Test 8: Browser Close
    print("Test 8: Browser Close...")
    try:
        await browser.close()
        results.append(("Browser Close", "PASS", "Browser closed successfully"))
    except Exception as e:
        results.append(("Browser Close", "FAIL", str(e)))

    # Print results
    print_results(results)
    return results


def print_results(results):
    """Print formatted test results."""
    print()
    print("=" * 60)
    print("TEST RESULTS")
    print("=" * 60)
    print()

    passed = 0
    warned = 0
    failed = 0

    for name, status, detail in results:
        if status == "PASS":
            emoji = "[PASS]"
            passed += 1
        elif status == "WARN":
            emoji = "[WARN]"
            warned += 1
        else:
            emoji = "[FAIL]"
            failed += 1

        print(f"{emoji} {name}")
        print(f"       {detail}")
        print()

    print("-" * 60)
    print(f"Total: {len(results)} | Passed: {passed} | Warnings: {warned} | Failed: {failed}")
    print("-" * 60)

    if failed == 0 and warned == 0:
        print("All tests passed!")
    elif failed == 0:
        print("Tests passed with warnings.")
    else:
        print("Some tests failed. Check the output above.")


if __name__ == "__main__":
    asyncio.run(run_tests())

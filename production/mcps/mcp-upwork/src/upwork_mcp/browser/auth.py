"""Authentication flow for Upwork using CDP."""

import asyncio
from pathlib import Path
import shutil
from .client import (
    UpworkBrowser,
    find_chrome,
    is_chrome_running_with_debug,
    start_chrome_with_debug,
    CDP_PORT,
    PROFILE_DIR,
)


async def login_interactive(timeout_minutes: int = 5):
    """Open Chrome for manual login to Upwork.

    Uses CDP to connect to a real Chrome instance, avoiding
    the "controlled by automated test software" detection.

    Args:
        timeout_minutes: How long to wait for user to complete login
    """
    print("=" * 60)
    print("UPWORK LOGIN (CDP Mode)")
    print("=" * 60)
    print()

    # Ensure Chrome is running with debug port
    if not is_chrome_running_with_debug():
        chrome_path = find_chrome()
        if not chrome_path:
            print("ERROR: Chrome not found. Please install Google Chrome.")
            return

        print("Starting Chrome...")
        if not start_chrome_with_debug():
            print(f"ERROR: Could not start Chrome with debug port.")
            print(f"Please start Chrome manually with:")
            print(f'  "{chrome_path}" --remote-debugging-port={CDP_PORT}')
            return

        await asyncio.sleep(2)

    browser = UpworkBrowser(timeout=60000)

    try:
        page = await browser.start()

        print(f"Chrome connected! You have {timeout_minutes} minutes to log in.")
        print()
        print("Steps:")
        print("  1. Click 'Verify you are human' if Cloudflare appears")
        print("  2. Enter your email/username")
        print("  3. Enter your password")
        print("  4. Complete any 2FA challenges")
        print("  5. Wait until you see the Upwork dashboard")
        print()
        print("Your session will be saved automatically.")
        print("=" * 60)

        await page.goto("https://www.upwork.com/ab/account-security/login")

        # Wait for successful login - user should be redirected to dashboard
        start_time = asyncio.get_event_loop().time()
        timeout_seconds = timeout_minutes * 60

        while True:
            await asyncio.sleep(3)

            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout_seconds:
                print()
                print("Timeout reached.")
                break

            try:
                current_url = page.url
                title = await page.title()

                # Check if logged in (on dashboard)
                if "/nx/" in current_url and "login" not in current_url.lower():
                    if "moment" not in title.lower():
                        print()
                        print("Login successful!")
                        print(f"Session saved to {PROFILE_DIR}")
                        print()
                        print("You can now use the Upwork MCP server.")
                        break
            except Exception:
                pass  # Page might be navigating

    except Exception as e:
        print()
        print(f"Error: {e}")
        print()
        print("Please try again with: uvx upwork-mcp --login")

    finally:
        await browser.close()


async def check_session() -> bool:
    """Check if existing session is valid."""
    if not is_chrome_running_with_debug():
        # Start Chrome to check session
        start_chrome_with_debug()
        await asyncio.sleep(2)

    browser = UpworkBrowser()
    try:
        await browser.start()
        return await browser.is_logged_in()
    except Exception:
        return False
    finally:
        await browser.close()


async def logout():
    """Clear saved session."""
    if PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR)
        print("Session cleared.")
    else:
        print("No session to clear.")

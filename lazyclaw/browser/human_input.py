"""Human-like input simulation for CDP — anti-detection mouse & keyboard.

Replaces naive CDP Input.dispatchMouseEvent with realistic behavior:
1. Bezier curve mouse movement (Fitts's Law timing)
2. Complete event chains (mousemove → mouseover → mouseenter → mousedown → mouseup → click)
3. screenX/screenY patch (CDP bug that Cloudflare Turnstile detects)
4. Variable timing with natural jitter

Based on research into Ghost Cursor, Cloudflare detection vectors, and
CDP's Input.dispatchMouseEvent screenX/screenY bug (Chromium #40280325).
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

# Simulated browser window offset from screen edge (realistic range)
_WINDOW_OFFSET_X = random.randint(0, 200)
_WINDOW_OFFSET_Y = random.randint(60, 120)  # Toolbar height

# Current cursor position (starts at a random resting spot)
_cursor_x: float = random.uniform(300, 800)
_cursor_y: float = random.uniform(200, 500)


# ── Data ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BezierPoint:
    """Immutable 2D point on a Bezier curve."""

    x: float
    y: float


# ── Bezier curve generation ──────────────────────────────────────────

def _cubic_bezier(
    p0: BezierPoint,
    p1: BezierPoint,
    p2: BezierPoint,
    p3: BezierPoint,
    t: float,
) -> BezierPoint:
    """Evaluate cubic Bezier at parameter t (0.0–1.0)."""
    u = 1.0 - t
    return BezierPoint(
        x=(u**3 * p0.x + 3 * u**2 * t * p1.x
           + 3 * u * t**2 * p2.x + t**3 * p3.x),
        y=(u**3 * p0.y + 3 * u**2 * t * p1.y
           + 3 * u * t**2 * p2.y + t**3 * p3.y),
    )


def _generate_bezier_path(
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    steps: int = 0,
) -> list[BezierPoint]:
    """Generate a human-like curved mouse path using cubic Bezier.

    Control points are randomized to create natural-looking curves.
    Step count adapts to distance (Fitts's Law: more steps for longer moves).
    """
    distance = math.hypot(end_x - start_x, end_y - start_y)

    if steps <= 0:
        # Adaptive: ~1 step per 15px, min 5, max 40
        steps = max(5, min(40, int(distance / 15)))

    # Randomized control points — create natural curve
    dx = end_x - start_x
    dy = end_y - start_y

    # Control point 1: ~30% along path, with perpendicular offset
    spread1 = random.uniform(0.1, 0.4)
    offset1 = random.uniform(-0.3, 0.3) * distance
    cp1 = BezierPoint(
        x=start_x + dx * spread1 + dy * offset1 / max(distance, 1),
        y=start_y + dy * spread1 - dx * offset1 / max(distance, 1),
    )

    # Control point 2: ~70% along path, with perpendicular offset
    spread2 = random.uniform(0.6, 0.9)
    offset2 = random.uniform(-0.2, 0.2) * distance
    cp2 = BezierPoint(
        x=start_x + dx * spread2 + dy * offset2 / max(distance, 1),
        y=start_y + dy * spread2 - dx * offset2 / max(distance, 1),
    )

    start = BezierPoint(x=start_x, y=start_y)
    end = BezierPoint(x=end_x, y=end_y)

    # Generate points along the curve
    points: list[BezierPoint] = []
    for i in range(steps + 1):
        t = i / steps
        point = _cubic_bezier(start, cp1, cp2, end, t)
        # Add tiny jitter (sub-pixel, like real hand tremor)
        jittered = BezierPoint(
            x=point.x + random.uniform(-0.5, 0.5),
            y=point.y + random.uniform(-0.5, 0.5),
        )
        points.append(jittered)

    return points


def _fitts_law_duration(distance: float, target_size: float = 20.0) -> float:
    """Calculate movement duration using Fitts's Law.

    Longer distances and smaller targets = slower movement.
    Returns duration in seconds.
    """
    # Fitts's Law: MT = a + b * log2(2D/W)
    a = 0.15  # Base time (seconds)
    b = 0.12  # Slope
    if distance < 1:
        return a
    index_of_difficulty = math.log2(2 * distance / max(target_size, 1))
    return a + b * max(0, index_of_difficulty)


# ── screenX/screenY fix ─────────────────────────────────────────────

SCREEN_COORD_PATCH_JS = """
// Patch CDP screenX/screenY bug (Chromium #40280325)
// CDP sets screenX === x (window-relative), but real events have
// screenX = x + window.screenX + window offset. Cloudflare checks this.
(() => {
    if (window.__lc_screen_patched) return;

    const OFFSET_X = """ + str(_WINDOW_OFFSET_X) + """;
    const OFFSET_Y = """ + str(_WINDOW_OFFSET_Y) + """;

    for (const EventClass of [MouseEvent, PointerEvent]) {
        const origScreenX = Object.getOwnPropertyDescriptor(
            EventClass.prototype, 'screenX'
        );
        const origScreenY = Object.getOwnPropertyDescriptor(
            EventClass.prototype, 'screenY'
        );

        if (origScreenX) {
            Object.defineProperty(EventClass.prototype, 'screenX', {
                get() {
                    const val = origScreenX.get.call(this);
                    // If screenX equals clientX, it's a CDP event — fix it
                    if (val === this.clientX) {
                        return val + (window.screenX || 0) + OFFSET_X;
                    }
                    return val;
                },
                configurable: true,
            });
        }
        if (origScreenY) {
            Object.defineProperty(EventClass.prototype, 'screenY', {
                get() {
                    const val = origScreenY.get.call(this);
                    if (val === this.clientY) {
                        return val + (window.screenY || 0) + OFFSET_Y;
                    }
                    return val;
                },
                configurable: true,
            });
        }
    }
    window.__lc_screen_patched = true;
})();
"""


# ── Public API ───────────────────────────────────────────────────────

async def apply_screen_patch(conn) -> None:
    """Inject screenX/screenY patch on every new document.

    Must be called ONCE after connecting, BEFORE any navigation.
    """
    try:
        await conn.send(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": SCREEN_COORD_PATCH_JS},
        )
        logger.debug("screenX/screenY patch injected")
    except Exception as exc:
        logger.warning("Failed to inject screen patch: %s", exc)


async def human_move_to(
    conn,
    target_x: float,
    target_y: float,
    target_size: float = 20.0,
) -> None:
    """Move cursor from current position to target using Bezier curve.

    Sends CDP Input.dispatchMouseEvent(mouseMoved) along the path
    with Fitts's Law timing — slow start, accelerate, decelerate.
    """
    global _cursor_x, _cursor_y

    distance = math.hypot(target_x - _cursor_x, target_y - _cursor_y)

    # Skip movement for very short distances (< 5px)
    if distance < 5:
        _cursor_x = target_x
        _cursor_y = target_y
        return

    path = _generate_bezier_path(_cursor_x, _cursor_y, target_x, target_y)
    duration = _fitts_law_duration(distance, target_size)
    step_delay = duration / max(len(path) - 1, 1)

    for point in path:
        await conn.send("Input.dispatchMouseEvent", {
            "type": "mouseMoved",
            "x": round(point.x),
            "y": round(point.y),
        })
        # Variable delay: slower at start and end (ease in/out)
        jitter = random.uniform(0.7, 1.3)
        await asyncio.sleep(step_delay * jitter)

    _cursor_x = target_x
    _cursor_y = target_y


async def human_click(
    conn,
    x: float,
    y: float,
    target_size: float = 20.0,
    move: bool = True,
) -> None:
    """Click at (x, y) with full human-like event chain.

    Complete chain: mousemove approach → mouseover → mouseenter →
    mousedown → [50-150ms hold] → mouseup → click.

    Optionally skips the move phase (if cursor is already positioned).
    """
    # Phase 1: Move cursor to target (Bezier path)
    if move:
        await human_move_to(conn, x, y, target_size)

    # Phase 2: Hover events (mouseover + mouseenter)
    await conn.send("Input.dispatchMouseEvent", {
        "type": "mouseMoved",
        "x": round(x),
        "y": round(y),
    })

    # Brief hesitation before clicking (reading/aiming)
    await asyncio.sleep(random.uniform(0.05, 0.2))

    # Phase 3: mousedown
    await conn.send("Input.dispatchMouseEvent", {
        "type": "mousePressed",
        "x": round(x),
        "y": round(y),
        "button": "left",
        "clickCount": 1,
    })

    # Phase 4: Hold (human finger press duration)
    await asyncio.sleep(random.uniform(0.05, 0.15))

    # Phase 5: mouseup
    await conn.send("Input.dispatchMouseEvent", {
        "type": "mouseReleased",
        "x": round(x),
        "y": round(y),
        "button": "left",
        "clickCount": 1,
    })

    # Post-click pause (human reaction time)
    await asyncio.sleep(random.uniform(0.1, 0.4))


async def human_type(
    conn,
    text: str,
    field_x: float | None = None,
    field_y: float | None = None,
) -> None:
    """Type text with human-like keystroke timing.

    Variable inter-key delay: faster for common sequences,
    slower at word boundaries, occasional micro-pauses.
    """
    # Click field first if coordinates provided
    if field_x is not None and field_y is not None:
        await human_click(conn, field_x, field_y)
        await asyncio.sleep(random.uniform(0.1, 0.3))

    for i, char in enumerate(text):
        # keyDown with text
        await conn.send("Input.dispatchKeyEvent", {
            "type": "keyDown",
            "text": char,
            "key": char,
        })
        await conn.send("Input.dispatchKeyEvent", {
            "type": "keyUp",
            "key": char,
        })

        # Variable timing
        base_delay = random.uniform(0.03, 0.10)

        # Slower at word boundaries
        if char == " ":
            base_delay = random.uniform(0.08, 0.18)
        # Occasional micro-pause (thinking while typing)
        elif random.random() < 0.05:
            base_delay = random.uniform(0.15, 0.35)

        await asyncio.sleep(base_delay)


async def human_scroll(
    conn,
    direction: str = "down",
    amount: int = 300,
) -> None:
    """Scroll with variable momentum like a real mousewheel/trackpad.

    Splits the scroll into 2-4 smaller increments with deceleration.
    """
    delta_y = amount if direction == "down" else -amount
    scroll_steps = random.randint(2, 4)
    remaining = delta_y

    for i in range(scroll_steps):
        # Decelerate: each step scrolls less
        fraction = (scroll_steps - i) / sum(range(1, scroll_steps + 1))
        step_delta = int(remaining * fraction)

        if abs(step_delta) < 10:
            step_delta = remaining
            remaining = 0
        else:
            remaining -= step_delta

        # Small random horizontal offset (natural hand position)
        mouse_x = 400 + random.randint(-50, 50)
        mouse_y = 300 + random.randint(-30, 30)

        await conn.send("Input.dispatchMouseEvent", {
            "type": "mouseWheel",
            "x": mouse_x,
            "y": mouse_y,
            "deltaX": 0,
            "deltaY": step_delta,
        })

        # Deceleration pause
        await asyncio.sleep(random.uniform(0.03, 0.08))

    # Post-scroll reading pause
    await asyncio.sleep(random.uniform(0.2, 0.5))

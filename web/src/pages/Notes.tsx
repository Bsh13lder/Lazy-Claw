import { useEffect } from "react";

/**
 * Legacy `/?page=notes` route. The Notes surface now lives inside the
 * Tasks workspace as a tab. We redirect any old bookmark or Telegram
 * deep-link to the canonical tab URL without losing scroll/state.
 *
 * Kept as a thin redirect (not deleted) so the Telegram confirmation
 * toast that says "Open the Notes page on the Web UI" still resolves
 * to a working surface.
 */
export default function NotesRedirect() {
  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("page", "tasks");
    url.searchParams.set("tab", "notes");
    window.history.replaceState(null, "", url.toString());
    // Force the App router to re-read the URL.
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, []);

  return (
    <div className="grid-bg min-h-full flex items-center justify-center text-text-muted text-[12px]">
      Opening Notes…
    </div>
  );
}

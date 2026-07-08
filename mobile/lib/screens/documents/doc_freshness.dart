/// Staleness comparison for cached documents vs. the server's latest.
///
/// A cached copy (PDF bytes / Univer snapshot) carries the `updated_at` it was
/// fetched with. The list provider — kept current by the `/changes` sync —
/// carries the server's latest `updated_at`. When the server's is strictly
/// newer, the on-device copy is stale and should be reloaded.
///
/// Both stamps are ISO-8601 UTC produced by the same server clock, so a plain
/// lexicographic compare orders them correctly. Unknowns (`null`) are treated
/// as NOT newer: prefer the cache and never thrash when offline or before the
/// list has loaded.
library;

bool isServerNewer(String? have, String? server) =>
    have != null && server != null && server.compareTo(have) > 0;

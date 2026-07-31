/// Pure gating logic for the Ledger's inbox bulk-selection mode.
///
/// Selection mode should only survive while the Ledger's project filter is
/// actively the inbox project. Comparing ids directly
/// (`projectFilter != inboxProjectId`) has a null-collapsing trap: when the
/// inbox project is deleted, `projectFilter` collapses to null (the Ledger's
/// pre-existing stale-filter reset already does this) AND `inboxProjectId` is
/// ALSO null (the project is gone from `state.projects`) — `null != null` is
/// `false`, so that naive comparison never fires. The result was every row
/// across the WHOLE Ledger getting stuck in selection mode forever, with no
/// visible way out: the bulk bar (and its ✕ cancel) only render while the
/// inbox filter is actively selected, so it vanishes right along with the
/// ability to escape.
///
/// This resolves "is the inbox filter actually active" FIRST as its own
/// boolean, then asks whether selection mode should exit — so a null filter
/// and a null inbox id can never be mistaken for "still on the inbox".
bool shouldExitSelection({
  required bool selectionMode,
  required String? projectFilter,
  required String? inboxProjectId,
}) {
  final inboxActive =
      inboxProjectId != null && projectFilter == inboxProjectId;
  return selectionMode && !inboxActive;
}

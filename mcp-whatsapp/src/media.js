"use strict";

// Pure media helpers for the WhatsApp MCP — no Baileys import, no I/O.
// index.js uses these to (a) surface media metadata on every read so the
// agent KNOWS a bubble carries a voice note / photo / file, (b) name
// downloaded files, and (c) build Baileys send payloads for the
// whatsapp_send_audio / _video / _document tools.

// Mime ↔ extension tables. Keep the two directions separate: several
// extensions can map to one mime, and the download path only ever needs
// ONE canonical extension per mime.
const MIME_TO_EXT = {
  "image/jpeg": "jpg",
  "image/png": "png",
  "image/webp": "webp",
  "image/gif": "gif",
  "video/mp4": "mp4",
  "video/quicktime": "mov",
  "video/3gpp": "3gp",
  "audio/ogg": "ogg",
  "audio/mpeg": "mp3",
  "audio/mp4": "m4a",
  "audio/aac": "aac",
  "audio/wav": "wav",
  "audio/x-wav": "wav",
  "application/pdf": "pdf",
  "application/msword": "doc",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
  "application/vnd.ms-excel": "xls",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
  "text/plain": "txt",
  "text/csv": "csv",
  "application/zip": "zip",
};

const EXT_TO_MIME = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
  gif: "image/gif",
  mp4: "video/mp4",
  mov: "video/quicktime",
  "3gp": "video/3gpp",
  // WhatsApp voice notes are ogg/opus — sending .ogg as a voice note with
  // this mimetype renders the native play-bubble on the recipient side.
  ogg: "audio/ogg; codecs=opus",
  opus: "audio/ogg; codecs=opus",
  mp3: "audio/mpeg",
  m4a: "audio/mp4",
  aac: "audio/aac",
  wav: "audio/wav",
  pdf: "application/pdf",
  doc: "application/msword",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xls: "application/vnd.ms-excel",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  txt: "text/plain",
  csv: "text/csv",
  zip: "application/zip",
};

// Baileys wraps media one level deep for disappearing / view-once chats.
const WRAPPER_KEYS = ["ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2"];

/** Unwrap ephemeral / view-once containers to the inner message content. */
function unwrapMessageContent(content) {
  if (!content || typeof content !== "object") return content;
  for (const key of WRAPPER_KEYS) {
    if (content[key] && content[key].message) return content[key].message;
  }
  return content;
}

const MEDIA_KINDS = [
  ["imageMessage", "image"],
  ["videoMessage", "video"],
  ["audioMessage", "audio"],
  ["documentMessage", "document"],
  ["stickerMessage", "sticker"],
];

/** Coerce Baileys fileLength (number | string | Long) to a plain number. */
function _toNumber(value) {
  if (value == null) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/**
 * Describe the media attached to a raw Baileys message, or null for text.
 *
 * Returns a NEW plain object (never mutates the message):
 *   { kind, mimetype, file_name, seconds, voice_note, size_bytes }
 */
function describeMedia(msg) {
  const content = unwrapMessageContent(msg && msg.message);
  if (!content || typeof content !== "object") return null;

  for (const [key, kind] of MEDIA_KINDS) {
    const media = content[key];
    if (!media) continue;
    return {
      kind,
      mimetype: media.mimetype || "application/octet-stream",
      file_name: media.fileName || null,
      seconds: _toNumber(media.seconds),
      voice_note: media.ptt === true,
      size_bytes: _toNumber(media.fileLength),
    };
  }
  return null;
}

/** Canonical file extension for a mimetype ("audio/ogg; codecs=opus" → "ogg"). */
function extForMime(mimetype) {
  if (!mimetype || typeof mimetype !== "string") return "bin";
  const base = mimetype.split(";")[0].trim().toLowerCase();
  return MIME_TO_EXT[base] || "bin";
}

/** Mimetype for a local file path, by extension (case-insensitive). */
function mimeForPath(filePath) {
  const ext = String(filePath || "").split(".").pop().toLowerCase();
  return EXT_TO_MIME[ext] || "application/octet-stream";
}

/**
 * Build the Baileys sendMessage payload for a media kind.
 *
 * opts: { mimetype, caption?, fileName?, voiceNote? }
 *   - audio: voiceNote defaults true (WhatsApp voice-note bubble);
 *     pass voiceNote=false to send as a plain audio file.
 */
function buildSendPayload(kind, buffer, opts) {
  const o = opts || {};
  switch (kind) {
    case "image":
      return { image: buffer, caption: o.caption || undefined };
    case "audio":
      return {
        audio: buffer,
        mimetype: o.mimetype || "audio/ogg; codecs=opus",
        ptt: o.voiceNote !== false,
      };
    case "video":
      return {
        video: buffer,
        mimetype: o.mimetype || "video/mp4",
        caption: o.caption || undefined,
      };
    case "document":
      return {
        document: buffer,
        fileName: o.fileName || "file",
        mimetype: o.mimetype || "application/octet-stream",
        caption: o.caption || undefined,
      };
    default:
      throw new Error(`Unknown media kind: ${kind}`);
  }
}

module.exports = {
  describeMedia,
  extForMime,
  mimeForPath,
  buildSendPayload,
  unwrapMessageContent,
};

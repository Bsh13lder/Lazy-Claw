"use strict";

// Pure-helper tests for src/media.js — run with `node --test tests/`.
// No Baileys / network needed: describeMedia & friends only inspect plain
// message objects shaped like Baileys WAMessage.

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  describeMedia,
  extForMime,
  mimeForPath,
  buildSendPayload,
  isNonContentMessage,
  canonicalChatJid,
} = require("../src/media.js");

// ---------------------------------------------------------------------------
// describeMedia
// ---------------------------------------------------------------------------

test("describeMedia: plain text message returns null", () => {
  assert.equal(describeMedia({ message: { conversation: "hi" } }), null);
  assert.equal(describeMedia({}), null);
  assert.equal(describeMedia(null), null);
});

test("describeMedia: image message", () => {
  const msg = {
    message: {
      imageMessage: { mimetype: "image/jpeg", fileLength: 52333, caption: "look" },
    },
  };
  const m = describeMedia(msg);
  assert.equal(m.kind, "image");
  assert.equal(m.mimetype, "image/jpeg");
  assert.equal(m.size_bytes, 52333);
  assert.equal(m.voice_note, false);
});

test("describeMedia: voice note (ptt audio)", () => {
  const msg = {
    message: {
      audioMessage: {
        mimetype: "audio/ogg; codecs=opus",
        seconds: 14,
        ptt: true,
        fileLength: "9001", // Baileys Long often serialises to string
      },
    },
  };
  const m = describeMedia(msg);
  assert.equal(m.kind, "audio");
  assert.equal(m.voice_note, true);
  assert.equal(m.seconds, 14);
  assert.equal(m.size_bytes, 9001);
});

test("describeMedia: document carries file_name", () => {
  const msg = {
    message: {
      documentMessage: {
        mimetype: "application/pdf",
        fileName: "invoice.pdf",
        fileLength: 1024,
      },
    },
  };
  const m = describeMedia(msg);
  assert.equal(m.kind, "document");
  assert.equal(m.file_name, "invoice.pdf");
});

test("describeMedia: video with duration", () => {
  const msg = {
    message: { videoMessage: { mimetype: "video/mp4", seconds: 33 } },
  };
  const m = describeMedia(msg);
  assert.equal(m.kind, "video");
  assert.equal(m.seconds, 33);
});

test("describeMedia: sticker", () => {
  const msg = { message: { stickerMessage: { mimetype: "image/webp" } } };
  assert.equal(describeMedia(msg).kind, "sticker");
});

test("describeMedia: unwraps ephemeral / view-once wrappers", () => {
  const inner = { imageMessage: { mimetype: "image/png" } };
  for (const wrapper of [
    { ephemeralMessage: { message: inner } },
    { viewOnceMessage: { message: inner } },
    { viewOnceMessageV2: { message: inner } },
  ]) {
    const m = describeMedia({ message: wrapper });
    assert.ok(m, `wrapper ${Object.keys(wrapper)[0]} should unwrap`);
    assert.equal(m.kind, "image");
  }
});

// ---------------------------------------------------------------------------
// extForMime / mimeForPath
// ---------------------------------------------------------------------------

test("extForMime: common types", () => {
  assert.equal(extForMime("image/jpeg"), "jpg");
  assert.equal(extForMime("audio/ogg; codecs=opus"), "ogg");
  assert.equal(extForMime("application/pdf"), "pdf");
  assert.equal(extForMime("video/mp4"), "mp4");
  assert.equal(extForMime("totally/unknown"), "bin");
  assert.equal(extForMime(null), "bin");
});

test("mimeForPath: by extension, case-insensitive", () => {
  assert.equal(mimeForPath("/tmp/photo.JPG"), "image/jpeg");
  assert.equal(mimeForPath("voice.ogg"), "audio/ogg; codecs=opus");
  assert.equal(mimeForPath("song.mp3"), "audio/mpeg");
  assert.equal(mimeForPath("doc.pdf"), "application/pdf");
  assert.equal(mimeForPath("mystery.xyz"), "application/octet-stream");
});

// ---------------------------------------------------------------------------
// buildSendPayload
// ---------------------------------------------------------------------------

test("buildSendPayload: audio voice note defaults to ptt=true", () => {
  const buf = Buffer.from("x");
  const p = buildSendPayload("audio", buf, { mimetype: "audio/ogg; codecs=opus" });
  assert.equal(p.audio, buf);
  assert.equal(p.ptt, true);
  assert.equal(p.mimetype, "audio/ogg; codecs=opus");
});

test("buildSendPayload: audio with voiceNote=false is a plain audio file", () => {
  const p = buildSendPayload("audio", Buffer.from("x"), {
    mimetype: "audio/mpeg",
    voiceNote: false,
  });
  assert.equal(p.ptt, false);
});

test("buildSendPayload: video with caption", () => {
  const buf = Buffer.from("v");
  const p = buildSendPayload("video", buf, { caption: "clip", mimetype: "video/mp4" });
  assert.equal(p.video, buf);
  assert.equal(p.caption, "clip");
});

test("buildSendPayload: document requires fileName + mimetype", () => {
  const buf = Buffer.from("d");
  const p = buildSendPayload("document", buf, {
    fileName: "report.pdf",
    mimetype: "application/pdf",
    caption: "monthly",
  });
  assert.equal(p.document, buf);
  assert.equal(p.fileName, "report.pdf");
  assert.equal(p.mimetype, "application/pdf");
  assert.equal(p.caption, "monthly");
});

test("buildSendPayload: unknown kind throws", () => {
  assert.throws(() => buildSendPayload("hologram", Buffer.from("x"), {}));
});

// ---------------------------------------------------------------------------
// isNonContentMessage — filters "[media]" junk out of reads / previews
// ---------------------------------------------------------------------------

test("isNonContentMessage: plain text and media are content", () => {
  assert.equal(isNonContentMessage({ key: { id: "1" }, message: { conversation: "hi" } }), false);
  assert.equal(isNonContentMessage({ key: { id: "2" }, message: { extendedTextMessage: { text: "yo" } } }), false);
  assert.equal(isNonContentMessage({ key: { id: "3" }, message: { imageMessage: { mimetype: "image/jpeg" } } }), false);
  assert.equal(isNonContentMessage({ key: { id: "4" }, message: { audioMessage: { ptt: true } } }), false);
});

test("isNonContentMessage: system stub (null body) is junk", () => {
  assert.equal(isNonContentMessage({ key: { id: "1" }, messageStubType: 27, message: null }), true);
  assert.equal(isNonContentMessage({ key: { id: "2" } }), true);
  assert.equal(isNonContentMessage(null), true);
  assert.equal(isNonContentMessage({}), true);
});

test("isNonContentMessage: reaction / protocol / senderKey / poll-vote are junk", () => {
  assert.equal(isNonContentMessage({ key: { id: "1" }, message: { reactionMessage: { text: "👍" } } }), true);
  assert.equal(isNonContentMessage({ key: { id: "2" }, message: { protocolMessage: { type: 0 } } }), true);
  assert.equal(isNonContentMessage({ key: { id: "3" }, message: { senderKeyDistributionMessage: {} } }), true);
  assert.equal(isNonContentMessage({ key: { id: "4" }, message: { pollUpdateMessage: {} } }), true);
  assert.equal(isNonContentMessage({ key: { id: "5" }, message: { messageContextInfo: {} } }), true);
});

test("isNonContentMessage: housekeeping ALONGSIDE real content is kept", () => {
  assert.equal(isNonContentMessage({ key: { id: "1" }, message: { senderKeyDistributionMessage: {}, conversation: "hi" } }), false);
  assert.equal(isNonContentMessage({ key: { id: "2" }, message: { messageContextInfo: {}, extendedTextMessage: { text: "yo" } } }), false);
});

test("isNonContentMessage: ephemeral / view-once unwrap before the check", () => {
  assert.equal(isNonContentMessage({ key: { id: "1" }, message: { ephemeralMessage: { message: { conversation: "secret" } } } }), false);
  assert.equal(isNonContentMessage({ key: { id: "2" }, message: { ephemeralMessage: { message: { reactionMessage: { text: "❤️" } } } } }), true);
});

test("isNonContentMessage: unknown/future content types are KEPT (denylist, not allowlist)", () => {
  assert.equal(isNonContentMessage({ key: { id: "1" }, message: { groupInviteMessage: { groupJid: "x@g.us" } } }), false);
  assert.equal(isNonContentMessage({ key: { id: "2" }, message: { locationMessage: { degreesLatitude: 40 } } }), false);
});

// ---------------------------------------------------------------------------
// canonicalChatJid — collapses @lid / device-suffix twins to one thread key
// ---------------------------------------------------------------------------

test("canonicalChatJid: @lid maps to its phone twin when linked", () => {
  const map = new Map([["55911@lid", "34600111222@s.whatsapp.net"]]);
  assert.equal(canonicalChatJid("55911@lid", map), "34600111222@s.whatsapp.net");
});

test("canonicalChatJid: unlinked @lid passes through", () => {
  assert.equal(canonicalChatJid("99999@lid", new Map()), "99999@lid");
  assert.equal(canonicalChatJid("99999@lid"), "99999@lid");
});

test("canonicalChatJid: strips device suffix on direct JIDs", () => {
  assert.equal(canonicalChatJid("34600111222:12@s.whatsapp.net", new Map()), "34600111222@s.whatsapp.net");
});

test("canonicalChatJid: @lid linked to a :NN phone JID is fully normalized", () => {
  const map = new Map([["55911@lid", "34600111222:5@s.whatsapp.net"]]);
  assert.equal(canonicalChatJid("55911@lid", map), "34600111222@s.whatsapp.net");
});

test("canonicalChatJid: groups and plain DMs are unchanged", () => {
  assert.equal(canonicalChatJid("120363000@g.us", new Map()), "120363000@g.us");
  assert.equal(canonicalChatJid("34600111222@s.whatsapp.net", new Map()), "34600111222@s.whatsapp.net");
  assert.equal(canonicalChatJid("", new Map()), "");
  assert.equal(canonicalChatJid(null, new Map()), null);
});

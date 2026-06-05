import { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { getMobileVersion, MOBILE_APK_URL } from "../../api";

export default function MobileAppTab() {
  const [v, setV] = useState<Awaited<ReturnType<typeof getMobileVersion>>>(null);
  const [absUrl, setAbsUrl] = useState("");
  useEffect(() => {
    getMobileVersion().then(setV);
    setAbsUrl(`${window.location.origin}${MOBILE_APK_URL}`);
  }, []);

  return (
    <div className="space-y-6 max-w-xl">
      <div>
        <h3 className="text-lg font-semibold text-text-primary">LazyClaw for Android</h3>
        {v ? (
          <p className="text-sm text-text-muted">
            v{v.version} (build {v.build}) · built {new Date(v.built_at).toLocaleString()}
          </p>
        ) : (
          <p className="text-sm text-text-muted">No build published yet.</p>
        )}
      </div>

      {v && (
        <div className="flex items-center gap-6">
          <a href={MOBILE_APK_URL} download
             className="px-4 py-2 rounded-lg bg-accent text-bg-primary font-medium hover:opacity-90 transition-opacity">
            Download APK
          </a>
          <div className="bg-white p-3 rounded-lg">
            <QRCodeSVG value={absUrl} size={140} />
            <p className="text-xs text-black mt-1 text-center">Scan on your phone</p>
          </div>
        </div>
      )}

      <div className="rounded-lg border border-border bg-bg-secondary p-4 text-sm space-y-2">
        <p className="font-medium text-text-primary">Installing on Xiaomi (HyperOS):</p>
        <ol className="list-decimal ml-5 space-y-1 text-text-muted">
          <li>Scan the QR with the phone (or open this page on the phone) and Download.</li>
          <li>When prompted, allow your browser to <b className="text-text-secondary">Install unknown apps</b>.</li>
          <li>Open the app, enter your computer's address, and log in.</li>
          <li>For reliable background notifications later: Settings → Apps → LazyClaw →
              enable <b className="text-text-secondary">Autostart</b> and set battery to <b className="text-text-secondary">No restrictions</b>.</li>
        </ol>
      </div>
    </div>
  );
}

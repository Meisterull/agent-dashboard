// Externes Fenster im Workspace: bettet eine fremde Web-Oberfläche (z. B.
// noVNC auf einem Agenten-PC) als iframe ein. Adressen der Form
// "IP:Port[/pfad]" laufen über den authentifizierten nginx-Proxy /ext/ —
// damit funktionieren http-Ziele trotz https-Dashboard (Mixed-Content) und
// WebSockets (websockify). Volle https://-URLs werden direkt eingebettet.
import { t } from "../sprache";

export function resolveExternalUrl(raw) {
  const url = (raw || "").trim();
  if (!url) return null;
  if (/^https:\/\//i.test(url)) return url;
  // http:// wäre im https-Dashboard eh blockiert → wie "host:port" behandeln
  const bare = url.replace(/^http:\/\//i, "");
  const m = bare.match(/^(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})(\/.*)?$/);
  if (!m) return null;
  return `/ext/${m[1]}/${m[2]}${m[3] || "/"}`;
}

export default function ExternalFrame({ url }) {
  const src = resolveExternalUrl(url);
  if (!src)
    return (
      <div className="flex flex-1 items-center justify-center p-4 text-center text-sm text-slate-400 dark:text-slate-500">
        {t(
          "Ungültige Adresse „{0}“ — erwartet „IP:Port[/pfad]“ (LAN) oder eine volle https://-URL.",
          url,
        )}
      </div>
    );
  return (
    <iframe
      src={src}
      title={url}
      className="h-full w-full flex-1 border-0 bg-white"
      allow="clipboard-read; clipboard-write; fullscreen"
    />
  );
}

import { useEffect, useRef, useState } from "react";
import { entscheideFreigabe, getFreigaben } from "../api";
import { melden, nachfragen } from "./Dialog";
import { t } from "../sprache";

// Freigabe-Anfragen der Automatik (Issue #56): Ein headless-Lauf braucht für
// einen Werkzeug-Aufruf eine Berechtigung und WARTET — hier entscheidet der
// Mensch. Eigenes Banner statt Rückfragen-Leiste: es geht um einen konkreten
// Befehl mit Frist, nicht um eine Frage mit Freitext-Antwort.
export default function FreigabenBanner({ onNew }) {
  const [offen, setOffen] = useState([]);
  const [jetzt, setJetzt] = useState(Date.now() / 1000);
  const bekannt = useRef(null);
  const onNewRef = useRef(onNew);
  onNewRef.current = onNew;

  const laden = () =>
    getFreigaben()
      .then((d) => {
        setOffen(d.offen || []);
        setJetzt(Date.now() / 1000);
        const ids = (d.offen || []).map((a) => a.id);
        if (bekannt.current && ids.some((id) => !bekannt.current.includes(id)))
          onNewRef.current?.();
        bekannt.current = ids;
      })
      .catch(() => {});

  useEffect(() => {
    laden();
    // Der Lauf wartet — also zügig nachsehen; im Hintergrund-Tab nicht.
    const takt = setInterval(() => !document.hidden && laden(), 4000);
    const onLive = () => !document.hidden && laden();
    window.addEventListener("live:mailbox", onLive);
    document.addEventListener("visibilitychange", onLive);
    return () => {
      clearInterval(takt);
      window.removeEventListener("live:mailbox", onLive);
      document.removeEventListener("visibilitychange", onLive);
    };
  }, []);

  const entscheide = async (a, erlauben, regel) => {
    try {
      await entscheideFreigabe(a.agent, a.id, erlauben, regel);
      setOffen((liste) => liste.filter((x) => x.id !== a.id));
    } catch (e) {
      melden({ title: t("Fehler"), text: String(e.message || e) });
      laden();
    }
  };

  // „Immer"/„Nie": das Muster ist ein Vorschlag — der Mensch sieht und ändert
  // es, bevor es in die Liste des Agenten wandert.
  const mitRegel = async (a, erlauben) => {
    const muster = await nachfragen({
      title: erlauben ? t("Immer erlauben") : t("Nie erlauben"),
      text: erlauben
        ? t("Dieses Muster gilt künftig für {0} ohne Rückfrage als erlaubt. Widerrufbar unter Agenten → Freigaben.", a.agent)
        : t("Dieses Muster wird für {0} künftig sofort abgelehnt — ohne Anfrage. Widerrufbar unter Agenten → Freigaben.", a.agent),
      label: t("Muster"),
      initial: a.vorschlag,
      ok: erlauben ? t("Immer erlauben") : t("Nie erlauben"),
      danger: !erlauben,
    });
    if (!muster) return;
    if (
      erlauben &&
      /[A-Za-z0-9+/_-]{32,}|[0-9a-f]{12,}:[0-9a-f]{12,}|bearer\s|token\s/i.test(muster)
    )
      await melden({
        title: t("Zugangsdaten im Muster?"),
        text: t("Das Muster enthält etwas, das wie ein Token aussieht. Es stünde dann im Klartext in der Freigabeliste — besser unter Agenten → Freigaben ein Geheimnis anlegen und im Befehl $NAME verwenden."),
      });
    entscheide(a, erlauben, muster);
  };

  if (offen.length === 0) return null;
  const knopf = "rounded px-3 py-1 text-sm font-medium";
  return (
    <div className="border-b border-sky-300 bg-sky-50 px-4 py-2 dark:border-sky-800 dark:bg-sky-950">
      <div className="mb-1 text-xs font-semibold text-sky-700 dark:text-sky-400">
        🔐 {t("Freigabe-Anfragen der Automatik ({0}) — der Lauf wartet", offen.length)}
      </div>
      <div className="space-y-2">
        {offen.map((a) => {
          const rest = Math.max(0, Math.round((a.frist - jetzt) / 60));
          return (
            <div key={a.id} className="rounded border border-sky-200 bg-white p-2 text-sm dark:border-sky-900 dark:bg-slate-900">
              <div className="flex flex-wrap items-baseline gap-x-2 text-xs text-slate-500 dark:text-slate-400">
                <span className="font-semibold text-slate-700 dark:text-slate-200">{a.agent}</span>
                <span className="font-mono">{a.tool}</span>
                {a.task_id && <span className="truncate font-mono">{a.task_id}</span>}
                <span className="ml-auto">{t("noch {0} min", rest)}</span>
              </div>
              <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-100 p-1.5 font-mono text-xs text-slate-800 dark:bg-slate-800 dark:text-slate-100">
                {a.text}
              </pre>
              {a.geheimnis_verdacht && (
                <div className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                  ⚠ {t("Der Befehl enthält etwas, das wie Zugangsdaten aussieht — nicht „immer erlauben“, sonst stehen sie in der Liste.")}
                </div>
              )}
              <div className="mt-2 flex flex-wrap gap-2">
                <button onClick={() => entscheide(a, true)} className={`${knopf} bg-green-600 text-white hover:bg-green-700`}>
                  {t("Erlauben")}
                </button>
                <button onClick={() => entscheide(a, false)} className={`${knopf} bg-red-600 text-white hover:bg-red-700`}>
                  {t("Ablehnen")}
                </button>
                <button onClick={() => mitRegel(a, true)} className={`${knopf} border border-slate-300 text-slate-600 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800`}>
                  {t("Immer erlauben …")}
                </button>
                <button onClick={() => mitRegel(a, false)} className={`${knopf} border border-slate-300 text-slate-600 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800`}>
                  {t("Nie erlauben …")}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

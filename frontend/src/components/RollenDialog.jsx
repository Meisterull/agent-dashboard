import { useEffect, useState } from "react";
import Modal from "./Modal";
import { bestaetigen, melden } from "./Dialog";
import { deleteRolle, getRolle, getRollen, saveRolle } from "../api";
import { t } from "../sprache";

// Rollen für Task-Läufe (Dashboard-Paket St.1): eine Rolle ist eine
// Markdown-Datei (config/rollen/<name>.md) mit Frontmatter (beschreibung,
// optional permission_mode/allowed_tools) und dem Rollen-Prompt darunter.
//
// Review 02.09. (P0-4): Der Text lebt im STATE und wird per defaultValue +
// key-Remount ins textarea gemountet — die frühere imperative Ref-Zuweisung
// lief ins Leere, solange das Feld noch nicht im DOM war: erste Auswahl
// zeigte ein leeres Feld, und „Speichern" überschrieb die Rollen-Datei mit
// Leertext. defaultValue statt value zugleich wegen der GBoard-Regel
// (kontrollierte Text-Inputs verdoppeln Android-Wortvorschläge, s. Chat.jsx).

const VORLAGE = `---
beschreibung: Wofür diese Rolle ist (ein Satz)
# Rechte wirken als SCHNITTMENGE mit agents.yaml — nur einschränken:
# permission_mode: default
# allowed_tools: []
---

Du arbeitest in einer besonderen Rolle. Beschreibe hier, worauf der Lauf
achten soll.
`;

const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;

export default function RollenDialog({ onClose }) {
  const [rollen, setRollen] = useState(null); // null = lädt
  const [name, setName] = useState(null); // ausgewählte Rolle
  const [text, setText] = useState(""); // Editor-Inhalt (Quelle der Wahrheit)
  const [version, setVersion] = useState(0); // remountet textarea/Eingabe
  const [neu, setNeu] = useState(""); // Spiegel des "Neue Rolle"-Felds
  const [laedt, setLaedt] = useState(false);
  const [speichert, setSpeichert] = useState(false);

  const liste = async () => {
    try {
      setRollen((await getRollen()).rollen);
    } catch (e) {
      setRollen([]);
      melden({ title: t("Fehler"), text: t("Laden fehlgeschlagen: {0}", e.message) });
    }
  };
  useEffect(() => {
    liste();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const auswaehlen = async (n) => {
    setLaedt(true);
    try {
      const d = await getRolle(n);
      setName(n);
      setText(d.text);
      setVersion((v) => v + 1);
    } catch (e) {
      melden({ title: t("Fehler"), text: t("Laden fehlgeschlagen: {0}", e.message) });
    } finally {
      setLaedt(false);
    }
  };

  const anlegen = () => {
    const n = neu.trim().toLowerCase();
    if (!NAME_RE.test(n)) {
      melden({ title: t("Rollen"), text: t("Ungültiger Name — erlaubt: kleinbuchstaben, ziffern, - und _") });
      return;
    }
    setName(n);
    setText(VORLAGE);
    setNeu("");
    setVersion((v) => v + 1); // remountet auch das (uncontrolled) Namensfeld
  };

  const speichern = async () => {
    if (!name) return;
    if (!text.trim()) {
      // P0-4: nie still eine Rollen-Datei mit Leertext überschreiben.
      melden({ title: t("Rollen"), text: t("Leerer Text wird nicht gespeichert — zum Entfernen die Rolle löschen.") });
      return;
    }
    setSpeichert(true);
    try {
      await saveRolle(name, text);
      await liste();
    } catch (e) {
      melden({ title: t("Fehler"), text: t("Speichern fehlgeschlagen: {0}", e.message) });
    } finally {
      setSpeichert(false);
    }
  };

  const loeschen = async (n) => {
    if (
      !(await bestaetigen({
        title: t("Rolle löschen"),
        text: t("Rolle „{0}“ endgültig löschen?", n),
        ok: t("Löschen"),
        danger: true,
      }))
    )
      return;
    try {
      await deleteRolle(n);
      if (name === n) {
        setName(null);
        setText("");
      }
      await liste();
    } catch (e) {
      melden({ title: t("Fehler"), text: t("Löschen fehlgeschlagen: {0}", e.message) });
    }
  };

  const gespeichertVorhanden = (rollen || []).some((r) => r.name === name);

  return (
    <Modal title={t("Rollen")} onClose={onClose}>
      <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">
        {t(
          "Eine Rolle gibt einem Task-Lauf einen Prompt und kann seine Rechte einschränken — als Schnittmenge mit agents.yaml, nie erweiternd.",
        )}
      </p>
      <div className="flex gap-2">
        <input
          key={`neu:${version}`}
          defaultValue=""
          maxLength={64}
          onChange={(e) => setNeu(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && anlegen()}
          placeholder={t("neue-rolle (kleinbuchstaben, - und _)")}
          className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1.5 font-mono text-sm dark:border-slate-600 dark:bg-slate-800"
        />
        <button
          onClick={anlegen}
          disabled={!neu.trim()}
          className="shrink-0 rounded bg-slate-800 px-3 py-1.5 text-sm text-white disabled:opacity-40 dark:bg-slate-700"
        >
          {t("Anlegen")}
        </button>
      </div>

      {rollen === null ? (
        <p className="mt-4 text-sm text-slate-400">{t("lädt…")}</p>
      ) : rollen.length === 0 && !name ? (
        <p className="mt-4 text-sm text-slate-500 dark:text-slate-400">
          {t("Noch keine Rolle angelegt — oben einen Namen vergeben, die Vorlage ist vorausgefüllt.")}
        </p>
      ) : (
        <ul className="mt-3 flex flex-col gap-1.5">
          {rollen.map((r) => (
            <li
              key={r.name}
              className={`flex items-center gap-2 rounded border px-2.5 py-1.5 ${
                r.name === name
                  ? "border-blue-400 dark:border-blue-600"
                  : "border-slate-200 dark:border-slate-700"
              }`}
            >
              <button onClick={() => auswaehlen(r.name)} className="min-w-0 flex-1 text-left">
                <div className="truncate font-mono text-sm">{r.name}</div>
                <div className="truncate text-xs text-slate-500 dark:text-slate-400">
                  {r.fehler
                    ? t("Datei fehlerhaft: {0}", r.fehler)
                    : r.beschreibung || t("ohne Beschreibung")}
                  {(r.permission_mode || r.allowed_tools) && !r.fehler && (
                    <span className="ml-1 text-indigo-500 dark:text-indigo-400">
                      · {t("schränkt Rechte ein")}
                    </span>
                  )}
                </div>
              </button>
              <button
                onClick={() => loeschen(r.name)}
                title={t("Rolle „{0}“ endgültig löschen?", r.name)}
                className="shrink-0 rounded px-1.5 py-1 text-xs text-slate-400 hover:text-red-600"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}

      {name && (
        <div className="mt-3">
          <div className="mb-1 flex items-center gap-2">
            <span className="font-mono text-sm">{name}</span>
            {!gespeichertVorhanden && (
              <span className="text-xs text-amber-600 dark:text-amber-400">{t("noch nicht gespeichert")}</span>
            )}
            <span className="flex-1" />
            <button
              onClick={speichern}
              disabled={speichert || laedt || !text.trim()}
              className="rounded bg-blue-600 px-3 py-1 text-sm font-medium text-white disabled:opacity-40 hover:bg-blue-700"
            >
              {speichert ? "…" : t("Speichern")}
            </button>
          </div>
          <textarea
            key={`${name}:${version}`}
            defaultValue={text}
            onChange={(e) => setText(e.target.value)}
            rows={12}
            spellCheck={false}
            className="w-full rounded border border-slate-300 p-2 font-mono text-xs leading-snug dark:border-slate-600 dark:bg-slate-900"
          />
        </div>
      )}
    </Modal>
  );
}

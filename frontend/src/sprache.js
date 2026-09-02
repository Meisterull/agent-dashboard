// Oberflächensprache (Deutsch/Englisch) — bewusst ohne i18n-Bibliothek.
//
// PRINZIP: Der deutsche Text IST der Schlüssel. `t("Speichern")` liefert auf
// Deutsch den Text unverändert zurück und schlägt auf Englisch im Wörterbuch
// nach; fehlt ein Eintrag, bleibt es beim Deutschen (nie ein leerer Knopf).
// Der Code bleibt damit lesbar wie bisher, und neue Strings funktionieren
// sofort — sie sind nur eben noch unübersetzt, bis das Wörterbuch nachzieht.
//
// SPRACHE LESEN: synchron aus localStorage (`ui.sprache`), damit der erste
// Render schon stimmt. Die Quelle der Wahrheit ist das globale Setting
// `language` (settings.json): App.jsx gleicht nach dem Laden der Settings ab
// und lädt bei Abweichung einmal neu (sprachAngleichen). Das Settings-Modal
// schreibt beim Speichern beide Stellen und lädt neu — ein Reload statt
// reaktiver Verdrahtung durch alle Komponenten ist hier der ehrlichere Deal.
//
// PLATZHALTER: t("Neuer Versuch in {0}s", 5) — {0}, {1}, … werden ersetzt.
// Dynamische Teile gehören NIE in den Schlüssel.
//
// Wörterbuch in Teildateien (sprache/woerter_*.js), damit mehrere Baustellen
// konfliktfrei ergänzen können; hier werden sie zusammengelegt.
import kern from "./sprache/woerter_kern";
import terminal from "./sprache/woerter_terminal";
import chat from "./sprache/woerter_chat";
import agenten from "./sprache/woerter_agenten";
import dateien from "./sprache/woerter_dateien";
import workspace from "./sprache/woerter_workspace";

const WOERTER = { ...kern, ...terminal, ...chat, ...agenten, ...dateien, ...workspace };

const SCHLUESSEL = "ui.sprache";

export function aktuelleSprache() {
  try {
    const s = localStorage.getItem(SCHLUESSEL);
    return s === "en" ? "en" : "de";
  } catch {
    return "de"; // localStorage gesperrt (Privatmodus) → Deutsch
  }
}

const EN = aktuelleSprache() === "en";

// <html lang> mitziehen (Review P2): index.html sagt statisch lang="de" —
// Screenreader und Browser-Übersetzer hielten die englische Oberfläche
// sonst für Deutsch.
if (typeof document !== "undefined")
  document.documentElement.lang = EN ? "en" : "de";

/** Übersetzt einen deutschen UI-Text; {0}, {1}, … werden durch `werte` ersetzt. */
export function t(text, ...werte) {
  let out = EN ? (WOERTER[text] ?? text) : text;
  for (let i = 0; i < werte.length; i++) out = out.replaceAll(`{${i}}`, String(werte[i]));
  return out;
}

/** Merkt die Sprache auf diesem Gerät. Liefert true, wenn sie sich geändert hat. */
export function sprachMerken(sprache) {
  const neu = sprache === "en" ? "en" : "de";
  const alt = aktuelleSprache();
  try {
    localStorage.setItem(SCHLUESSEL, neu);
  } catch {
    /* Privatmodus: dann gilt weiter das globale Setting beim nächsten Laden */
  }
  return neu !== alt;
}

/**
 * Nach dem Laden der Settings aufrufen: Weicht das globale Setting von der
 * Gerätesprache ab (anderes Gerät hat umgestellt, oder erster Besuch), wird
 * angeglichen und einmal neu geladen. Loop-sicher: nach dem Reload stimmen
 * beide überein.
 */
export function sprachAngleichen(settingsSprache) {
  if (!settingsSprache) return;
  if (sprachMerken(settingsSprache)) window.location.reload();
}

// Fenster ein-/ausblenden (Issue #50) — reine Logik, ohne React, damit sie
// sich ohne Browser prüfen lässt (tests/test_fenstersichtbar.mjs).
//
// Ausgeblendet heißt: kein Platz im Layout, kein Tab — aber das Panel bleibt
// gemountet (nur per CSS versteckt), damit Chat-Zustand und SSH-Sessions das
// Aus- und Wiedereinblenden überleben, genau wie beim Tab-Wechsel.
//
// Gespeichert wird pro Gerät im localStorage (speicher.js): am Handy will man
// andere Fenster als am PC. Unbekannte Ids bleiben stehen — externe Fenster
// kommen asynchron aus den Settings, ihr „aus" soll den Start überleben.

export const LS_VERSTECKT = "workspace-versteckt-v1";

/** Gespeicherten Text → Set der ausgeblendeten Panel-Ids (kaputt → leer). */
export function versteckteLesen(text) {
  try {
    const liste = JSON.parse(text);
    return new Set(Array.isArray(liste) ? liste.filter((x) => typeof x === "string") : []);
  } catch {
    return new Set();
  }
}

export const versteckteText = (versteckt) => JSON.stringify([...versteckt].sort());

/** Sichtbare Ids in Panel-Reihenfolge. Nie leer: Wäre alles aus, gilt alles. */
export function sichtbareIds(alleIds, versteckt) {
  const sichtbar = alleIds.filter((id) => !versteckt.has(id));
  return sichtbar.length ? sichtbar : alleIds;
}

/** Darf `id` gerade ausgeblendet werden? Mindestens ein Fenster bleibt. */
export function darfAusblenden(id, alleIds, versteckt) {
  if (versteckt.has(id)) return true; // ist schon aus → Haken setzen geht immer
  return alleIds.some((x) => x !== id && !versteckt.has(x));
}

/**
 * Sichtbarkeit von `id` umschalten. Liefert das NEUE Set, oder das alte
 * unverändert, wenn das letzte sichtbare Fenster verschwinden würde.
 */
export function umschalten(versteckt, id, alleIds) {
  if (!darfAusblenden(id, alleIds, versteckt)) return versteckt;
  const neu = new Set(versteckt);
  if (neu.has(id)) neu.delete(id);
  else neu.add(id);
  return neu;
}

/**
 * Welcher Tab gilt, wenn `tab` ausgeblendet wurde? Bevorzugt der Chat, sonst
 * das erste sichtbare Fenster. Ist `tab` sichtbar, bleibt er.
 */
export function ersatzTab(tab, sichtbar) {
  if (sichtbar.includes(tab)) return tab;
  return sichtbar.includes("chat") ? "chat" : sichtbar[0];
}

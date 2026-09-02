// localStorage kann in WebViews mit "Cookies blockieren" oder im Privatmodus
// schon beim ZUGRIFF werfen (Review P2) — ungeschützt riss das App, Chat und
// Terminal beim ersten Render (weiße Seite). Diese Helfer machen jeden
// Zugriff zur Konvenienz: klappt es nicht, läuft die App ohne Gedächtnis.
export function lsGet(schluessel) {
  try {
    return window.localStorage.getItem(schluessel);
  } catch {
    return null;
  }
}

export function lsSet(schluessel, wert) {
  try {
    window.localStorage.setItem(schluessel, wert);
  } catch {
    /* voll oder gesperrt — Wert gilt dann nur für diese Sitzung */
  }
}

export function lsDel(schluessel) {
  try {
    window.localStorage.removeItem(schluessel);
  } catch {
    /* siehe lsSet */
  }
}

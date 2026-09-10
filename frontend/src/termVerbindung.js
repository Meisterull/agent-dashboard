// Verbindungs-Hygiene fürs Terminal (Terminal.jsx) — die Teile, die ohne
// Browser prüfbar sind (tests/test_verbindung.mjs), plus der Sichtbarkeits-
// Wächter, den tests/test_terminal_browser.cjs im echten Chrome nachstellt.
//
// Drei Befunde vom 10.09.2026 (Screenshot vom Handy):
//
// 1. Ein Panel, das die App per display:none ausblendet (Workspace.jsx in der
//    Tab-Ansicht am Handy), hat für getComputedStyle die Breite „100%". Das
//    FitAddon liest daraus 100 px, zieht 15 px Scrollbar ab und kommt bei
//    18-px-Schrift auf SIEBEN Spalten. Die gingen als resize an die Shell —
//    Claude Code brach danach jede Zeile in Siebener-Häppchen um, und weil
//    der Server-Replay den rohen Strom nachspielt, blieb der Verlauf so.
//    Auslöser war jeder Tab-Wechsel weg vom Terminal (App.jsx feuert danach
//    ein resize) und jede Tastatur-Bewegung, solange ein anderes Panel vorne
//    lag. Regel: ausgeblendet wird weder gefittet noch eine Größe gemeldet.
//
// 2. Bei schlechtem Netz meldet ein WebSocket oft minutenlang KEIN close:
//    readyState bleibt OPEN, Tastendrücke gehen ins Leere, nichts kommt
//    zurück — „ich kann plötzlich nicht mehr weiterschreiben". Der
//    Herzschlag schickt nach Funkstille ein Ping (und beim Tippen sofort,
//    wenn das Echo ausbleibt); kommt kein Pong, gilt die Verbindung als tot
//    und wird ohne Warten neu aufgebaut.
//
// 3. Was während der Trennung getippt wird, ging verloren (sendRaw prüfte
//    nur readyState). Die Warteschlange hebt es auf und liefert es nach dem
//    Reattach am Stück nach — die Shell lief ja weiter, die Reihenfolge
//    stimmt.

/** Panel sichtbar? display:none irgendwo drüber macht clientWidth/Height 0. */
export function istSichtbar(el) {
  return !!el && el.clientWidth > 0 && el.clientHeight > 0;
}

// Was der Server ohnehin verwirft (ssh_bridge.MIN_COLS/MIN_ROWS) — hier
// gespiegelt, damit ein Frontend nie Messmüll losschickt.
export const MIN_COLS = 20;
export const MIN_ROWS = 3;

export function plausibleGroesse(cols, rows) {
  return cols >= MIN_COLS && rows >= MIN_ROWS;
}

/**
 * Herzschlag über einer offenen Verbindung. Zeit und Wirkung sind injiziert
 * (jetzt/sendePing/tot), damit der Test die Uhr selbst dreht.
 *
 *   empfangen()   jede Nachricht vom Server (Ausgabe oder Pong) = Lebenszeichen
 *   gesendet()    Eingabe ging raus — bleibt das Echo aus, sofort nachfragen
 *   sofort()      App kommt in den Vordergrund: gleich prüfen, nicht erst warten
 *   pruefen()     Takt (alle paar Sekunden); true = Verbindung für tot erklärt
 */
export class Herzschlag {
  constructor({
    jetzt = () => Date.now(),
    sendePing,
    tot,
    pingNach = 15000, // Funkstille, ab der gepingt wird
    pongFrist = 8000, // so lange darf das Pong brauchen
    ruheVorPing = 2000, // beim Tippen: so lange ohne Echo → Ping
  } = {}) {
    this.jetzt = jetzt;
    this.sendePing = sendePing;
    this.tot = tot;
    this.pingNach = pingNach;
    this.pongFrist = pongFrist;
    this.ruheVorPing = ruheVorPing;
    this.neueVerbindung();
  }

  neueVerbindung() {
    this.zuletzt = this.jetzt();
    this.pingSeit = 0;
  }

  empfangen() {
    this.zuletzt = this.jetzt();
    this.pingSeit = 0;
  }

  gesendet() {
    if (!this.pingSeit && this.jetzt() - this.zuletzt > this.ruheVorPing) this._ping();
  }

  sofort() {
    if (!this.pingSeit) this._ping();
  }

  pruefen() {
    const t = this.jetzt();
    if (this.pingSeit) {
      if (t - this.pingSeit <= this.pongFrist) return false;
      this.pingSeit = 0;
      this.zuletzt = t; // nach dem Neuaufbau nicht sofort wieder pingen
      this.tot();
      return true;
    }
    if (t - this.zuletzt > this.pingNach) this._ping();
    return false;
  }

  _ping() {
    this.pingSeit = this.jetzt();
    this.sendePing();
  }
}

/**
 * Eingaben, die auf den Reattach warten. Gedeckelt: wer bei totem Netz
 * minutenlang weitertippt, bekommt ab dem Deckel `false` zurück (und sieht
 * im Terminal den Hinweis) — Ältestes wegwerfen wäre schlimmer, dann käme
 * bei der Shell der Schluss ohne den Anfang an.
 */
export class Warteschlange {
  constructor(maxZeichen = 16 * 1024) {
    this.max = maxZeichen;
    this.teile = [];
    this.zeichen = 0;
  }

  get laenge() {
    return this.zeichen;
  }

  push(data) {
    if (!data) return true;
    if (this.zeichen + data.length > this.max) return false;
    this.teile.push(data);
    this.zeichen += data.length;
    return true;
  }

  leeren() {
    const alles = this.teile.join("");
    this.teile = [];
    this.zeichen = 0;
    return alles;
  }
}

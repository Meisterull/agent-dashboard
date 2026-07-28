// Terminal-Tastenkodierung: übersetzt Tasten + Modifikatoren (Strg/Alt/Shift)
// in die Escape-Sequenzen, die xterm/SSH erwarten. Grundlage ist das
// xterm-Schema "CSI 1;<mod>" mit mod = 1 + shift(1) + alt(2) + ctrl(4).

const CSI = "\x1b[";

const modParam = (m) =>
  1 + (m.shift ? 1 : 0) + (m.alt ? 2 : 0) + (m.ctrl ? 4 : 0);

// Cursor-/Navigationstasten: entweder "CSI X" oder "CSI n~"
const NAMED = {
  up: { letter: "A" },
  down: { letter: "B" },
  right: { letter: "C" },
  left: { letter: "D" },
  home: { letter: "H" },
  end: { letter: "F" },
  insert: { tilde: "2" },
  delete: { tilde: "3" },
  pageup: { tilde: "5" },
  pagedown: { tilde: "6" },
};

// Einzelnes Zeichen mit Modifikatoren kodieren (z.B. Strg+C → \x03,
// Alt+B → ESC b). Wird auch auf Eingaben der Bildschirmtastatur angewandt,
// wenn ein Sticky-Modifikator aktiv ist.
export function encodeChar(ch, mods = {}) {
  let out = mods.shift ? ch.toUpperCase() : ch;
  if (mods.ctrl) {
    const code = out.toUpperCase().charCodeAt(0);
    if (code === 63) out = "\x7f"; // Strg+? → DEL
    else if (code >= 64 && code <= 95) out = String.fromCharCode(code & 0x1f);
    else if (out === " ") out = "\x00";
  }
  if (mods.alt) out = "\x1b" + out;
  return out;
}

// Benannte Taste oder einzelnes Zeichen mit Modifikatoren kodieren.
export function encodeKey(key, mods = {}) {
  const m = modParam(mods);
  switch (key) {
    case "esc":
      return "\x1b";
    case "enter":
      return mods.alt ? "\x1b\r" : "\r";
    case "backspace":
      return mods.alt ? "\x1b\x7f" : "\x7f";
    case "tab":
      return mods.shift ? CSI + "Z" : "\t";
    case "space":
      return encodeChar(" ", mods);
  }
  const n = NAMED[key];
  if (n) {
    if (n.letter)
      return m === 1 ? CSI + n.letter : `${CSI}1;${m}${n.letter}`;
    return m === 1 ? `${CSI}${n.tilde}~` : `${CSI}${n.tilde};${m}~`;
  }
  return encodeChar(key, mods);
}

"""Alle Backend-Tests am Stück:  cd backend && python -m tests.run_alle

Testmodule werden aus tests/test_*.py eingesammelt und laufen je als eigener
Prozess — mehrere Tests setzen WORKSPACE_DIR um und laden Module neu, das darf
sich nicht gegenseitig stören.
Alles Standardlib: läuft auf dem Host genauso wie im Container.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def module() -> list[str]:
    return [f"tests.{p.stem}" for p in sorted(Path(__file__).parent.glob("test_*.py"))]


def main() -> int:
    module_ = module()
    fehlgeschlagen = []
    for modul in module_:
        print(f"\n=== {modul} ===", flush=True)
        if subprocess.run([sys.executable, "-m", modul]).returncode != 0:
            fehlgeschlagen.append(modul)
    print()
    if fehlgeschlagen:
        print(f"FEHLGESCHLAGEN: {', '.join(fehlgeschlagen)}")
        return 1
    print(f"alle {len(module_)} Testmodule grün")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

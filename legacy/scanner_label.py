#!/usr/bin/env python3
"""
scanner_label.py — Envía etiquetas al gamestate_scanner.lua en tiempo real.

USO:
    python scanner_label.py

Escribe el nombre de la pantalla que ves en el momento exacto.
El Lua lo recoge en el siguiente frame y lo anota en el log con el GS actual.
"""

import os, time

DYN_DIR    = r"C:\proyectos\MAME\dinamicos"
LABEL_FILE = os.path.join(DYN_DIR, "scanner_label.txt")

LABELS = {
    "1": "TITULO",
    "2": "INSERT_COIN",
    "3": "CHAR_SELECT",
    "4": "VS_SCREEN",
    "5": "COMBAT",
    "6": "ROUND_OVER_WIN",
    "7": "ROUND_OVER_LOSS",
    "8": "GAME_OVER_CONTINUE",
    "9": "CONTINUE_EXPIRED",
    "0": "BONUS_STAGE",
    "b": "BONUS_STAGE",
    "t": "TITULO",
    "c": "COMBAT",
    "s": "CHAR_SELECT",
    "g": "GAME_OVER_CONTINUE",
    "w": "ROUND_OVER_WIN",
    "l": "ROUND_OVER_LOSS",
    "x": "CONTINUE_EXPIRED",
}

print("=" * 55)
print("  Scanner Label Tool — SF2CE GAME_STATE mapper")
print("=" * 55)
print()
print("  Teclas rápidas:")
print("    1 / t  → TITULO")
print("    2      → INSERT_COIN")
print("    3 / s  → CHAR_SELECT")
print("    4      → VS_SCREEN")
print("    5 / c  → COMBAT")
print("    6 / w  → ROUND_OVER_WIN")
print("    7 / l  → ROUND_OVER_LOSS")
print("    8 / g  → GAME_OVER_CONTINUE")
print("    9 / x  → CONTINUE_EXPIRED")
print("    0 / b  → BONUS_STAGE")
print()
print("  O escribe el nombre completo y pulsa Enter.")
print("  Ctrl+C para salir.")
print()

while True:
    try:
        raw = input("  Etiqueta > ").strip()
        if not raw:
            continue

        label = LABELS.get(raw.lower(), raw.upper().replace(" ", "_"))

        with open(LABEL_FILE, "w") as f:
            f.write(label)

        ts = time.strftime("%H:%M:%S")
        print(f"  [{ts}] → '{label}' enviado al Lua ✓")

    except KeyboardInterrupt:
        print("\n  Saliendo.")
        break
    except Exception as e:
        print(f"  ERROR: {e}")
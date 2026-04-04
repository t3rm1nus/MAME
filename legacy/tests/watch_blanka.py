import time  # <--- FALTA ESTE
import os
import sys
from tests.test_blanka_movements import run_suite

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core.mame_client import MAMEClient
from core.mame_controller import MAMEController
from tests.test_blanka_movements import run_suite

INPUT_FILE = r"C:\proyectos\MAME\input.txt"

# ✅ Limpiar ANTES de lanzar MAME — Lua nunca ve basura antigua
with open(INPUT_FILE, "w") as f:
    f.write("\n")

mame = MAMEClient()
mame.launch()

# ── UN solo punto de entrada ──────────────────────────────────
# Navega manualmente hasta el combate, luego pulsa ENTER
print("🔥 PREPARANDO MOTORES...")
time.sleep(1)
print("Saca el foco de la consola, ve a MAME y prepárate.")
print("El test empezará SOLO en 10 segundos...")
time.sleep(10) # <--- Te da tiempo de sobra a poner MAME, quitar la pausa y esperar.
run_suite()
#!/usr/bin/env python3
"""
reset_training.py — Limpieza TOTAL para empezar entrenamiento desde cero
=======================================================================
Ejecuta esto antes de lanzar train_FASE1.py cuando quieras un reset completo.
"""

import os
import shutil
import time

MAME_DIR = r"C:\proyectos\MAME"

paths_to_delete = [
    # Modelos y VecNormalize
    os.path.join(MAME_DIR, "models", "blanka", "fase1"),
    
    # Logs
    os.path.join(MAME_DIR, "logs", "blanka", "fase1"),
    
    # Estadísticas de rivales
    os.path.join(MAME_DIR, "rival_stats.json"),
    
    # Archivos temporales del bridge (por si acaso)
    os.path.join(MAME_DIR, "dinamicos"),
]

print("🧹 Iniciando limpieza completa para nuevo entrenamiento...\n")

for path in paths_to_delete:
    if os.path.exists(path):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
                print(f"✓ Borrada carpeta: {path}")
            else:
                os.remove(path)
                print(f"✓ Borrado archivo: {path}")
        except Exception as e:
            print(f"✗ Error al borrar {path}: {e}")
    else:
        print(f"→ No existía: {path}")

# Recrear carpetas necesarias
os.makedirs(os.path.join(MAME_DIR, "models", "blanka", "fase1"), exist_ok=True)
os.makedirs(os.path.join(MAME_DIR, "logs", "blanka", "fase1"), exist_ok=True)
os.makedirs(os.path.join(MAME_DIR, "dinamicos"), exist_ok=True)

print("\n" + "="*60)
print("✅ LIMPIEZA COMPLETA FINALIZADA")
print("="*60)
print("Ahora puedes lanzar el entrenamiento limpio:")
print('   python train_FASE1.py --steps 1000000 --envs 6 --lr 2.5e-4')
print("\nRecuerda:")
print("   • El reward nuevo ya debe estar en blanka_env.py")
print("   • ROLLING_AND_ELECTRIC_ONLY = False (full PPO)")
print("   • No uses --visible si quieres máxima velocidad")
print("="*60)

# Pequeña pausa para que leas
time.sleep(2)
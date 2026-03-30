#!/usr/bin/env python3
"""
watch_blanka.py — Visualizador del agente entrenado en tiempo real
==================================================================
Versión: 1.0 (29/03/2026)

Busca el último checkpoint guardado en models/blanka/fase1/ (o el que
se indique por argumento), lanza MAME a velocidad normal con ventana
visible, y deja que el agente juegue usando el modelo entrenado.

El Lua autoplay_bridge.lua se encarga de toda la navegación de menús
(INSERT_COIN, CHAR_SELECT, CONTINUE, etc.) igual que en entrenamiento.
Este script solo toma el control cuando el bridge reporta IN_COMBAT.

USO:
  # Usa el ultimo checkpoint encontrado automaticamente:
  python watch_blanka.py

  # Especifica un modelo concreto (sin .zip):
  python watch_blanka.py --model models/blanka/fase1/fase1_32768_steps

  # Numero de episodios a observar (0 = infinito):
  python watch_blanka.py --episodes 5

  # Carpeta de modelos alternativa:
  python watch_blanka.py --model_dir models/blanka/fase2
"""

import argparse
import glob
import os
import sys
import time
import subprocess
from typing import Optional

import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# ── RUTAS ─────────────────────────────────────────────────────────────────────
MAME_DIR    = r"C:\proyectos\MAME"
DYN_DIR     = os.path.join(MAME_DIR, "dinamicos")
MAME_EXE    = r"C:\proyectos\MAME\EMULADOR\mame.exe"
LUA_SCRIPT  = r"C:\proyectos\MAME\lua\autoplay_bridge.lua"
MODELS_BASE = os.path.join(MAME_DIR, "models", "blanka", "fase1")
VN_PATH     = os.path.join(MAME_DIR, "models", "blanka", "fase1", "fase1_vecnorm.pkl")
STATS_FILE  = os.path.join(MAME_DIR, "rival_stats.json")

CLAIM_FILE  = os.path.join(DYN_DIR, "instance_id_claim.txt")
VER_FILE    = os.path.join(DYN_DIR, "bridge_version_0.txt")

os.makedirs(DYN_DIR, exist_ok=True)

CHAR_NAMES = {
    0:"Ryu", 1:"Honda", 2:"Blanka", 3:"Guile", 4:"Ken", 5:"Chun-Li",
    6:"Zangief", 7:"Dhalsim", 8:"M.Bison", 9:"Sagat", 10:"Balrog", 11:"Vega",
}

ACTION_NAMES = {
    0:"NOOP", 1:"UP", 2:"DOWN", 3:"LEFT", 4:"RIGHT",
    5:"JAB", 6:"STRONG", 7:"FIERCE", 8:"SHORT", 9:"FORWARD", 10:"RH",
    11:"D+JAB", 12:"D+FIERCE", 13:"D+SHORT", 14:"D+RH",
    15:"ROLLING🔥", 16:"ROLLING(M)", 17:"ROLLING(S)",
    18:"ELECTRIC⚡", 19:"JMP→+F", 20:"JMP→+FWD", 21:"JMP→+RH",
    22:"JMP↑+F", 23:"JMP←+F", 24:"JMP←+FWD", 25:"ROLL-JUMP",
}


# ── BUSCAR ULTIMO MODELO ──────────────────────────────────────────────────────

def find_latest_model(model_dir: str) -> Optional[str]:
    """
    Busca el checkpoint mas reciente en model_dir.
    Ordena por numero de steps extraido del nombre (fase1_NNNNN_steps.zip).
    Si no hay ninguno con ese patron, coge el mas nuevo por fecha.
    """
    zips = glob.glob(os.path.join(model_dir, "*.zip"))
    if not zips:
        return None

    # Intentar ordenar por numero de steps en el nombre
    def extract_steps(path):
        base = os.path.basename(path)
        parts = base.replace(".zip", "").split("_")
        for p in parts:
            if p.isdigit():
                return int(p)
        return 0

    # Preferir checkpoints con steps en el nombre; excluir "final" de la ordenacion
    numbered = [z for z in zips if extract_steps(z) > 0]
    if numbered:
        best = max(numbered, key=extract_steps)
    else:
        # Fallback: el mas reciente por fecha de modificacion
        best = max(zips, key=os.path.getmtime)

    return best.replace(".zip", "")   # PPO.load no quiere la extension


# ── LANZAR MAME (VISIBLE, VELOCIDAD NORMAL) ───────────────────────────────────

def launch_mame_visible() -> Optional[subprocess.Popen]:
    """
    Lanza MAME a velocidad normal con ventana visible.
    Escribe claim con ID=0 para que el Lua use dinamicos/state_0.txt.
    Espera hasta 35s a que el bridge este listo.
    """
    if not os.path.exists(MAME_EXE):
        print(f"[WATCH] ERROR: no existe {MAME_EXE}")
        return None
    if not os.path.exists(LUA_SCRIPT):
        print(f"[WATCH] ERROR: no existe {LUA_SCRIPT}")
        return None

    # Limpiar archivos de sesion anterior
    for f in [VER_FILE, CLAIM_FILE,
              os.path.join(DYN_DIR, "instance_id_claimed_0.txt"),
              os.path.join(DYN_DIR, "mame_input_0.txt"),
              os.path.join(DYN_DIR, "state_0.txt")]:
        try:
            if os.path.exists(f): os.remove(f)
        except Exception:
            pass

    # Escribir claim ID=0
    try:
        with open(CLAIM_FILE, "w") as f:
            f.write("0")
    except Exception as e:
        print(f"[WATCH] WARN: no se pudo escribir claim: {e}")

    cmd = [
        MAME_EXE, "sf2ce",
        "-rompath",         os.path.join(MAME_DIR, "EMULADOR", "roms"),
        "-autoboot_script", LUA_SCRIPT,
        "-skip_gameinfo",
        "-video", "d3d",
        "-window",
        "-nomaximize",
        # SIN -nothrottle → velocidad normal (60fps)
    ]

    print(f"[WATCH] Lanzando MAME (velocidad normal, ventana visible)...")
    try:
        proc = subprocess.Popen(cmd, cwd=os.path.dirname(MAME_EXE))
        print(f"[WATCH] PID={proc.pid}")
    except Exception as e:
        print(f"[WATCH] ERROR lanzando MAME: {e}")
        return None

    # Esperar bridge listo
    print("[WATCH] Esperando Lua bridge...")
    t0 = time.time()
    while time.time() - t0 < 35.0:
        if proc.poll() is not None:
            print("[WATCH] ERROR: MAME termino inesperadamente")
            return None
        if os.path.exists(VER_FILE):
            try:
                ver = open(VER_FILE).read().strip()
                if ver:
                    print(f"[WATCH] Bridge listo: {ver}  ({time.time()-t0:.1f}s)")
                    time.sleep(1.5)
                    return proc
            except Exception:
                pass
        time.sleep(0.25)

    print("[WATCH] TIMEOUT esperando bridge")
    return None


# ── CARGAR MODELO ─────────────────────────────────────────────────────────────

def load_model(model_path: str):
    """Carga PPO + VecNormalize. Devuelve (model, vec_norm_or_None)."""
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
    from env.blanka_env import BlankaEnv

    print(f"[WATCH] Cargando modelo: {model_path}.zip")

    # Entorno dummy necesario para cargar el modelo (no se usa para jugar)
    def _dummy():
        return BlankaEnv(instance_id=0, max_steps=9999)

    dummy_env = DummyVecEnv([_dummy])

    vec_norm = None
    if os.path.exists(VN_PATH):
        print(f"[WATCH] Cargando VecNormalize: {VN_PATH}")
        try:
            vec_norm = VecNormalize.load(VN_PATH, dummy_env)
            vec_norm.training = False     # modo inferencia
            vec_norm.norm_reward = False  # no normalizar recompensa en watch
            dummy_env = vec_norm
        except Exception as e:
            print(f"[WATCH] WARN VecNormalize: {e} — continuando sin normalizar")
            vec_norm = None
    else:
        print(f"[WATCH] WARN: no se encontro VecNorm en {VN_PATH}")

    try:
        model = PPO.load(model_path, env=dummy_env, device="cpu")
        params = sum(p.numel() for p in model.policy.parameters())
        print(f"[WATCH] Modelo cargado. Parametros: {params:,}")
        return model, vec_norm
    except Exception as e:
        print(f"[WATCH] ERROR cargando modelo: {e}")
        sys.exit(1)


# ── NORMALIZAR OBSERVACION ────────────────────────────────────────────────────

def normalize_obs(obs: np.ndarray, vec_norm) -> np.ndarray:
    """Aplica VecNormalize a la observacion si esta disponible."""
    if vec_norm is None:
        return obs
    try:
        obs_2d = obs.reshape(1, -1)
        obs_norm = vec_norm.normalize_obs(obs_2d)
        return obs_norm.reshape(-1)
    except Exception:
        return obs


# ── BUCLE PRINCIPAL ───────────────────────────────────────────────────────────

def watch(model_path: str, max_episodes: int = 0):
    from mame_bridge import MAMEBridge
    from env.blanka_env import BlankaEnv, N_ACTIONS

    # 1. Lanzar MAME
    proc = launch_mame_visible()
    if proc is None:
        sys.exit(1)

    # 2. Cargar modelo
    model, vec_norm = load_model(model_path)

    # 3. Bridge directo (sin entorno Gymnasium — control manual del loop)
    bridge = MAMEBridge(instance_id=0)

    # Reutilizamos _get_obs del entorno para construir el vector de estado
    env = BlankaEnv(instance_id=0, max_steps=99999)
    env.bridge = bridge   # compartir el mismo bridge

    print("\n" + "="*60)
    print("  WATCH MODE — el agente juega con el modelo cargado")
    print("  El Lua navega menus automaticamente")
    print("  Ctrl+C para salir")
    print("="*60 + "\n")

    ep_num       = 0
    total_steps  = 0
    action_hist  = {i: 0 for i in range(N_ACTIONS)}

    try:
        while max_episodes == 0 or ep_num < max_episodes:
            ep_num += 1
            print(f"\n[WATCH] ── Episodio {ep_num} ──")

            # Esperar combate valido
            obs, info = env.reset()
            rival     = info.get("rival", 0xFF)
            rname     = CHAR_NAMES.get(rival, f"ID_{rival:02X}")
            p1_dir    = info.get("p1_dir", 1)
            print(f"[WATCH] vs {rname} | dir={'→' if p1_dir==1 else '←'}")

            ep_step    = 0
            ep_p1_dmg  = 0.0
            ep_p2_dmg  = 0.0
            ep_rewards = 0.0
            prev_p1hp  = 144.0
            prev_p2hp  = 144.0
            done       = False

            while not done:
                ep_step   += 1
                total_steps += 1

                # Inferencia — el modelo decide la accion
                obs_norm = normalize_obs(obs, vec_norm)
                action, _ = model.predict(obs_norm, deterministic=True)
                action = int(action)
                action_hist[action] = action_hist.get(action, 0) + 1

                obs, reward, terminated, truncated, info = env.step(action)
                done = terminated or truncated

                p1hp = info.get("p1_hp", 144.0)
                p2hp = info.get("p2_hp", 144.0)
                ep_p1_dmg += max(0.0, prev_p1hp - p1hp)
                ep_p2_dmg += max(0.0, prev_p2hp - p2hp)
                ep_rewards += reward
                prev_p1hp  = p1hp
                prev_p2hp  = p2hp

                aname = ACTION_NAMES.get(action, f"ACT{action}")

                # Imprimir solo acciones interesantes (no NOOP en cada frame)
                if action != 0 or ep_step % 60 == 0:
                    dist   = abs(info.get("p1_hp", 144) - 0)  # placeholder
                    charge = info.get("charge", 0)
                    fk_w   = info.get("fk_land", 0)
                    p1x    = obs[1] * 1400  # obs[1] = p1_x normalizado
                    p2x    = obs[6] * 1400  # obs[6] = p2_x normalizado

                    extras = ""
                    if fk_w > 0 and fk_w <= 20:
                        extras = " ⚡FK-WIN!"
                    if charge >= 68:
                        extras += " [CARGADO]"

                    if action != 0:
                        print(f"  [{ep_step:>4}] {aname:<14} | "
                              f"P1={p1hp:>3.0f} P2={p2hp:>3.0f} | "
                              f"R={reward:>+7.1f}{extras}")

                # Resumen cada 120 frames aunque no haya accion
                if ep_step % 120 == 0:
                    print(f"  [{ep_step:>4}] --- P1={p1hp:>3.0f} P2={p2hp:>3.0f} "
                          f"| dmg_dado={ep_p2_dmg:.0f} dmg_rec={ep_p1_dmg:.0f} "
                          f"| R_acum={ep_rewards:>+.0f}")

            # Fin episodio
            won  = info.get("won", False)
            rndw = info.get("round_wins", 0)
            print(f"\n[WATCH] FIN ep={ep_num} | {'✓ VICTORIA' if won else '✗ DERROTA'} "
                  f"| rondas_ganadas={rndw} "
                  f"| dmg_dado={ep_p2_dmg:.0f} dmg_rec={ep_p1_dmg:.0f} "
                  f"| pasos={ep_step} | R_total={ep_rewards:+.0f}")

            # Top acciones usadas en este episodio
            used = sorted(action_hist.items(), key=lambda x: -x[1])
            used = [(ACTION_NAMES.get(a, f"A{a}"), n) for a, n in used if n > 0][:6]
            print(f"  Top acciones (acum): " +
                  " | ".join(f"{n}:{c}" for n, c in used))

    except KeyboardInterrupt:
        print("\n[WATCH] Ctrl+C — cerrando...")
    finally:
        try:
            env.close()
        except Exception:
            pass
        if proc and proc.poll() is None:
            print("[WATCH] Cerrando MAME...")
            proc.terminate()

    # Resumen final
    print(f"\n{'='*60}")
    print(f"  WATCH finalizado | {ep_num} episodios | {total_steps} steps")
    top = sorted(action_hist.items(), key=lambda x: -x[1])
    print("  Acciones mas usadas (total):")
    for a, c in top[:8]:
        if c > 0:
            print(f"    {ACTION_NAMES.get(a, f'A{a}'):<16} : {c}")
    print("="*60)


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Watch — agente SF2CE Blanka jugando con modelo entrenado")
    ap.add_argument("--model",     type=str, default=None,
        help="Path al modelo (sin .zip). Por defecto: ultimo checkpoint en models/blanka/fase1/")
    ap.add_argument("--model_dir", type=str, default=MODELS_BASE,
        help=f"Carpeta donde buscar el ultimo checkpoint (default: {MODELS_BASE})")
    ap.add_argument("--vecnorm",   type=str, default=None,
        help="Path alternativo al .pkl de VecNormalize")
    ap.add_argument("--episodes",  type=int, default=0,
        help="Numero de episodios a ver (0 = infinito)")
    args = ap.parse_args()

    # Resolver VecNorm alternativo
    if args.vecnorm:
        VN_PATH = args.vecnorm

    # Resolver modelo
    if args.model:
        model_path = args.model
        if not os.path.exists(model_path + ".zip"):
            print(f"[WATCH] ERROR: no existe {model_path}.zip")
            sys.exit(1)
        print(f"[WATCH] Modelo especificado: {model_path}.zip")
    else:
        model_dir = args.model_dir
        model_path = find_latest_model(model_dir)
        if model_path is None:
            print(f"[WATCH] ERROR: no se encontro ningun .zip en {model_dir}")
            print(f"        Usa --model para especificar uno manualmente.")
            sys.exit(1)
        steps_str = os.path.basename(model_path)
        print(f"[WATCH] Ultimo modelo encontrado: {steps_str}.zip")

    watch(model_path=model_path, max_episodes=args.episodes)
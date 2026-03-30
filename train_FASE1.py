#!/usr/bin/env python3
"""
train_blanka_fase1.py — SF2CE | Curriculum Learning Fase 1 (CL-1)
==================================================================
Version: 2.1 (30/03/2026)

CAMBIOS v2.1:
  · [NUEVO] Tracking granular de bosses en TensorBoard y consola:
      cl1/boss_balrog_eps, cl1/boss_vega_eps, cl1/boss_sagat_eps,
      cl1/boss_bison_eps  → cuántos episodios se llegó a cada boss.
  · [NUEVO] cl1/arcade_clears → episodios en que se completó el arcade.
  · [NUEVO] cl1/in_bonus_stage_eps → episodios con bonus stage detectado.
  · [NUEVO] Print inmediato en consola al alcanzar cada boss por primera vez.
  · [NUEVO] Print inmediato en consola al completar el arcade (Bison KO).
  · [NUEVO] Resumen de bosses al final del entrenamiento.

CAMBIOS v2.0 (mantenidos):
  · [FIX CRÍTICO] max_steps del entorno: 3000 → 30000.
  · [FIX] registry.save() ahora se llama en Ctrl+C.
  · [FIX] Métricas rivals_defeated.

FASE CL-1: ROLLING_AND_ELECTRIC_ONLY
  6 instancias headless a máxima velocidad (-nothrottle).
  El entorno fuerza Rolling (dist>=150) o Electric (dist<150) cada step.
  Objetivo: hit rate > 40% en ambos moves.

USO:
  python train_FASE1.py                    # arrancar CL-1
  python train_FASE1.py --resume models/blanka/fase1/fase1_999912_steps
  python train_FASE1.py --stats
  python train_FASE1.py --visible          # instancia 0 con ventana
  python train_FASE1.py --envs 2           # solo 2 instancias
"""

import argparse
import os
import sys
import time
import subprocess
from collections import deque
from typing import Optional, List

import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import SubprocVecEnv, VecNormalize
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.callbacks import BaseCallback

from env.blanka_env import (
    BlankaEnv, CHAR_NAMES, BOSS_IDS, BOSS_ORDER, ARCADE_FINAL_BOSS,
    ROLLING_AND_ELECTRIC_ONLY,
    ROLLING_ACTIONS, ACTION_ROLLING, ACTION_ELECTRIC,
    N_ACTIONS,
)
from core.rival_registry import RivalRegistry

# ── CONFIG ────────────────────────────────────────────────────────────────────
N_ENVS      = 6
N_STEPS     = 1365      # x6 envs = 8190 steps/rollout
BATCH_SIZE  = 128
N_EPOCHS    = 4
GAMMA       = 0.99
GAE_LAMBDA  = 0.95
CLIP_RANGE  = 0.2
ENT_COEF    = 0.10
VF_COEF     = 0.5
MAX_GRAD    = 0.5
TARGET_KL   = 0.05
LR_DEFAULT  = 3e-4
TOTAL_STEPS = 1_000_000
SAVE_FREQ   = 8192

# max_steps del entorno — una run arcade completa
ENV_MAX_STEPS = 30000

# ── RUTAS ─────────────────────────────────────────────────────────────────────
MAME_DIR   = r"C:\proyectos\MAME"
DYN_DIR    = os.path.join(MAME_DIR, "dinamicos")
MAME_EXE   = r"C:\proyectos\MAME\EMULADOR\mame.exe"
LUA_SCRIPT = r"C:\proyectos\MAME\lua\autoplay_bridge.lua"
MODELS_DIR = os.path.join(MAME_DIR, "models", "blanka", "fase1")
LOGS_DIR   = os.path.join(MAME_DIR, "logs",   "blanka", "fase1")
VN_PATH    = os.path.join(MODELS_DIR, "fase1_vecnorm.pkl")
STATS_FILE = os.path.join(MAME_DIR, "rival_stats.json")
CLAIM_FILE = os.path.join(DYN_DIR, "instance_id_claim.txt")

def ver_file(i):     return os.path.join(DYN_DIR, f"bridge_version_{i}.txt")
def claimed_file(i): return os.path.join(DYN_DIR, f"instance_id_claimed_{i}.txt")
def input_file(i):   return os.path.join(DYN_DIR, f"mame_input_{i}.txt")
def state_file(i):   return os.path.join(DYN_DIR, f"state_{i}.txt")
def state_tmp(i):    return os.path.join(DYN_DIR, f"state_{i}.tmp")

os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR,   exist_ok=True)
os.makedirs(DYN_DIR,    exist_ok=True)


# ── LIMPIEZA ──────────────────────────────────────────────────────────────────

def _try_remove(path: str) -> bool:
    if not os.path.exists(path): return False
    try: os.remove(path); return True
    except Exception: return False

def clean_all(n: int):
    targets = [CLAIM_FILE]
    for i in range(n):
        targets += [ver_file(i), claimed_file(i),
                    input_file(i), state_file(i), state_tmp(i)]
    cnt = 0
    for f in targets:
        if os.path.exists(f):
            try: os.remove(f); cnt += 1
            except Exception as e: print(f"  [CLEAN] no se pudo borrar {f}: {e}")
    if cnt: print(f"  [CLEAN] {cnt} archivos de sesion anterior eliminados")


# ── LANZAR UNA INSTANCIA ──────────────────────────────────────────────────────

def launch_one(instance_id: int, visible: bool = False) -> Optional[subprocess.Popen]:
    if not os.path.exists(MAME_EXE):
        print(f"[MAME-{instance_id}] ERROR: no existe {MAME_EXE}"); return None

    for f in [CLAIM_FILE, claimed_file(instance_id), ver_file(instance_id)]:
        _try_remove(f)

    try:
        with open(CLAIM_FILE, "w") as f:
            f.write(str(instance_id))
        print(f"[MAME-{instance_id}] Claim escrito ({CLAIM_FILE} = {instance_id})")
    except Exception as e:
        print(f"[MAME-{instance_id}] ERROR escribiendo claim: {e}"); return None

    video = "d3d" if visible else "none"
    cmd = [
        MAME_EXE, "sf2ce",
        "-rompath",         os.path.join(MAME_DIR, "EMULADOR", "roms"),
        "-autoboot_script", LUA_SCRIPT,
        "-skip_gameinfo",
        "-sound", "none",
        "-video", video,
    ]
    if not visible:
        cmd += ["-nothrottle"]
    else:
        cmd += ["-window", "-nomaximize"]

    print(f"[MAME-{instance_id}] Lanzando video={video} throttle={visible}...")
    try:
        proc = subprocess.Popen(
            cmd, cwd=os.path.dirname(MAME_EXE),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        print(f"[MAME-{instance_id}] PID={proc.pid}")
    except Exception as e:
        print(f"[MAME-{instance_id}] ERROR Popen: {e}")
        _try_remove(CLAIM_FILE)
        return None

    cf = claimed_file(instance_id)
    print(f"[MAME-{instance_id}] Esperando que el Lua consuma el claim...")
    t0 = time.time()
    while time.time() - t0 < 25.0:
        if proc.poll() is not None:
            print(f"[MAME-{instance_id}] ERROR: proceso muerto antes de reclamar ID")
            return None
        if os.path.exists(cf) or not os.path.exists(CLAIM_FILE):
            print(f"[MAME-{instance_id}] Claim consumido ✓  ({time.time()-t0:.1f}s)")
            break
        time.sleep(0.2)
    else:
        print(f"[MAME-{instance_id}] WARN: claim no confirmado en 25s")
        _try_remove(CLAIM_FILE)

    vf = ver_file(instance_id)
    print(f"[MAME-{instance_id}] Esperando bridge_version_{instance_id}.txt...")
    t1 = time.time()
    while time.time() - t1 < 45.0:
        if proc.poll() is not None:
            print(f"[MAME-{instance_id}] ERROR: proceso muerto esperando bridge")
            return None
        if os.path.exists(vf):
            try:
                with open(vf) as fv: ver = fv.read().strip()
                if ver:
                    print(f"[MAME-{instance_id}] Bridge listo — {ver}  ({time.time()-t1:.1f}s) ✓")
                    return proc
            except Exception:
                pass
        time.sleep(0.3)

    print(f"[MAME-{instance_id}] WARN: bridge_version no apareció en 45s — continuando")
    return proc


def launch_all(n: int, visible_first: bool = False) -> List[subprocess.Popen]:
    procs = []
    for i in range(n):
        vis = visible_first and (i == 0)
        print(f"\n  ── Instancia {i} de {n-1} {'[VISIBLE]' if vis else '[headless]'} ──")
        proc = launch_one(i, visible=vis)
        if proc is None:
            print(f"[LAUNCH] FALLO en instancia {i} — abortando")
            for p in procs:
                try: p.terminate()
                except Exception: pass
            return []
        procs.append(proc)
        if i < n - 1:
            print(f"  1s antes de siguiente instancia...")
            time.sleep(1.0)

    print(f"\n[LAUNCH] {len(procs)}/{n} instancias activas ✓")
    print("  3s extra para que los menús arranquen...")
    time.sleep(3.0)
    return procs


# ── CALLBACKS ─────────────────────────────────────────────────────────────────

class CheckpointVN(BaseCallback):
    def __init__(self, save_freq, save_path, prefix, vn_path, verbose=1):
        super().__init__(verbose)
        self._freq = save_freq; self._path = save_path
        self._pfx  = prefix;    self._vn   = vn_path; self._last = 0

    def _init_callback(self): os.makedirs(self._path, exist_ok=True)

    def _on_step(self) -> bool:
        n = self.num_timesteps
        if n - self._last >= self._freq:
            self._last = n
            p = os.path.join(self._path, f"{self._pfx}_{n}_steps")
            self.model.save(p)
            self._save_vn()
            if self.verbose: print(f"  [Checkpoint] {p}.zip  (step={n:,})")
        return True

    def _save_vn(self):
        env = self.training_env
        for _ in range(5):
            if hasattr(env, "obs_rms"): env.save(self._vn); return
            env = getattr(env, "venv", None)
            if env is None: return


class CL1MetricsCallback(BaseCallback):
    """
    Métricas CL-1 para SubprocVecEnv (6 instancias).

    v2.1: tracking granular de bosses, bonus stages y arcade clears.
    v2.0: rivals_defeated, bonus stages, récords.
    v1.3: timeout_win (KO vs tiempo).

    Métricas TensorBoard nuevas (v2.1):
      cl1/boss_balrog_eps  — episodios en que se llegó a Balrog
      cl1/boss_vega_eps    — episodios en que se llegó a Vega
      cl1/boss_sagat_eps   — episodios en que se llegó a Sagat
      cl1/boss_bison_eps   — episodios en que se llegó a Bison
      cl1/any_boss_eps     — episodios con al menos 1 boss
      cl1/arcade_clears    — episodios en que se pasó el juego
      cl1/bonus_stage_eps  — episodios con bonus stage detectado
    """

    # IDs de los 4 bosses — mismo orden que aparecen en el arcade
    _BOSS_ORDER = [10, 11, 9, 8]  # Balrog, Vega, Sagat, Bison
    _BOSS_KEY   = {10: "balrog", 11: "vega", 9: "sagat", 8: "bison"}

    def __init__(self, registry: RivalRegistry, verbose: int = 1):
        super().__init__(verbose)
        self.registry = registry

        # ── métricas de episodio ──────────────────────────────────────────
        self._ep_count      = 0
        self._ep_wins       = deque(maxlen=100)
        self._ep_lens       = deque(maxlen=100)
        self._ep_p2dmg      = deque(maxlen=100)
        self._ep_round_wins = deque(maxlen=100)
        self._ep_rivals_def = deque(maxlen=100)

        # ── victorias por tipo ─────────────────────────────────────────────
        self._ko_wins      = 0
        self._timeout_wins = 0
        self._first_win    = False

        # ── bosses (v2.1) ─────────────────────────────────────────────────
        # Cuántos episodios se llegó a cada boss (acumulado total)
        self._boss_eps: dict = {bid: 0 for bid in self._BOSS_ORDER}
        # Primer episodio en que se vio cada boss por primera vez
        self._boss_first_ep: dict = {}
        # Episodios en que hay al menos 1 boss
        self._any_boss_eps = 0

        # ── bonus stages (v2.1) ───────────────────────────────────────────
        self._bonus_stage_eps = 0
        self._bonus_first_ep: Optional[int] = None

        # ── arcade clears (v2.1) ──────────────────────────────────────────
        self._arcade_clears   = 0
        self._arcade_first_ep: Optional[int] = None

        # ── progreso / récords ────────────────────────────────────────────
        self._rival_counts: dict = {}
        self._max_rivals        = 0
        self._max_bosses_ep     = 0  # mayor número de bosses distintos en 1 episodio

        self._last_roll = -1

    def _on_step(self) -> bool:
        for info in self.locals.get("infos", []):
            if "episode" not in info:
                continue

            ep           = info["episode"]
            won          = info.get("won",                False)
            t_w          = info.get("timeout_win",        False)
            rw           = info.get("round_wins",         0)
            seq          = info.get("arcade_sequence",    [])
            bon          = info.get("reached_bonus",      False)
            p2           = info.get("p2_hp",              144.0)
            rival        = info.get("rival",              0xFF)
            rivals_def   = info.get("rivals_defeated",    0)
            bosses_ids   = info.get("bosses_reached_ids", [])
            bosses_count = info.get("bosses_reached_count", 0)
            arcade_clear = info.get("arcade_cleared",     False)

            self._ep_count += 1
            self._ep_wins.append(1 if won else 0)
            self._ep_lens.append(ep.get("l", 0))
            self._ep_p2dmg.append(max(0.0, 144.0 - float(p2)))
            self._ep_round_wins.append(rw)
            self._ep_rivals_def.append(rivals_def)

            # ── rivales alcanzados ────────────────────────────────────────
            for cid in seq:
                self._rival_counts[cid] = self._rival_counts.get(cid, 0) + 1

            if rivals_def > self._max_rivals:
                self._max_rivals = rivals_def
                rname = CHAR_NAMES.get(rival, f"ID_{rival}")
                print(
                    f"\n[⭐ NUEVO RÉCORD CL-1] {rivals_def} rivales derrotados"
                    f" | último: {rname} | steps={self.num_timesteps:,}"
                )

            # ── bonus stages (v2.1) ───────────────────────────────────────
            if bon:
                self._bonus_stage_eps += 1
                if self._bonus_first_ep is None:
                    self._bonus_first_ep = self._ep_count
                    print(
                        f"\n[⭐ PRIMER BONUS STAGE CL-1] "
                        f"ep={self._ep_count} | steps={self.num_timesteps:,}"
                    )

            # ── bosses (v2.1) ─────────────────────────────────────────────
            if bosses_count > 0:
                self._any_boss_eps += 1

                if bosses_count > self._max_bosses_ep:
                    self._max_bosses_ep = bosses_count
                    bnames = [CHAR_NAMES.get(b, f"ID_{b}") for b in bosses_ids]
                    print(
                        f"\n[🏆 NUEVO RÉCORD BOSSES CL-1] {bosses_count} bosses en 1 ep"
                        f" | {bnames} | steps={self.num_timesteps:,}"
                    )

                for boss_id in bosses_ids:
                    self._boss_eps[boss_id] = self._boss_eps.get(boss_id, 0) + 1

                    if boss_id not in self._boss_first_ep:
                        self._boss_first_ep[boss_id] = self._ep_count
                        bname = CHAR_NAMES.get(boss_id, f"ID_{boss_id}")
                        print(
                            f"\n[⚔️  PRIMER BOSS CL-1: {bname}] "
                            f"ep={self._ep_count} | steps={self.num_timesteps:,} "
                            f"| rivals previos={rivals_def}"
                        )

            # ── arcade clear (v2.1) ───────────────────────────────────────
            if arcade_clear:
                self._arcade_clears += 1
                if self._arcade_first_ep is None:
                    self._arcade_first_ep = self._ep_count
                    print(
                        f"\n[🎮 ¡¡ARCADE CLEAR!! CL-1] "
                        f"ep={self._ep_count} | steps={self.num_timesteps:,} "
                        f"| rivales_derrotados={rivals_def}"
                    )
                else:
                    print(
                        f"\n[🎮 ARCADE CLEAR #{self._arcade_clears} CL-1] "
                        f"ep={self._ep_count} | steps={self.num_timesteps:,}"
                    )

            # ── victorias ────────────────────────────────────────────────
            if won:
                if t_w: self._timeout_wins += 1
                else:   self._ko_wins      += 1

                if not self._first_win:
                    self._first_win = True
                    rname    = CHAR_NAMES.get(rival, f"ID_{rival}")
                    win_type = "TIEMPO" if t_w else "KO"
                    print(
                        f"\n[🏆 PRIMERA VICTORIA CL-1] ep={self._ep_count}"
                        f" vs {rname} | tipo={win_type} | steps={self.num_timesteps:,}"
                    )

        # Log en cada rollout
        roll = self.n_calls // N_STEPS
        if roll > self._last_roll:
            self._last_roll = roll
            self._log()

        return True

    def _log(self):
        if not self._ep_wins:
            return

        wr  = np.mean(self._ep_wins) * 100
        al  = np.mean(self._ep_lens)
        ad  = np.mean(self._ep_p2dmg)
        ar  = np.mean(self._ep_round_wins)
        ard = np.mean(self._ep_rivals_def) if self._ep_rivals_def else 0.0
        total_wins = self._ko_wins + self._timeout_wins

        print(f"\n{'═'*65}")
        print(f"  CL-1 | Rollout {self._last_roll} | Steps {self.num_timesteps:,}")
        print(f"  Episodios      : {self._ep_count}")
        print(f"  Win rate       : {wr:.1f}%  (ventana {len(self._ep_wins)})")
        if total_wins > 0:
            print(f"    └─ Por KO    : {self._ko_wins}  |  Por Tiempo: {self._timeout_wins}")
        print(f"  Avg round wins : {ar:.2f}  | Avg P2 dmg  : {ad:.1f}")
        print(f"  Avg ep len     : {al:.0f}  | Avg rivales : {ard:.2f}")
        print(f"  Max rivales/ep : {self._max_rivals}")

        # ── bosses ────────────────────────────────────────────────────────
        print(f"  ── BOSSES ─────────────────────────────────────────────")
        for boss_id in self._BOSS_ORDER:
            bname  = CHAR_NAMES[boss_id]
            count  = self._boss_eps.get(boss_id, 0)
            pct    = count * 100.0 / max(self._ep_count, 1)
            first  = self._boss_first_ep.get(boss_id)
            marker = "✓" if first is not None else "✗"
            print(
                f"    {marker} {bname:10s}: {count:4d} eps ({pct:5.1f}%)"
                + (f"  [primer ep: {first}]" if first else "")
            )
        print(f"  Max bosses/ep  : {self._max_bosses_ep}")
        print(f"  Bonus stages   : {self._bonus_stage_eps} eps"
              + (f"  [primer ep: {self._bonus_first_ep}]" if self._bonus_first_ep else ""))
        print(f"  Arcade clears  : {self._arcade_clears}"
              + (f"  [primer ep: {self._arcade_first_ep}]" if self._arcade_first_ep else ""))

        # ── top rivales ───────────────────────────────────────────────────
        if self._rival_counts:
            top = sorted(self._rival_counts.items(), key=lambda x: -x[1])[:6]
            print("  Top rivales    : " + ", ".join(
                f"{CHAR_NAMES.get(c,'?')}:{n}" for c, n in top))

        print(f"{'═'*65}")

        # ── TensorBoard ───────────────────────────────────────────────────
        self.logger.record("cl1/win_rate",           wr)
        self.logger.record("cl1/ko_wins",            self._ko_wins)
        self.logger.record("cl1/timeout_wins",       self._timeout_wins)
        self.logger.record("cl1/avg_ep_len",         al)
        self.logger.record("cl1/avg_p2_damage",      ad)
        self.logger.record("cl1/avg_round_wins",     ar)
        self.logger.record("cl1/avg_rivals_per_ep",  ard)
        self.logger.record("cl1/max_rivals_per_ep",  self._max_rivals)

        # Bosses (v2.1)
        for boss_id in self._BOSS_ORDER:
            key   = self._BOSS_KEY[boss_id]
            count = self._boss_eps.get(boss_id, 0)
            self.logger.record(f"cl1/boss_{key}_eps", count)

        self.logger.record("cl1/any_boss_eps",     self._any_boss_eps)
        self.logger.record("cl1/max_bosses_per_ep", self._max_bosses_ep)
        self.logger.record("cl1/bonus_stage_eps",  self._bonus_stage_eps)
        self.logger.record("cl1/arcade_clears",    self._arcade_clears)

        self.logger.dump(self.num_timesteps)

        if self._last_roll % 20 == 0:
            self.registry.print_summary()

    def print_final_summary(self):
        """Resumen final impreso al terminar el entrenamiento."""
        print(f"\n{'═'*65}")
        print(f"  CL-1 — RESUMEN FINAL")
        print(f"{'═'*65}")
        print(f"  Total episodios  : {self._ep_count}")
        print(f"  KO wins          : {self._ko_wins}")
        print(f"  Timeout wins     : {self._timeout_wins}")
        print(f"  Max rivales/ep   : {self._max_rivals}")
        print(f"  Bonus stages     : {self._bonus_stage_eps} eps"
              + (f" (primer ep #{self._bonus_first_ep})" if self._bonus_first_ep else " (ninguno)"))
        print(f"  Arcade clears    : {self._arcade_clears}"
              + (f" (primer ep #{self._arcade_first_ep})" if self._arcade_first_ep else " (ninguno)"))
        print(f"  ── Bosses alcanzados ──")
        for boss_id in self._BOSS_ORDER:
            bname  = CHAR_NAMES[boss_id]
            count  = self._boss_eps.get(boss_id, 0)
            pct    = count * 100.0 / max(self._ep_count, 1)
            first  = self._boss_first_ep.get(boss_id)
            marker = "✓" if first else "✗"
            print(
                f"    {marker} {bname:10s}: {count:4d} eps ({pct:5.1f}%)"
                + (f"  [primer ep: {first}]" if first else "  [NUNCA ALCANZADO]")
            )
        print(f"{'═'*65}")


# ── MAKE ENV ──────────────────────────────────────────────────────────────────

def make_env(instance_id: int, registry: RivalRegistry):
    def _init():
        env = BlankaEnv(
            instance_id=instance_id,
            max_steps=ENV_MAX_STEPS,
            registry=registry,
        )
        env = Monitor(env, os.path.join(LOGS_DIR, f"monitor_{instance_id}"))
        return env
    return _init


# ── TRAIN ─────────────────────────────────────────────────────────────────────

def train(resume_path=None, lr=LR_DEFAULT, total_steps=TOTAL_STEPS,
          visible=False, n_envs=N_ENVS):

    print("\n" + "="*65)
    print("  SF2CE — PPO Blanka | FASE CL-1 (Rolling + Electric)")
    print("="*65)
    print(f"  Instancias : {n_envs}  |  Steps: {total_steps:,}  |  LR: {lr}")
    print(f"  ENV_MAX_STEPS             : {ENV_MAX_STEPS}")
    print(f"  ROLLING_AND_ELECTRIC_ONLY : {ROLLING_AND_ELECTRIC_ONLY}")
    print(f"  Archivos dinámicos        : {DYN_DIR}")
    print(f"  Modelos                   : {MODELS_DIR}")
    print(f"  Logs TensorBoard          : {LOGS_DIR}")
    print("="*65)

    if not ROLLING_AND_ELECTRIC_ONLY:
        print("\n⚠️  AVISO: ROLLING_AND_ELECTRIC_ONLY = False en blanka_env.py")
        print("   Para CL-1 ponlo a True y vuelve a lanzar.\n")

    print("\n[0/4] Limpiando archivos previos en dinamicos/...")
    clean_all(n_envs)

    print(f"\n[1/4] Lanzando {n_envs} instancias MAME (claim protocol)...")
    procs = launch_all(n_envs, visible_first=visible)
    if not procs:
        print("ERROR: sin instancias. Abortando."); sys.exit(1)

    print("\n[2/4] Registro de rivales...")
    registry = RivalRegistry(STATS_FILE)
    registry.print_summary()

    print(f"\n[3/4] Creando {n_envs} entornos SubprocVecEnv...")
    vec_env = SubprocVecEnv(
        [make_env(i, registry) for i in range(n_envs)],
        start_method="spawn",
    )
    is_new = resume_path is None

    if not is_new and os.path.exists(VN_PATH):
        print(f"  Cargando VecNormalize: {VN_PATH}")
        vec_env = VecNormalize.load(VN_PATH, vec_env)
        vec_env.training = True
    else:
        vec_env = VecNormalize(
            vec_env, norm_obs=True, norm_reward=True,
            clip_obs=10.0, clip_reward=10.0, gamma=GAMMA,
        )

    print("\n[4/4] Modelo PPO...")
    hp = dict(
        n_steps       = N_STEPS,
        batch_size    = BATCH_SIZE,
        n_epochs      = N_EPOCHS,
        gamma         = GAMMA,
        gae_lambda    = GAE_LAMBDA,
        clip_range    = CLIP_RANGE,
        ent_coef      = ENT_COEF,
        vf_coef       = VF_COEF,
        max_grad_norm = MAX_GRAD,
        target_kl     = TARGET_KL,
        policy_kwargs = dict(net_arch=[256, 256]),
        verbose       = 1,
        tensorboard_log = LOGS_DIR,
    )

    if not is_new:
        model = PPO.load(
            resume_path, env=vec_env, learning_rate=lr,
            tensorboard_log=LOGS_DIR, device="auto",
        )
        model.learning_rate = lr
        model.lr_schedule   = lambda _: lr
        for pg in model.policy.optimizer.param_groups:
            pg["lr"] = lr
    else:
        model = PPO("MlpPolicy", vec_env, learning_rate=lr, device="auto", **hp)

    params = sum(p.numel() for p in model.policy.parameters())
    print(f"  Parámetros: {params:,}\n")

    metrics_cb = CL1MetricsCallback(registry)
    cbs = [
        CheckpointVN(SAVE_FREQ, MODELS_DIR, "fase1", VN_PATH),
        metrics_cb,
    ]

    print("Iniciando entrenamiento CL-1...")
    print("  Rolling: dist>=150  |  Electric: dist<150")
    print("  Episodio = run arcade completa (termina en Game Over real O arcade clear)\n")
    print("  TensorBoard: tensorboard --logdir", LOGS_DIR)
    print("  Métricas de bosses: cl1/boss_balrog_eps, cl1/boss_vega_eps,")
    print("                      cl1/boss_sagat_eps,  cl1/boss_bison_eps")
    print("  Arcade clears:      cl1/arcade_clears\n")

    t0 = time.time()
    try:
        model.learn(
            total_timesteps     = total_steps,
            callback            = cbs,
            reset_num_timesteps = is_new,
            tb_log_name         = "ppo_blanka_fase1",
        )
    except KeyboardInterrupt:
        print("\n[CTRL+C] Guardando modelo y stats...")

    # ── GUARDADO FINAL — siempre se ejecuta, incluso tras Ctrl+C ─────────────
    elapsed   = time.time() - t0
    final_mdl = os.path.join(MODELS_DIR, "fase1_final")
    model.save(final_mdl)
    print(f"  Modelo  → {final_mdl}.zip")

    env_ = vec_env
    for _ in range(5):
        if hasattr(env_, "obs_rms"):
            env_.save(VN_PATH)
            print(f"  VecNorm → {VN_PATH}")
            break
        env_ = getattr(env_, "venv", None)
        if env_ is None:
            break

    registry.save()
    print(f"  Stats   → {STATS_FILE}")

    print(f"\n{'='*65}")
    print(f"  CL-1 COMPLETADA  |  {elapsed/3600:.2f}h  |  {model.num_timesteps:,} steps")
    print(f"{'='*65}")

    metrics_cb.print_final_summary()

    print(f"\n  Modelo: {final_mdl}.zip")
    print(f"  Para CL-2: pon ROLLING_AND_ELECTRIC_ONLY=False y usa:")
    print(f"    python train_blanka_v1.py --resume {final_mdl}")

    registry.print_summary()

    try:
        vec_env.close()
    except Exception:
        pass

    print("\n[CLEANUP] Terminando MAMEs...")
    for i, p in enumerate(procs):
        try:
            p.terminate()
            print(f"  MAME-{i} PID={p.pid} terminado")
        except Exception:
            pass


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="SF2CE CL-1 — 6 envs headless")
    ap.add_argument("--resume",  type=str,   default=None,
        help="Checkpoint previo (sin .zip)")
    ap.add_argument("--lr",      type=float, default=None,
        help="Learning rate (default auto)")
    ap.add_argument("--steps",   type=int,   default=TOTAL_STEPS,
        help=f"Total steps (default {TOTAL_STEPS:,})")
    ap.add_argument("--envs",    type=int,   default=N_ENVS,
        help=f"Número de instancias MAME (default {N_ENVS})")
    ap.add_argument("--visible", action="store_true",
        help="Primera instancia con ventana visible")
    ap.add_argument("--stats",   action="store_true",
        help="Mostrar estadísticas de rivales y salir")
    args = ap.parse_args()

    if args.stats:
        RivalRegistry(STATS_FILE).print_summary()
        sys.exit(0)

    lr = args.lr if args.lr else (1e-4 if args.resume else LR_DEFAULT)
    if args.resume and not args.lr:
        print(f"[INFO] Resume → lr automático = {lr}")

    train(
        resume_path = args.resume,
        lr          = lr,
        total_steps = args.steps,
        visible     = args.visible,
        n_envs      = args.envs,
    )
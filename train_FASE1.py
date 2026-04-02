#!/usr/bin/env python3
"""
train_FASE1.py — SF2CE PPO Blanka | CL-1 Multi-Env (6 instancias headless)
===========================================================================
Versión: 3.1 (01/04/2026)

CAMBIOS v3.1 — HIPERPARÁMETROS PPO CORREGIDOS PARA ROMPER COLAPSO DE ENTROPÍA:

  PROBLEMA (v3.0):
    A 470k steps el agente tenía 0 rolling_uses, 0 electric_uses, entropía -1.86
    (colapsada desde distribución uniforme -1.95 para 7 acciones) y
    avg_p2_damage=72 constante. La política había convergido prematuramente a
    2 acciones (NOOP + un movimiento) sin explorar Rolling ni Electric.

    Causa raíz: rollout de 24.576 steps (6×4096) a 710 FPS = ~34 segundos de
    experiencia por update. Con ent_coef=0.03, la entropía cae en los primeros
    2-3 updates antes de que el agente vea suficiente variedad para aprender.
    batch_size=128 con 24.576 steps = 192 minibatches → overfitting rápido.

  CORRECCIONES:
    · ent_coef: 0.03 → 0.05
      Aumenta el incentivo de exploración un 67%. Con 7 acciones CL-1,
      la entropía máxima es ln(7)≈1.95. A 0.05, el término de entropía
      contribuye ~0.097 al loss, suficiente para resistir colapso prematuro.

    · n_steps: 4096 → 2048
      Rollout efectivo: 6×2048 = 12.288 steps por update (vs 24.576).
      A 710 FPS: ~17 segundos de experiencia por update.
      Más updates = la política recibe corrección antes de colapsar.

    · batch_size: 128 → 256
      Con 12.288 steps y batch=256: 48 minibatches por update (vs 192).
      Reduce el overfitting por update. El gradiente ve más variedad.

    · n_epochs: 4 → 10
      Más épocas sobre cada rollout para extraer más aprendizaje por
      experiencia colectada. Compensado por el batch más grande.

    · target_kl: 0.02 → 0.01
      Freno más conservador para que la política no se aleje demasiado.
      Con ent_coef alto, es importante evitar updates demasiado agresivos.

    · learning_rate nuevo: 1e-4 (en lugar de 3e-4 para modelos nuevos)
      Con ent_coef alto y rollout corto, lr=3e-4 puede causar inestabilidad.

DESCRIPCIÓN (mantenida de v3.0):
  Clon EXACTO de train_blanka_v1.py (Fase 2) adaptado para ejecutar
  6 instancias de MAME en paralelo, todas headless por defecto.

  Los únicos cambios respecto a train_blanka_v1.py son:
    · N_ENVS = 6  (SubprocVecEnv en lugar de DummyVecEnv)
    · Lanzamiento multi-instancia con protocolo claim (launch_all)
    · Todos los procesos MAME sin ventana por defecto (-nothrottle + -video none)
    · --visible (flag opcional) muestra la instancia 0 con ventana
    · Rutas apuntan a models/blanka/fase1  y  logs/blanka/fase1

USO:
  # Entrenamiento nuevo (6 MAMEs headless):
  python train_FASE1.py

  # Con instancia 0 visible (para supervisión):
  python train_FASE1.py --visible

  # Solo 2 instancias:
  python train_FASE1.py --envs 2

  # Reanudar desde checkpoint:
  python train_FASE1.py --resume models/blanka/fase1/fase1_999912_steps

  # Ver resumen de rivales:
  python train_FASE1.py --stats

FLUJO AUTOMÁTICO:
  1. Limpia archivos dinámicos de sesión anterior
  2. Lanza N instancias de MAME secuencialmente (protocolo claim)
  3. Cada instancia obtiene su instance_id único via claim file
  4. El Lua navega menús y selecciona Blanka automáticamente en cada instancia
  5. SubprocVecEnv sincroniza los 6 entornos en paralelo
  6. El agente PPO juega runs de arcade continuas en los 6 MAMEs
  7. Checkpoints automáticos cada 8192 steps en models/blanka/fase1/
  8. Al terminar (Ctrl+C o steps agotados): guarda modelo, VecNorm y stats
"""

import argparse
import os
import sys
import time
import subprocess
from collections import deque
from typing import Optional, List

import numpy as np

# ── PATH SETUP ────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import SubprocVecEnv, VecNormalize
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.callbacks import BaseCallback

from env.blanka_env import (
    BlankaEnv, CHAR_NAMES, ROLLING_ACTIONS, ACTION_ELECTRIC,
    BOSS_IDS, BOSS_ORDER, ARCADE_FINAL_BOSS,
)
from core.rival_registry import RivalRegistry

# ── CONFIG MULTI-ENV ──────────────────────────────────────────────────────────
N_ENVS = 6          # instancias MAME por defecto

# ── RUTAS ─────────────────────────────────────────────────────────────────────
MAME_DIR   = r"C:\proyectos\MAME"
DYN_DIR    = os.path.join(MAME_DIR, "dinamicos")
MAME_EXE   = r"C:\proyectos\MAME\EMULADOR\mame.exe"
LUA_SCRIPT = r"C:\proyectos\MAME\lua\autoplay_bridge.lua"
MODELS_DIR = os.path.join(MAME_DIR, "models", "blanka", "fase1")
LOGS_DIR   = os.path.join(MAME_DIR, "logs",   "blanka", "fase1")
VN_PATH    = os.path.join(MODELS_DIR, "vecnorm_fase1.pkl")
STATS_FILE = os.path.join(MAME_DIR, "rival_stats.json")
CLAIM_FILE = os.path.join(DYN_DIR, "instance_id_claim.txt")

os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR,   exist_ok=True)
os.makedirs(DYN_DIR,    exist_ok=True)

# ── HELPERS DE RUTAS DINÁMICAS ────────────────────────────────────────────────
def ver_file(i):      return os.path.join(DYN_DIR, f"bridge_version_{i}.txt")
def claimed_file(i):  return os.path.join(DYN_DIR, f"instance_id_claimed_{i}.txt")
def input_file(i):    return os.path.join(DYN_DIR, f"mame_input_{i}.txt")
def state_file(i):    return os.path.join(DYN_DIR, f"state_{i}.txt")
def state_tmp(i):     return os.path.join(DYN_DIR, f"state_{i}.tmp")

# ── ACCIONES (igual que train_blanka_v1.py) ───────────────────────────────────
ACTION_ROLLING_FIERCE = 15
ACTION_ROLLING_STRONG = 16
ACTION_ROLLING_JAB    = 17
ACTION_ELECTRIC_ID    = 18
ACTION_ROLLING_JUMP   = 25

# ── HIPERPARÁMETROS PPO — v3.1 (anti-colapso de entropía) ────────────────────
# Cambios respecto a v3.0:
#   ent_coef:   0.03  → 0.05  (más exploración, resistir colapso prematuro)
#   n_steps:    4096  → 2048  (rollout más corto → más updates → corrección antes)
#   batch_size: 128   → 256   (menos minibatches por update → menos overfitting)
#   n_epochs:   4     → 10    (más aprovechamiento de cada rollout)
#   target_kl:  0.02  → 0.01  (freno más fino con ent_coef alto)
# Rollout efectivo: 6 envs × 2048 = 12.288 steps por update
PPO_HPARAMS = dict(
    n_steps       = 2048,    # 6 envs × 2048 = 12.288 steps por rollout
    batch_size    = 256,
    n_epochs      = 10,
    gamma         = 0.99,
    gae_lambda    = 0.95,
    clip_range    = 0.2,
    ent_coef      = 0.05,    # v3.1: 0.03 → 0.05
    vf_coef       = 0.5,
    max_grad_norm = 0.5,
    target_kl     = 0.01,    # v3.1: 0.02 → 0.01
    policy_kwargs = dict(net_arch=[256, 256]),
    verbose       = 1,
    tensorboard_log = LOGS_DIR,
)

# ── BOSS TRACKING ─────────────────────────────────────────────────────────────
_BOSS_ORDER = [10, 11, 9, 8]   # Balrog, Vega, Sagat, Bison
_BOSS_KEY   = {10: "balrog", 11: "vega", 9: "sagat", 8: "bison"}


# ── LIMPIEZA DE ARCHIVOS PREVIOS ──────────────────────────────────────────────

def _try_remove(path: str) -> bool:
    if not os.path.exists(path):
        return False
    try:
        os.remove(path)
        return True
    except Exception:
        return False


def clean_all(n: int):
    """Elimina archivos de sesión anterior para evitar colisiones de IDs."""
    targets = [CLAIM_FILE]
    for i in range(n):
        targets += [ver_file(i), claimed_file(i),
                    input_file(i), state_file(i), state_tmp(i)]
    cnt = sum(1 for f in targets if _try_remove(f))
    if cnt:
        print(f"  [CLEAN] {cnt} archivos de sesión anterior eliminados")


# ── LANZAR UNA INSTANCIA MAME ─────────────────────────────────────────────────

def launch_one(instance_id: int, visible: bool = False) -> Optional[subprocess.Popen]:
    """
    Lanza una instancia MAME con el protocolo claim.

    El Lua lee CLAIM_FILE, adopta ese instance_id y escribe
    instance_id_claimed_N.txt para confirmar. Luego escribe
    bridge_version_N.txt cuando el bridge está listo.
    """
    if not os.path.exists(MAME_EXE):
        print(f"[MAME-{instance_id}] ERROR: no existe {MAME_EXE}")
        return None

    # Limpiar claim y archivos de esta instancia
    for f in [CLAIM_FILE, claimed_file(instance_id), ver_file(instance_id)]:
        _try_remove(f)

    # Escribir claim
    try:
        with open(CLAIM_FILE, "w") as f:
            f.write(str(instance_id))
        print(f"[MAME-{instance_id}] Claim escrito ({CLAIM_FILE} = {instance_id})")
    except Exception as e:
        print(f"[MAME-{instance_id}] ERROR escribiendo claim: {e}")
        return None

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

    # Esperar a que el Lua consuma el claim
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

    # Esperar a que el bridge escriba su version file
    vf = ver_file(instance_id)
    print(f"[MAME-{instance_id}] Esperando bridge_version_{instance_id}.txt...")
    t1 = time.time()
    while time.time() - t1 < 45.0:
        if proc.poll() is not None:
            print(f"[MAME-{instance_id}] ERROR: proceso muerto esperando bridge")
            return None
        if os.path.exists(vf):
            try:
                with open(vf) as fv:
                    ver = fv.read().strip()
                if ver:
                    print(f"[MAME-{instance_id}] Bridge listo — {ver}  ({time.time()-t1:.1f}s) ✓")
                    return proc
            except Exception:
                pass
        time.sleep(0.3)

    print(f"[MAME-{instance_id}] WARN: bridge_version no apareció en 45s — continuando")
    return proc


def launch_all(n: int, visible_first: bool = False) -> List[subprocess.Popen]:
    """Lanza N instancias MAME secuencialmente con el protocolo claim."""
    procs = []
    for i in range(n):
        vis = visible_first and (i == 0)
        print(f"\n  ── Instancia {i}/{n-1} {'[VISIBLE]' if vis else '[headless]'} ──")
        proc = launch_one(i, visible=vis)
        if proc is None:
            print(f"[LAUNCH] FALLO en instancia {i} — abortando")
            for p in procs:
                try:
                    p.terminate()
                except Exception:
                    pass
            return []
        procs.append(proc)
        if i < n - 1:
            print("  1s antes de siguiente instancia...")
            time.sleep(1.0)

    print(f"\n[LAUNCH] {len(procs)}/{n} instancias activas ✓")
    print("  3s extra para que los menús arranquen...")
    time.sleep(3.0)
    return procs


# ── CALLBACKS ─────────────────────────────────────────────────────────────────

class CheckpointVN(BaseCallback):
    def __init__(self, save_freq: int, save_path: str,
                 prefix: str, vn_path: str, verbose: int = 1):
        super().__init__(verbose)
        self._freq    = save_freq
        self._path    = save_path
        self._prefix  = prefix
        self._vn_path = vn_path
        self._last    = 0

    def _init_callback(self):
        os.makedirs(self._path, exist_ok=True)

    def _on_step(self) -> bool:
        n = self.num_timesteps
        if n - self._last >= self._freq:
            self._last = n
            p = os.path.join(self._path, f"{self._prefix}_{n}_steps")
            self.model.save(p)
            self._save_vn()
            if self.verbose:
                print(f"  [Checkpoint] {p}.zip  (step={n:,})")
        return True

    def _save_vn(self):
        env = self.training_env
        for _ in range(5):
            if hasattr(env, "obs_rms"):
                env.save(self._vn_path)
                return
            env = getattr(env, "venv", None)
            if env is None:
                return


class MetricsCallback(BaseCallback):
    """
    Métricas en consola + TensorBoard por rollout.
    Prefijo sf2/. Adaptado para SubprocVecEnv (lista de infos por step).
    """

    def __init__(self, registry: RivalRegistry, verbose: int = 1):
        super().__init__(verbose)
        self.registry        = registry

        self._ep_wins        = deque(maxlen=100)
        self._ep_lens        = deque(maxlen=100)
        self._ep_p2dmg       = deque(maxlen=100)
        self._ep_rivals_def  = deque(maxlen=100)
        self._ep_count       = 0

        self._roll_fierce    = 0
        self._roll_strong    = 0
        self._roll_jab       = 0
        self._elec_uses      = 0
        self._rjump_uses     = 0
        self._fk_rolls       = 0
        self._ko_wins        = 0
        self._timeout_wins   = 0
        self._last_roll      = -1
        self._first_win      = False

        self._rival_eps: dict = {}
        self._max_rivals_ep  = 0

        # ── boss tracking ──────────────────────────────────────────────────
        self._boss_eps: dict      = {bid: 0 for bid in _BOSS_ORDER}
        self._boss_first_ep: dict = {}
        self._any_boss_eps        = 0
        self._max_bosses_ep       = 0

        # ── arcade clears ──────────────────────────────────────────────────
        self._arcade_clears             = 0
        self._arcade_first_ep: Optional[int] = None

        # ── bonus stages ───────────────────────────────────────────────────
        self._bonus_stage_eps           = 0
        self._bonus_first_ep: Optional[int] = None

        # último rival visto (para el log)
        self._last_rival = 0xFF

    def _on_step(self) -> bool:
        # SubprocVecEnv devuelve una lista de infos (uno por env)
        infos = self.locals.get("infos", [{}])

        for info in infos:
            action      = info.get("action",      -1)
            p2hp        = info.get("p2_hp",       144.0)
            fk_land     = info.get("fk_land",     0)
            rival       = info.get("rival",       0xFF)
            self._last_rival = rival

            if action == ACTION_ROLLING_FIERCE:                     self._roll_fierce += 1
            if action == ACTION_ROLLING_STRONG:                     self._roll_strong += 1
            if action == ACTION_ROLLING_JAB:                        self._roll_jab    += 1
            if action == ACTION_ELECTRIC_ID:                        self._elec_uses   += 1
            if action == ACTION_ROLLING_JUMP:                       self._rjump_uses  += 1
            if action in ROLLING_ACTIONS and 0 < fk_land <= 20:    self._fk_rolls    += 1

            if "episode" in info:
                ep            = info["episode"]
                won           = info.get("won",                    False)
                t_w           = info.get("timeout_win",            False)
                rivals_def    = info.get("rivals_defeated",        0)
                reached_bonus = info.get("reached_bonus",          False)
                bosses_ids    = info.get("bosses_reached_ids",     [])
                bosses_count  = info.get("bosses_reached_count",   0)
                arcade_clear  = info.get("arcade_cleared",         False)

                self._ep_count += 1
                self._ep_wins.append(1 if won else 0)
                self._ep_lens.append(ep.get("l", 0))
                self._ep_p2dmg.append(max(0.0, 144.0 - float(p2hp)))
                self._ep_rivals_def.append(rivals_def)

                if rival <= 11:
                    self._rival_eps[rival] = self._rival_eps.get(rival, 0) + 1

                # ── récord rivales ─────────────────────────────────────────
                if rivals_def > self._max_rivals_ep:
                    self._max_rivals_ep = rivals_def
                    rname = CHAR_NAMES.get(rival, f"ID_{rival}")
                    print(
                        f"\n[🏆 NUEVO RÉCORD] {rivals_def} rivales derrotados"
                        f" | último: {rname} | steps={self.num_timesteps:,}"
                    )

                # ── bonus stage ────────────────────────────────────────────
                if reached_bonus:
                    self._bonus_stage_eps += 1
                    if self._bonus_first_ep is None:
                        self._bonus_first_ep = self._ep_count
                        print(
                            f"\n[⭐ PRIMER BONUS STAGE] ep={self._ep_count}"
                            f" | steps={self.num_timesteps:,}"
                        )

                # ── bosses ─────────────────────────────────────────────────
                if bosses_count > 0:
                    self._any_boss_eps += 1

                    if bosses_count > self._max_bosses_ep:
                        self._max_bosses_ep = bosses_count
                        bnames = [CHAR_NAMES.get(b, f"ID_{b}") for b in bosses_ids]
                        print(
                            f"\n[🏆 RÉCORD BOSSES] {bosses_count} bosses en 1 ep"
                            f" | {bnames} | steps={self.num_timesteps:,}"
                        )

                    for boss_id in bosses_ids:
                        self._boss_eps[boss_id] = self._boss_eps.get(boss_id, 0) + 1
                        if boss_id not in self._boss_first_ep:
                            self._boss_first_ep[boss_id] = self._ep_count
                            bname = CHAR_NAMES.get(boss_id, f"ID_{boss_id}")
                            print(
                                f"\n[⚔️  PRIMER BOSS: {bname}]"
                                f" ep={self._ep_count} | steps={self.num_timesteps:,}"
                            )

                # ── arcade clear ───────────────────────────────────────────
                if arcade_clear:
                    self._arcade_clears += 1
                    if self._arcade_first_ep is None:
                        self._arcade_first_ep = self._ep_count
                        print(
                            f"\n[🎮 ¡¡ARCADE CLEAR!! #{self._arcade_clears}]"
                            f" ep={self._ep_count} | steps={self.num_timesteps:,}"
                            f" | rivales={rivals_def}"
                        )
                    else:
                        print(
                            f"\n[🎮 ARCADE CLEAR #{self._arcade_clears}]"
                            f" ep={self._ep_count} | steps={self.num_timesteps:,}"
                        )

                # ── victorias ──────────────────────────────────────────────
                if won:
                    if t_w: self._timeout_wins += 1
                    else:   self._ko_wins      += 1

                    if not self._first_win:
                        self._first_win = True
                        rname    = CHAR_NAMES.get(rival, f"ID_{rival}")
                        win_type = "TIEMPO" if t_w else "KO"
                        print(
                            f"\n[🏆 PRIMERA VICTORIA] ep={self._ep_count}"
                            f" vs {rname} | tipo={win_type} | steps={self.num_timesteps:,}"
                        )

        roll = self.n_calls // PPO_HPARAMS["n_steps"]
        if roll > self._last_roll:
            self._last_roll = roll
            self._log(self._last_rival)

        return True

    def _log(self, last_rival: int):
        if not self._ep_wins:
            return

        wr         = np.mean(self._ep_wins) * 100
        avg_len    = np.mean(self._ep_lens)       if self._ep_lens       else 0
        avg_dmg    = np.mean(self._ep_p2dmg)      if self._ep_p2dmg      else 0
        avg_rivals = np.mean(self._ep_rivals_def) if self._ep_rivals_def else 0
        rname      = CHAR_NAMES.get(last_rival, f"ID_{last_rival:02X}")
        total_roll = self._roll_fierce + self._roll_strong + self._roll_jab

        print(f"\n{'─'*65}")
        print(f"  Rollout {self._last_roll} | Steps {self.num_timesteps:,}")
        print(f"  Episodios      : {self._ep_count}")
        print(f"  Win rate       : {wr:.1f}%  (últimos {len(self._ep_wins)})")
        print(f"    └─ Por KO    : {self._ko_wins}  |  Por Tiempo: {self._timeout_wins}")
        print(f"  Avg ep len     : {avg_len:.0f}  | Avg P2 dmg: {avg_dmg:.1f}")
        print(f"  Avg rivales/ep : {avg_rivals:.2f}  | Máx histórico: {self._max_rivals_ep}")
        print(f"  Rolling        : {total_roll} usos "
              f"(F:{self._roll_fierce} S:{self._roll_strong} J:{self._roll_jab})")
        print(f"  FK+Rolling     : {self._fk_rolls}")
        print(f"  Electric       : {self._elec_uses} usos")
        print(f"  Roll-Jump      : {self._rjump_uses} usos")

        # ── bosses ────────────────────────────────────────────────────────
        print(f"  ── BOSSES ─────────────────────────────────────────────")
        for boss_id in _BOSS_ORDER:
            bname  = CHAR_NAMES[boss_id]
            count  = self._boss_eps.get(boss_id, 0)
            pct    = count * 100.0 / max(self._ep_count, 1)
            first  = self._boss_first_ep.get(boss_id)
            marker = "✓" if first else "✗"
            print(
                f"    {marker} {bname:10s}: {count:4d} eps ({pct:5.1f}%)"
                + (f"  [primer ep: {first}]" if first else "")
            )
        print(f"  Max bosses/ep  : {self._max_bosses_ep}")
        print(f"  Bonus stages   : {self._bonus_stage_eps} eps"
              + (f"  [primer ep: {self._bonus_first_ep}]" if self._bonus_first_ep else ""))
        print(f"  Arcade clears  : {self._arcade_clears}"
              + (f"  [primer ep: {self._arcade_first_ep}]" if self._arcade_first_ep else ""))

        if self._rival_eps:
            rivals_str = ", ".join(
                f"{CHAR_NAMES.get(c,'?')}:{n}"
                for c, n in sorted(self._rival_eps.items()))
            print(f"  Rivales vistos : {rivals_str}")
        print(f"  Rival actual   : {rname}")
        print(f"{'─'*65}")

        # ── TensorBoard ───────────────────────────────────────────────────
        self.logger.record("sf2/win_rate",            wr)
        self.logger.record("sf2/ko_wins",             self._ko_wins)
        self.logger.record("sf2/timeout_wins",        self._timeout_wins)
        self.logger.record("sf2/avg_ep_len",          avg_len)
        self.logger.record("sf2/avg_p2_damage",       avg_dmg)
        self.logger.record("sf2/avg_rivals_per_ep",   avg_rivals)
        self.logger.record("sf2/max_rivals_ep",       self._max_rivals_ep)
        self.logger.record("sf2/rolling_uses",        total_roll)
        self.logger.record("sf2/rolling_jump_uses",   self._rjump_uses)
        self.logger.record("sf2/electric_uses",       self._elec_uses)
        self.logger.record("sf2/fk_rolling",          self._fk_rolls)
        self.logger.record("sf2/arcade_clears",       self._arcade_clears)
        self.logger.record("sf2/any_boss_eps",        self._any_boss_eps)
        self.logger.record("sf2/bonus_stage_eps",     self._bonus_stage_eps)

        for boss_id in _BOSS_ORDER:
            key   = _BOSS_KEY[boss_id]
            count = self._boss_eps.get(boss_id, 0)
            self.logger.record(f"sf2/boss_{key}_eps", count)

        self.logger.dump(self.num_timesteps)

        if self._last_roll % 20 == 0:
            self.registry.print_summary()

    def print_final_summary(self):
        print(f"\n{'═'*65}")
        print(f"  RESUMEN FINAL — Fase 1 Multi-Env ({N_ENVS} instancias)")
        print(f"{'═'*65}")
        print(f"  Total episodios : {self._ep_count}")
        print(f"  KO wins         : {self._ko_wins}")
        print(f"  Timeout wins    : {self._timeout_wins}")
        print(f"  Max rivales/ep  : {self._max_rivals_ep}")
        print(f"  Bonus stages    : {self._bonus_stage_eps} eps"
              + (f" (primer ep #{self._bonus_first_ep})" if self._bonus_first_ep else " (ninguno)"))
        print(f"  Arcade clears   : {self._arcade_clears}"
              + (f" (primer ep #{self._arcade_first_ep})" if self._arcade_first_ep else " (ninguno)"))
        print(f"  ── Bosses alcanzados ──")
        for boss_id in _BOSS_ORDER:
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


# ── ENTORNO ───────────────────────────────────────────────────────────────────

def make_env(instance_id: int, registry: RivalRegistry):
    def _init():
        # v3.2: cl1_mode=True → 7 acciones CL-1 (explícito, no depende de la constante global)
        env = BlankaEnv(instance_id=instance_id, max_steps=30000,
                        registry=registry, cl1_mode=True)
        env = Monitor(env, os.path.join(LOGS_DIR, f"monitor_{instance_id}"))
        return env
    return _init


# ── ENTRENAMIENTO ─────────────────────────────────────────────────────────────

def train(resume_path: Optional[str] = None,
          lr: float = 1e-4,
          total_steps: int = 5_000_000,
          visible: bool = False,
          n_envs: int = N_ENVS,
          procs: Optional[List] = None):

    print("\n" + "="*65)
    print(f"  SF2CE — PPO Blanka | Fase 1 Multi-Env ({n_envs} instancias)")
    print("="*65)
    print(f"  Resume   : {resume_path or 'Nuevo modelo'}")
    print(f"  LR       : {lr}")
    print(f"  Steps    : {total_steps:,}")
    print(f"  N_ENVS   : {n_envs}  (SubprocVecEnv)")
    print(f"  Visible  : {'instancia 0' if visible else 'todas headless'}")
    print(f"  Modelos  : {MODELS_DIR}")
    print(f"  Logs     : {LOGS_DIR}")
    print(f"  ent_coef : {PPO_HPARAMS['ent_coef']}  (v3.1: anti-colapso)")
    print(f"  n_steps  : {PPO_HPARAMS['n_steps']}  → rollout={n_envs * PPO_HPARAMS['n_steps']:,}")
    print(f"  Flujo    : episodio termina solo por ARCADE CLEAR o MAX_STEPS")
    print(f"             el Lua pulsa Continue automáticamente al perder")
    print("="*65)

    # ── LANZAR MAMEs ──────────────────────────────────────────────────────────
    if procs is None:
        print(f"\n[0/4] Limpiando archivos previos en dinamicos/...")
        clean_all(n_envs)

        print(f"\n[1/4] Lanzando {n_envs} instancias MAME...")
        procs = launch_all(n_envs, visible_first=visible)
        if not procs:
            print("ERROR: sin instancias activas. Abortando.")
            sys.exit(1)
        print(f"      {len(procs)} MAMEs OK\n")

    # ── REGISTRO ──────────────────────────────────────────────────────────────
    print("[2/4] Cargando registro de rivales...")
    registry = RivalRegistry(STATS_FILE)
    registry.print_summary()

    # ── ENTORNO ───────────────────────────────────────────────────────────────
    print(f"[3/4] Creando {n_envs} entornos SubprocVecEnv...")
    vec_env = SubprocVecEnv(
        [make_env(i, registry) for i in range(n_envs)],
        start_method="spawn",
    )
    is_new = resume_path is None

    if not is_new and os.path.exists(VN_PATH):
        print(f"      Cargando VecNormalize: {VN_PATH}")
        vec_env = VecNormalize.load(VN_PATH, vec_env)
        vec_env.training = True
    else:
        print("      Nuevo VecNormalize")
        vec_env = VecNormalize(
            vec_env, norm_obs=True, norm_reward=True,
            clip_obs=10.0, clip_reward=10.0, gamma=0.99,
        )

    # ── MODELO PPO ────────────────────────────────────────────────────────────
    print("[4/4] Preparando modelo PPO...")
    if not is_new:
        print(f"      Cargando pesos desde: {resume_path}")
        model = PPO.load(
            resume_path, env=vec_env, learning_rate=lr,
            tensorboard_log=LOGS_DIR, device="auto",
        )
        model.learning_rate = lr
        model.lr_schedule   = lambda _: lr
        for pg in model.policy.optimizer.param_groups:
            pg["lr"] = lr
        if model.n_steps != PPO_HPARAMS["n_steps"]:
            print(f"      Actualizando n_steps: {model.n_steps} → {PPO_HPARAMS['n_steps']}")
            model.n_steps    = PPO_HPARAMS["n_steps"]
            model.batch_size = PPO_HPARAMS["batch_size"]
    else:
        model = PPO("MlpPolicy", vec_env, learning_rate=lr, device="auto", **PPO_HPARAMS)

    params = sum(p.numel() for p in model.policy.parameters())
    print(f"      Parámetros: {params:,}\n")

    ckpt_cb    = CheckpointVN(8192, MODELS_DIR, "fase1", VN_PATH)
    metrics_cb = MetricsCallback(registry=registry, verbose=1)

    print(f"Iniciando entrenamiento ({total_steps:,} steps, {n_envs} envs)...")
    print(f"  Rollout efectivo  : {n_envs} × {PPO_HPARAMS['n_steps']} = {n_envs * PPO_HPARAMS['n_steps']:,} steps")
    print(f"  Episodio = run arcade completa hasta ARCADE CLEAR o MAX_STEPS")
    print(f"  Game Over → Lua pulsa Continue → mismo episodio continúa\n")
    print(f"  TensorBoard: tensorboard --logdir {LOGS_DIR}\n")

    t0 = time.time()
    try:
        model.learn(
            total_timesteps     = total_steps,
            callback            = [ckpt_cb, metrics_cb],
            reset_num_timesteps = is_new,
            tb_log_name         = "ppo_blanka_fase1",
        )
    except KeyboardInterrupt:
        print("\n[CTRL+C] Interrumpido. Guardando modelo y stats...")

    # ── GUARDADO FINAL ────────────────────────────────────────────────────────
    elapsed   = time.time() - t0
    final_mdl = os.path.join(MODELS_DIR, "fase1_final")
    model.save(final_mdl)
    print(f"  Modelo  → {final_mdl}.zip")

    vn_cand = vec_env
    for _ in range(5):
        if hasattr(vn_cand, "obs_rms"):
            vn_cand.save(VN_PATH)
            print(f"  VecNorm → {VN_PATH}")
            break
        vn_cand = getattr(vn_cand, "venv", None)
        if vn_cand is None:
            break

    registry.save()
    print(f"  Stats   → {STATS_FILE}")

    print(f"\n{'='*65}")
    print(f"  Entrenamiento finalizado")
    print(f"  Modelo  : {final_mdl}.zip")
    print(f"  Tiempo  : {elapsed/3600:.2f}h")
    print(f"  Steps   : {model.num_timesteps:,}")
    print(f"{'='*65}")

    metrics_cb.print_final_summary()
    registry.print_summary()

    try:
        vec_env.close()
    except Exception:
        pass

    # ── TERMINAR MAMEs ────────────────────────────────────────────────────────
    print("\n[CLEANUP] Terminando instancias MAME...")
    for i, p in enumerate(procs):
        try:
            p.terminate()
            print(f"  MAME-{i} PID={p.pid} terminado")
        except Exception:
            pass

    print(f"\n  Para Fase 2 (arcade completo, 1 env visible):")
    print(f"    python train_blanka_v1.py --resume {final_mdl}")


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=f"SF2CE PPO Blanka — Fase 1 Multi-Env ({N_ENVS} instancias headless)")
    parser.add_argument("--resume", type=str, default=None,
        help="Checkpoint previo (sin .zip)")
    parser.add_argument("--lr", type=float, default=None,
        help="Learning rate (default: 1e-4)")
    parser.add_argument("--steps", type=int, default=5_000_000,
        help="Total steps (default: 5M)")
    parser.add_argument("--envs", type=int, default=N_ENVS,
        help=f"Número de instancias MAME (default {N_ENVS})")
    parser.add_argument("--visible", action="store_true", default=False,
        help="Instancia 0 con ventana visible (las demás siguen headless)")
    parser.add_argument("--stats", action="store_true", default=False,
        help="Mostrar estadísticas de rivales y salir")
    args = parser.parse_args()

    if args.stats:
        RivalRegistry(STATS_FILE).print_summary()
        sys.exit(0)

    # v3.1: lr por defecto siempre 1e-4 (antes era 3e-4 para nuevo, 1e-4 para resume)
    # Con ent_coef=0.05, lr=3e-4 puede causar inestabilidad en los primeros updates.
    lr = args.lr if args.lr is not None else 1e-4
    if args.lr is None:
        print(f"[INFO] lr automático = {lr}  (v3.1: siempre 1e-4)")

    train(
        resume_path = args.resume,
        lr          = lr,
        total_steps = args.steps,
        visible     = args.visible,
        n_envs      = args.envs,
    )
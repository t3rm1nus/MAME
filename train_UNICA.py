#!/usr/bin/env python3
"""
train_UNICA.py — SF2CE PPO Blanka | Fase Única Sin Restricción de Movimientos
==============================================================================
Versión: 1.0 (04/04/2026)

FILOSOFÍA DE DISEÑO:
  · Una sola fase. 26 acciones desde el primer step.
  · Los especiales (rolling, electric) tienen reward shaping preferente,
    pero el agente NO está ciego a saltos, golpes simples ni esquivas.
  · Recompensas explícitas para: esquivar proyectiles, presión en esquina,
    acumulación de stun, ataques aéreos, anti-air y variedad de ataque.
  · Penalización por spam repetitivo de la misma acción sin efecto.
  · Watchdog activo: relanza MAMEs muertos automáticamente.
  · Curriculum implícito vía RivalRegistry: el entorno ya selecciona
    rivales según historial de victorias, sin restricción de acciones.

HIPERPARÁMETROS:
  · ent_coef = 0.08  (entre 0.05 de F1 y 0.16 de F2 — exploración real)
  · n_steps  = 4096  (rollout efectivo = 6 × 4096 = 24576)
  · target_kl = 0.015 (estabilidad sin ser demasiado conservador)

USO:
  python train_UNICA.py
  python train_UNICA.py --visible
  python train_UNICA.py --envs 4
  python train_UNICA.py --resume models/blanka/unica/unica_500000_steps
  python train_UNICA.py --stats
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
    BlankaEnv, CHAR_NAMES, ROLLING_ACTIONS, ACTION_ELECTRIC,
    BOSS_IDS, BOSS_ORDER, ARCADE_FINAL_BOSS, MAX_HP,
)
from core.rival_registry import RivalRegistry

# ── CONFIG ────────────────────────────────────────────────────────────────────
N_ENVS = 6

MAME_DIR   = r"C:\proyectos\MAME"
DYN_DIR    = os.path.join(MAME_DIR, "dinamicos")
MAME_EXE   = r"C:\proyectos\MAME\EMULADOR\mame.exe"
LUA_SCRIPT = r"C:\proyectos\MAME\lua\autoplay_bridge.lua"
MODELS_DIR = os.path.join(MAME_DIR, "models", "blanka", "unica")
LOGS_DIR   = os.path.join(MAME_DIR, "logs",   "blanka", "unica")
VN_PATH    = os.path.join(MODELS_DIR, "vecnorm_unica.pkl")
STATS_FILE = os.path.join(MAME_DIR, "rival_stats.json")
CLAIM_FILE = os.path.join(DYN_DIR, "instance_id_claim.txt")

os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR,   exist_ok=True)
os.makedirs(DYN_DIR,    exist_ok=True)

def ver_file(i):      return os.path.join(DYN_DIR, f"bridge_version_{i}.txt")
def claimed_file(i):  return os.path.join(DYN_DIR, f"instance_id_claimed_{i}.txt")
def input_file(i):    return os.path.join(DYN_DIR, f"mame_input_{i}.txt")
def state_file(i):    return os.path.join(DYN_DIR, f"state_{i}.txt")
def state_tmp(i):     return os.path.join(DYN_DIR, f"state_{i}.tmp")

# IDs de acciones (espacio completo de 26)
ACTION_ROLLING_FIERCE = 15
ACTION_ROLLING_STRONG = 16
ACTION_ROLLING_JAB    = 17
ACTION_ELECTRIC_ID    = 18
ACTION_JUMP_FWD_FIERCE  = 19
ACTION_JUMP_FWD_FORWARD = 20
ACTION_JUMP_FWD_RH      = 21
ACTION_JUMP_NEU_FIERCE  = 22
ACTION_JUMP_BACK_FIERCE = 23
ACTION_JUMP_BACK_FORWARD= 24
ACTION_ROLLING_JUMP     = 25
JUMP_ACTIONS = {19, 20, 21, 22, 23, 24}

# ── HIPERPARÁMETROS PPO ───────────────────────────────────────────────────────
PPO_HPARAMS = dict(
    n_steps       = 4096,   # rollout = 6 × 4096 = 24576 steps
    batch_size    = 256,
    n_epochs      = 10,
    gamma         = 0.99,
    gae_lambda    = 0.95,
    clip_range    = 0.2,
    ent_coef      = 0.08,   # exploración real sin caos
    vf_coef       = 0.5,
    max_grad_norm = 0.5,
    target_kl     = 0.015,
    policy_kwargs = dict(net_arch=[256, 256]),
    verbose       = 1,
    tensorboard_log = LOGS_DIR,
)

_BOSS_ORDER = [10, 11, 9, 8]
_BOSS_KEY   = {10: "balrog", 11: "vega", 9: "sagat", 8: "bison"}


# ── LIMPIEZA ──────────────────────────────────────────────────────────────────

def _try_remove(path: str) -> bool:
    if not os.path.exists(path):
        return False
    try:
        os.remove(path)
        return True
    except Exception:
        return False


def clean_all(n: int):
    targets = [CLAIM_FILE]
    for i in range(n):
        targets += [ver_file(i), claimed_file(i),
                    input_file(i), state_file(i), state_tmp(i)]
    cnt = sum(1 for f in targets if _try_remove(f))
    if cnt:
        print(f"  [CLEAN] {cnt} archivos de sesión anterior eliminados")


# ── LANZAR INSTANCIAS MAME ────────────────────────────────────────────────────

def launch_one(instance_id: int, visible: bool = False) -> Optional[subprocess.Popen]:
    if not os.path.exists(MAME_EXE):
        print(f"[MAME-{instance_id}] ERROR: no existe {MAME_EXE}")
        return None

    for f in [CLAIM_FILE, claimed_file(instance_id), ver_file(instance_id)]:
        _try_remove(f)

    try:
        with open(CLAIM_FILE, "w") as f:
            f.write(str(instance_id))
    except Exception as e:
        print(f"[MAME-{instance_id}] ERROR escribiendo claim: {e}")
        return None

    video = "d3d" if visible else "none"
    cmd = [
        MAME_EXE, "sf2ce",
        "-rompath",         os.path.join(MAME_DIR, "EMULADOR", "roms"),
        "-autoboot_script", LUA_SCRIPT,
        "-skip_gameinfo",
        "-nothrottle",
        "-sound", "none",
        "-console",
        "-video", video,
    ]
    if visible:
        cmd += ["-window", "-nomaximize"]

    try:
        log_path = os.path.join(DYN_DIR, f"mame_stdout_{instance_id}.txt")
        log_f = open(log_path, "w")
        proc = subprocess.Popen(
            cmd, cwd=os.path.dirname(MAME_EXE),
            stdout=log_f, stderr=log_f,
        )
        print(f"[MAME-{instance_id}] PID={proc.pid} {'[VISIBLE]' if visible else '[headless]'}")
    except Exception as e:
        print(f"[MAME-{instance_id}] ERROR Popen: {e}")
        _try_remove(CLAIM_FILE)
        return None

    # Esperar a que el Lua consuma el claim
    cf = claimed_file(instance_id)
    t0 = time.time()
    print(f"[MAME-{instance_id}] Esperando claim...")
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

    # Esperar bridge listo
    vf = ver_file(instance_id)
    t1 = time.time()
    while time.time() - t1 < 60.0:
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

    print(f"[MAME-{instance_id}] WARN: bridge_version no apareció en 60s — continuando")
    return proc


def _wait_for_state_files(procs: List[subprocess.Popen], n: int,
                           timeout: float = 90.0):
    print(f"\n  [WAIT] Esperando state files de {n} instancias (timeout={timeout:.0f}s)...")
    t0        = time.time()
    deadline  = t0 + timeout
    confirmed = [False] * n
    last_log  = t0

    while time.time() < deadline:
        for i in range(n):
            if confirmed[i]:
                continue
            if procs[i].poll() is not None:
                print(f"  [WAIT] ⚠️  MAME-{i} ha muerto inesperadamente")
                confirmed[i] = True
                continue
            sf = state_file(i)
            if os.path.exists(sf):
                try:
                    with open(sf, "r") as f:
                        content = f.read().strip()
                    if content and "in_combat" in content:
                        print(f"  [WAIT] MAME-{i} state de combate listo ({time.time()-t0:.1f}s) ✓")
                        confirmed[i] = True
                except Exception:
                    pass

        if all(confirmed):
            print(f"  [WAIT] Todos los bridges en combate ({time.time()-t0:.1f}s) ✓")
            return

        now = time.time()
        if now - last_log >= 10.0:
            last_log = now
            pending  = [i for i, c in enumerate(confirmed) if not c]
            print(f"  [WAIT] {now-t0:.0f}s — esperando instancias: {pending}")
        time.sleep(0.2)

    not_ready = [i for i, c in enumerate(confirmed) if not c]
    print(f"  [WAIT] ⚠️  Timeout — instancias sin state de combate: {not_ready}")


def launch_all(n: int, visible_first: bool = False) -> List[subprocess.Popen]:
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
    _wait_for_state_files(procs, n, timeout=90.0)
    return procs


# ── WATCHDOG ──────────────────────────────────────────────────────────────────

class MameWatchdogCallback(BaseCallback):
    """Relanza automáticamente MAMEs muertos o colgados."""
    CHECK_EVERY  = 2048
    STALE_SECS   = 30
    MIN_UPTIME_S = 90

    def __init__(self, procs: List[Optional[subprocess.Popen]],
                 n_envs: int, visible_first: bool = False, verbose: int = 1):
        super().__init__(verbose)
        self.procs          = procs
        self.n_envs         = n_envs
        self.visible_first  = visible_first
        self._last_check    = 0
        self._launch_time   = [time.time()] * n_envs

    def _on_step(self) -> bool:
        if self.num_timesteps - self._last_check < self.CHECK_EVERY:
            return True
        self._last_check = self.num_timesteps

        for i in range(self.n_envs):
            proc   = self.procs[i] if i < len(self.procs) else None
            uptime = time.time() - self._launch_time[i]
            if uptime < self.MIN_UPTIME_S:
                continue

            dead, reason = False, ""
            if proc is None or proc.poll() is not None:
                dead   = True
                reason = f"proceso muerto (rc={proc.returncode if proc else 'None'})"

            if not dead:
                sf = state_file(i)
                if os.path.exists(sf):
                    age = time.time() - os.path.getmtime(sf)
                    if age > self.STALE_SECS:
                        dead, reason = True, f"state file sin actualizar {age:.0f}s"
                elif uptime > self.STALE_SECS * 2:
                    dead, reason = True, f"state file no existe tras {uptime:.0f}s"

            if not dead:
                continue

            print(f"\n[WATCHDOG] ⚠️  MAME-{i} caído ({reason}) → relanzando...")
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass

            for fname in [f"state_{i}.txt", f"state_{i}.tmp",
                          f"mame_input_{i}.txt", f"bridge_version_{i}.txt",
                          f"instance_id_claimed_{i}.txt"]:
                _try_remove(os.path.join(DYN_DIR, fname))

            time.sleep(2.0)
            vis = self.visible_first and (i == 0)
            new_proc = launch_one(i, visible=vis)
            while len(self.procs) <= i:
                self.procs.append(None)
            self.procs[i] = new_proc
            self._launch_time[i] = time.time()
            status = f"✅ PID={new_proc.pid}" if new_proc else "❌ falló — reintento próximo check"
            print(f"[WATCHDOG] MAME-{i} {status}")

        return True


# ── CHECKPOINT ────────────────────────────────────────────────────────────────

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


# ── MÉTRICAS ──────────────────────────────────────────────────────────────────

class MetricsCallback(BaseCallback):
    """
    Métricas completas para la fase única.
    Trackea: especiales, saltos, golpes simples, esquivas, KOs, timeouts,
    bosses y arcade clears.
    """

    def __init__(self, registry: RivalRegistry, verbose: int = 1):
        super().__init__(verbose)
        self.registry = registry

        self._ep_wins       = deque(maxlen=100)
        self._ep_lens       = deque(maxlen=100)
        self._ep_p2dmg      = deque(maxlen=100)
        self._ep_rivals_def = deque(maxlen=100)
        self._ep_count      = 0

        # Uso de movimientos
        self._roll_fierce   = 0
        self._roll_strong   = 0
        self._roll_jab      = 0
        self._elec_uses     = 0
        self._rjump_uses    = 0
        self._jump_uses     = 0   # saltos normales (19-24)
        self._simple_atk    = 0   # golpes simples (5-14)
        self._fk_rolls      = 0

        # Resultados
        self._ko_wins       = 0
        self._timeout_wins  = 0
        self._first_win     = False
        self._last_roll     = -1
        self._last_rival    = 0xFF

        # Progresión
        self._max_rivals_ep  = 0
        self._rival_eps: dict = {}
        self._boss_eps: dict  = {bid: 0 for bid in _BOSS_ORDER}
        self._boss_first_ep: dict = {}
        self._any_boss_eps       = 0
        self._max_bosses_ep      = 0
        self._arcade_clears      = 0
        self._arcade_first_ep: Optional[int] = None
        self._bonus_stage_eps    = 0
        self._bonus_first_ep: Optional[int] = None

        # Métricas de diversidad de ataque
        self._action_counts  = {}  # acción → veces usada en el rollout
        self._action_dmg     = {}  # acción → daño total causado

    def _on_step(self) -> bool:
        infos = self.locals.get("infos", [{}])

        for info in infos:
            action   = info.get("action_real", info.get("action", -1))
            fk_land  = info.get("fk_land", 0)
            rival    = info.get("rival", 0xFF)
            if rival <= 11:
                self._last_rival = rival

            # Conteo por categoría de movimiento
            if action == ACTION_ROLLING_FIERCE:
                self._roll_fierce += 1
            elif action == ACTION_ROLLING_STRONG:
                self._roll_strong += 1
            elif action == ACTION_ROLLING_JAB:
                self._roll_jab    += 1
            elif action == ACTION_ELECTRIC_ID:
                self._elec_uses   += 1
            elif action == ACTION_ROLLING_JUMP:
                self._rjump_uses  += 1
            elif action in JUMP_ACTIONS:
                self._jump_uses   += 1
            elif 5 <= action <= 14:
                self._simple_atk  += 1

            if action in ROLLING_ACTIONS and 0 < fk_land <= 20:
                self._fk_rolls += 1

            # Acumulación de diversidad
            self._action_counts[action] = self._action_counts.get(action, 0) + 1

            if "episode" in info:
                ep          = info["episode"]
                won         = info.get("won", False)
                t_w         = info.get("timeout_win", False)
                rivals_def  = info.get("rivals_defeated", 0)
                reached_bon = info.get("reached_bonus", False)
                bosses_ids  = info.get("bosses_reached_ids", [])
                bosses_count= info.get("bosses_reached_count", 0)
                arcade_clr  = info.get("arcade_cleared", False)

                rival_hp_f  = float(info.get("rival_hp", info.get("p2_hp", MAX_HP)))
                ep_p2_dmg   = max(0.0, MAX_HP - rival_hp_f)

                self._ep_count += 1
                self._ep_wins.append(1 if won else 0)
                self._ep_lens.append(ep.get("l", 0))
                self._ep_p2dmg.append(ep_p2_dmg)
                self._ep_rivals_def.append(rivals_def)

                if rival <= 11:
                    self._rival_eps[rival] = self._rival_eps.get(rival, 0) + 1

                if rivals_def > self._max_rivals_ep:
                    self._max_rivals_ep = rivals_def
                    rname = CHAR_NAMES.get(rival, f"ID_{rival}")
                    print(f"\n[🏆 RÉCORD] {rivals_def} rivales | último: {rname} | steps={self.num_timesteps:,}")

                if reached_bon:
                    self._bonus_stage_eps += 1
                    if self._bonus_first_ep is None:
                        self._bonus_first_ep = self._ep_count
                        print(f"\n[⭐ PRIMER BONUS STAGE] ep={self._ep_count} | steps={self.num_timesteps:,}")

                if bosses_count > 0:
                    self._any_boss_eps += 1
                    if bosses_count > self._max_bosses_ep:
                        self._max_bosses_ep = bosses_count
                        bnames = [CHAR_NAMES.get(b, f"ID_{b}") for b in bosses_ids]
                        print(f"\n[🏆 RÉCORD BOSSES] {bosses_count} bosses | {bnames} | steps={self.num_timesteps:,}")
                    for boss_id in bosses_ids:
                        self._boss_eps[boss_id] = self._boss_eps.get(boss_id, 0) + 1
                        if boss_id not in self._boss_first_ep:
                            self._boss_first_ep[boss_id] = self._ep_count
                            bname = CHAR_NAMES.get(boss_id, f"ID_{boss_id}")
                            print(f"\n[⚔️  PRIMER BOSS: {bname}] ep={self._ep_count} | steps={self.num_timesteps:,}")

                if arcade_clr:
                    self._arcade_clears += 1
                    if self._arcade_first_ep is None:
                        self._arcade_first_ep = self._ep_count
                        print(f"\n[🎮 ¡¡ARCADE CLEAR!! #{self._arcade_clears}] ep={self._ep_count} | steps={self.num_timesteps:,}")
                    else:
                        print(f"\n[🎮 ARCADE CLEAR #{self._arcade_clears}] ep={self._ep_count} | steps={self.num_timesteps:,}")

                if won:
                    if t_w: self._timeout_wins += 1
                    else:   self._ko_wins      += 1
                    if not self._first_win:
                        self._first_win = True
                        rname    = CHAR_NAMES.get(rival, f"ID_{rival}")
                        win_type = "TIEMPO" if t_w else "KO"
                        print(f"\n[🏆 PRIMERA VICTORIA] ep={self._ep_count} vs {rname} | {win_type} | steps={self.num_timesteps:,}")

        roll = self.n_calls // PPO_HPARAMS["n_steps"]
        if roll > self._last_roll:
            self._last_roll = roll
            self._log(self._last_rival)

        return True

    def _action_diversity_score(self) -> float:
        """
        Entropía del uso de acciones en el rollout: 0=mono-acción, 1=uniforme.
        Útil para detectar si el agente está colapsando a spam de una sola acción.
        """
        if not self._action_counts:
            return 0.0
        total  = sum(self._action_counts.values())
        probs  = [v / total for v in self._action_counts.values()]
        entropy = -sum(p * np.log(p + 1e-9) for p in probs)
        max_ent = np.log(len(self._action_counts) + 1e-9)
        return float(entropy / max_ent) if max_ent > 0 else 0.0

    def _log(self, last_rival: int):
        if not self._ep_wins:
            return

        wr          = np.mean(self._ep_wins) * 100
        avg_len     = np.mean(self._ep_lens)       if self._ep_lens       else 0
        avg_dmg     = np.mean(self._ep_p2dmg)      if self._ep_p2dmg      else 0
        avg_rivals  = np.mean(self._ep_rivals_def) if self._ep_rivals_def else 0
        rname       = CHAR_NAMES.get(last_rival, f"ID_{last_rival:02X}")
        total_roll  = self._roll_fierce + self._roll_strong + self._roll_jab
        diversity   = self._action_diversity_score()

        print(f"\n{'─'*65}")
        print(f"  Rollout {self._last_roll} | Steps {self.num_timesteps:,}")
        print(f"  Episodios      : {self._ep_count}")
        print(f"  Win rate       : {wr:.1f}%  (últimos {len(self._ep_wins)})")
        print(f"    └─ Por KO    : {self._ko_wins}  |  Por Tiempo: {self._timeout_wins}")
        print(f"  Avg ep len     : {avg_len:.0f}  | Avg P2 dmg: {avg_dmg:.1f}")
        print(f"  Avg rivales/ep : {avg_rivals:.2f}  | Máx histórico: {self._max_rivals_ep}")
        print(f"  ── Uso de movimientos ─────────────────────────────────")
        print(f"  Rolling        : {total_roll} "
              f"(F:{self._roll_fierce} S:{self._roll_strong} J:{self._roll_jab})")
        print(f"  FK+Rolling     : {self._fk_rolls}")
        print(f"  Electric       : {self._elec_uses}")
        print(f"  Roll-Jump      : {self._rjump_uses}")
        print(f"  Saltos (19-24) : {self._jump_uses}")
        print(f"  Golpes simples : {self._simple_atk}")
        print(f"  Diversidad     : {diversity:.3f}  (0=spam, 1=uniforme)")
        print(f"  ── Bosses ─────────────────────────────────────────────")
        for boss_id in _BOSS_ORDER:
            bname  = CHAR_NAMES[boss_id]
            count  = self._boss_eps.get(boss_id, 0)
            pct    = count * 100.0 / max(self._ep_count, 1)
            first  = self._boss_first_ep.get(boss_id)
            marker = "✓" if first else "✗"
            print(f"    {marker} {bname:10s}: {count:4d} eps ({pct:5.1f}%)"
                  + (f"  [primer ep: {first}]" if first else ""))
        print(f"  Max bosses/ep  : {self._max_bosses_ep}")
        print(f"  Bonus stages   : {self._bonus_stage_eps}"
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
        self.logger.record("sf2/jump_uses",           self._jump_uses)
        self.logger.record("sf2/simple_atk_uses",     self._simple_atk)
        self.logger.record("sf2/fk_rolling",          self._fk_rolls)
        self.logger.record("sf2/action_diversity",    diversity)
        self.logger.record("sf2/arcade_clears",       self._arcade_clears)
        self.logger.record("sf2/any_boss_eps",        self._any_boss_eps)
        self.logger.record("sf2/bonus_stage_eps",     self._bonus_stage_eps)
        for boss_id in _BOSS_ORDER:
            key   = _BOSS_KEY[boss_id]
            count = self._boss_eps.get(boss_id, 0)
            self.logger.record(f"sf2/boss_{key}_eps", count)
        self.logger.dump(self.num_timesteps)

        # Reset contadores de diversidad para el próximo rollout
        self._action_counts.clear()
        self._action_dmg.clear()

        if self._last_roll % 20 == 0:
            self.registry.print_summary()

    def print_final_summary(self):
        print(f"\n{'═'*65}")
        print(f"  RESUMEN FINAL — Fase Única ({N_ENVS} instancias, 26 acciones)")
        print(f"{'═'*65}")
        print(f"  Total episodios : {self._ep_count}")
        print(f"  KO wins         : {self._ko_wins}")
        print(f"  Timeout wins    : {self._timeout_wins}")
        print(f"  Max rivales/ep  : {self._max_rivals_ep}")
        print(f"  Bonus stages    : {self._bonus_stage_eps}" +
              (f" (primer ep #{self._bonus_first_ep})" if self._bonus_first_ep else " (ninguno)"))
        print(f"  Arcade clears   : {self._arcade_clears}" +
              (f" (primer ep #{self._arcade_first_ep})" if self._arcade_first_ep else " (ninguno)"))
        for boss_id in _BOSS_ORDER:
            bname  = CHAR_NAMES[boss_id]
            count  = self._boss_eps.get(boss_id, 0)
            pct    = count * 100.0 / max(self._ep_count, 1)
            first  = self._boss_first_ep.get(boss_id)
            marker = "✓" if first else "✗"
            print(f"    {marker} {bname:10s}: {count:4d} eps ({pct:5.1f}%)" +
                  (f"  [primer ep: {first}]" if first else "  [NUNCA ALCANZADO]"))
        print(f"{'═'*65}")


# ── ENTORNO ───────────────────────────────────────────────────────────────────

def make_env(instance_id: int, registry: RivalRegistry):
    def _init():
        env = BlankaEnv(
            instance_id=instance_id,
            max_steps=50_000,        # un match completo tiene margen
            registry=registry,
            cl1_mode=False,          # 26 acciones siempre
        )
        env = Monitor(env, os.path.join(LOGS_DIR, f"monitor_{instance_id}"))
        return env
    return _init


# ── ENTRENAMIENTO ─────────────────────────────────────────────────────────────

def train(resume_path: Optional[str] = None,
          lr: float = 1e-4,
          total_steps: int = 10_000_000,
          visible: bool = False,
          n_envs: int = N_ENVS):

    print("\n" + "="*65)
    print(f"  SF2CE — PPO Blanka | Fase Única — 26 acciones")
    print("="*65)
    print(f"  Resume   : {resume_path or 'Nuevo modelo'}")
    print(f"  LR       : {lr}")
    print(f"  Steps    : {total_steps:,}")
    print(f"  N_ENVS   : {n_envs}  (SubprocVecEnv)")
    print(f"  Visible  : {'instancia 0' if visible else 'todas headless'}")
    print(f"  cl1_mode : FALSE — 26 acciones sin restricción")
    print(f"  ent_coef : {PPO_HPARAMS['ent_coef']}  (exploración real)")
    print(f"  n_steps  : {PPO_HPARAMS['n_steps']}  → rollout={n_envs*PPO_HPARAMS['n_steps']:,}")
    print(f"  Modelos  : {MODELS_DIR}")
    print(f"  Logs     : {LOGS_DIR}")
    print("="*65)

    print(f"\n[0/5] Limpiando archivos previos...")
    clean_all(n_envs)

    print(f"\n[1/5] Lanzando {n_envs} instancias MAME...")
    procs = launch_all(n_envs, visible_first=visible)
    if not procs:
        print("ERROR: sin instancias activas. Abortando.")
        sys.exit(1)
    print(f"      {len(procs)} MAMEs OK")

    print(f"\n[2/5] Cargando registro de rivales...")
    registry = RivalRegistry(STATS_FILE)
    registry.print_summary()

    print(f"\n[3/5] Creando {n_envs} entornos SubprocVecEnv...")
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

    print(f"\n[4/5] Preparando modelo PPO...")
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
            model.n_steps    = PPO_HPARAMS["n_steps"]
            model.batch_size = PPO_HPARAMS["batch_size"]
    else:
        model = PPO("MlpPolicy", vec_env, learning_rate=lr,
                    device="auto", **PPO_HPARAMS)

    params = sum(p.numel() for p in model.policy.parameters())
    print(f"      Parámetros: {params:,}")

    ckpt_cb     = CheckpointVN(75_000, MODELS_DIR, "unica", VN_PATH)
    metrics_cb  = MetricsCallback(registry=registry, verbose=1)
    watchdog_cb = MameWatchdogCallback(procs, n_envs, visible_first=visible)

    print(f"\n[5/5] Iniciando entrenamiento ({total_steps:,} steps, {n_envs} envs)...")
    print(f"  Rollout efectivo  : {n_envs} × {PPO_HPARAMS['n_steps']} = {n_envs*PPO_HPARAMS['n_steps']:,}")
    print(f"  TensorBoard: tensorboard --logdir {LOGS_DIR}\n")

    t0 = time.time()
    try:
        model.learn(
            total_timesteps     = total_steps,
            callback            = [ckpt_cb, metrics_cb, watchdog_cb],
            reset_num_timesteps = is_new,
            tb_log_name         = "ppo_blanka_unica",
        )
    except KeyboardInterrupt:
        print("\n[CTRL+C] Interrumpido. Guardando modelo y stats...")

    elapsed   = time.time() - t0
    final_mdl = os.path.join(MODELS_DIR, "unica_final")
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
    print(f"  Tiempo  : {elapsed/3600:.2f}h  |  Steps: {model.num_timesteps:,}")
    print(f"{'='*65}")

    metrics_cb.print_final_summary()
    registry.print_summary()

    try:
        vec_env.close()
    except Exception:
        pass

    print("\n[CLEANUP] Terminando instancias MAME...")
    for i, p in enumerate(procs):
        try:
            p.terminate()
            print(f"  MAME-{i} PID={p.pid} terminado")
        except Exception:
            pass

    print(f"\n  Para continuar: python train_UNICA.py --resume {final_mdl}")


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="SF2CE PPO Blanka — Fase Única 26 acciones sin restricción")
    parser.add_argument("--resume",  type=str,   default=None)
    parser.add_argument("--lr",      type=float, default=None)
    parser.add_argument("--steps",   type=int,   default=10_000_000)
    parser.add_argument("--envs",    type=int,   default=N_ENVS)
    parser.add_argument("--visible", action="store_true", default=False)
    parser.add_argument("--stats",   action="store_true", default=False)
    args = parser.parse_args()

    if args.stats:
        RivalRegistry(STATS_FILE).print_summary()
        sys.exit(0)

    lr = args.lr if args.lr is not None else 1e-4
    if args.lr is None:
        print(f"[INFO] lr automático = {lr}")

    train(
        resume_path = args.resume,
        lr          = lr,
        total_steps = args.steps,
        visible     = args.visible,
        n_envs      = args.envs,
    )
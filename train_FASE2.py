#!/usr/bin/env python3
"""
train_FASE2.py — SF2CE PPO Blanka | Fase 2 Arcade Completo
===========================================================
Versión: 2.2 (02/04/2026)

CAMBIOS v2.2 — FIX CRÍTICO: Race condition en Claim Protocol

  PROBLEMA RAÍZ:
  ──────────────
  launch_all() lanzaba las N instancias con solo time.sleep(1.0) entre
  ellas, pero el Lua tarda hasta 600 frames (~10s a 60fps) en leer el
  claim file. Como CLAIM_FILE es un único archivo compartido, la instancia
  N sobreescribía el claim antes de que el Lua de la instancia N-1 lo
  consumiera. Resultado: múltiples instancias Lua caían en el fallback
  ID=0 y escribían todas sobre state_0.txt.

  A continuación, SubprocVecEnv(start_method="spawn") llama reset() en
  todos los subprocesos simultáneamente al arrancar. Cada reset() busca
  in_combat=True leyendo state_N.txt, pero durante el arranque todos los
  Lua están en BOOTING/INSERT_COIN (~10-15s reales), así que in_combat es
  siempre false. Con state_0.txt corrupto (múltiples escritores), la
  condición nunca se cumple → los 6 envs se bloquean 120s cada uno →
  deadlock total.

  FIX (v2.2):
  ───────────
  launch_one() ahora espera activamente a que aparezca el archivo
  instance_id_claimed_N.txt (señal de que el Lua ya consumió el claim y
  se auto-asignó ese ID) ANTES de retornar. Esto garantiza que cuando
  se lanza la instancia N+1 y se escribe un nuevo CLAIM_FILE, el Lua de
  la instancia N ya terminó de leerlo. Serialización completa del
  protocolo de claim.

  Tiempo de espera máximo: 25s (suficiente para el fallback de 600 frames
  a 60fps = ~10s + margen). Si se agota, se usa el timeout anterior como
  fallback.

CAMBIOS v2.1 (mantenidos):
  · MameWatchdogCallback: relanza MAMEs muertos automáticamente.
  · Checkpoint cada 50k steps.
  · Uso de info["action_real"] en MetricsCallback.
  · max_steps=999_999_999 y n_steps=8192 para episodios largos.
"""

import os
import time
import subprocess
import argparse
from collections import deque
from typing import Optional, List

import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import SubprocVecEnv, VecNormalize
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.save_util import load_from_zip_file

from env.blanka_env import BlankaEnv, CHAR_NAMES, ROLLING_ACTIONS, ACTION_ELECTRIC, BOSS_IDS
from core.rival_registry import RivalRegistry

# --- RUTAS ---
BASE_DIR = r"C:\proyectos\MAME"
MAME_EXE = os.path.join(BASE_DIR, r"EMULADOR\mame.exe")
MAME_ROM = "sf2ce"
MAME_LUASC = os.path.join(BASE_DIR, r"lua\autoplay_bridge.lua")
MODELS_DIR = os.path.join(BASE_DIR, r"models\blanka\fase2")
LOGS_DIR = os.path.join(BASE_DIR, "logs", "blanka", "fase2")
DYN_DIR = os.path.join(BASE_DIR, "dinamicos")
CLAIM_FILE = os.path.join(DYN_DIR, "instance_id_claim.txt")
VN_PATH = os.path.join(MODELS_DIR, "vecnorm_fase2.pkl")
STATS_FILE = os.path.join(BASE_DIR, "rival_stats.json")

os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR,   exist_ok=True)
os.makedirs(DYN_DIR,    exist_ok=True)

# ── HIPERPARÁMETROS PPO — Fase 2 ─────────────────────────────────────────────
PPO_HPARAMS = dict(
    n_steps       = 8192,   # Aumentado para episodios largos sin truncation
    batch_size    = 256,
    n_epochs      = 10,
    gamma         = 0.99,
    gae_lambda    = 0.95,
    clip_range    = 0.20,
    ent_coef      = 0.16,
    vf_coef       = 0.5,
    max_grad_norm = 0.5,
    target_kl     = 0.02,
    policy_kwargs = dict(net_arch=[256, 256]),
    verbose       = 1,
    tensorboard_log = LOGS_DIR,
)

_BOSS_ORDER = [10, 11, 9, 8]
_BOSS_KEY   = {10: "balrog", 11: "vega", 9: "sagat", 8: "bison"}

ACTION_ROLLING_FIERCE = 15
ACTION_ROLLING_STRONG = 16
ACTION_ROLLING_JAB    = 17
ACTION_ELECTRIC_ID    = 18


# ── FUNCIONES DE LANZAMIENTO ──────────────────────────────────────────────────

def _try_remove(path: str):
    if not os.path.exists(path): return False
    try: os.remove(path); return True
    except: return False

def clean_all(n: int):
    targets = [CLAIM_FILE]
    for i in range(n):
        targets += [os.path.join(DYN_DIR, f"bridge_version_{i}.txt"),
                    os.path.join(DYN_DIR, f"instance_id_claimed_{i}.txt"),
                    os.path.join(DYN_DIR, f"mame_input_{i}.txt"),
                    os.path.join(DYN_DIR, f"state_{i}.txt"),
                    os.path.join(DYN_DIR, f"state_{i}.tmp")]
    cnt = sum(1 for f in targets if _try_remove(f))
    if cnt: print(f"  [CLEAN] {cnt} archivos de sesión anterior eliminados")

def launch_one(instance_id: int, visible: bool = False) -> Optional[subprocess.Popen]:
    """
    Lanza una instancia MAME y espera a que el Lua consuma el claim y esté listo.

    FIX v2.2: Antes de retornar, espera activamente a que aparezca
    instance_id_claimed_N.txt — señal de que el Lua ya leyó el claim file
    y se auto-asignó ese ID. Esto garantiza serialización correcta del
    protocolo de claim entre instancias consecutivas.
    """
    for f in [CLAIM_FILE,
              os.path.join(DYN_DIR, f"instance_id_claimed_{instance_id}.txt"),
              os.path.join(DYN_DIR, f"bridge_version_{instance_id}.txt")]:
        _try_remove(f)

    try:
        with open(CLAIM_FILE, "w") as f:
            f.write(str(instance_id))
    except Exception as e:
        print(f"[MAME-{instance_id}] ERROR escribiendo claim: {e}")
        return None

    video = "d3d" if visible else "none"
    cmd = [
        MAME_EXE, MAME_ROM,
        "-rompath",         os.path.join(BASE_DIR, "EMULADOR", "roms"),
        "-autoboot_script", MAME_LUASC,
        "-skip_gameinfo",
        "-console",
        "-sound", "none",
        "-video", video,
    ]
    if not visible:
        cmd += ["-nothrottle"]
    else:
        cmd += ["-window", "-nomaximize"]

    try:
        proc = subprocess.Popen(cmd, cwd=os.path.dirname(MAME_EXE),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[MAME-{instance_id}] ERROR Popen: {e}")
        _try_remove(CLAIM_FILE)
        return None

    print(f"[MAME-{instance_id}] PID={proc.pid} {'[VISIBLE]' if visible else '[headless]'}")

    # ── v2.2 FIX: Esperar a que el Lua consuma el claim (serialización real) ──
    # El Lua renombra CLAIM_FILE a instance_id_claimed_N.txt al leerlo.
    # Solo cuando ese archivo exista podemos saber que el Lua tiene su ID
    # asignado y que CLAIM_FILE ya está libre para la siguiente instancia.
    cf = os.path.join(DYN_DIR, f"instance_id_claimed_{instance_id}.txt")
    t0 = time.time()
    claim_consumed = False
    print(f"[MAME-{instance_id}] Esperando que Lua consuma claim (ID={instance_id})...")
    while time.time() - t0 < 25.0:
        if proc.poll() is not None:
            print(f"[MAME-{instance_id}] ERROR: proceso muerto antes de reclamar ID")
            return None
        if os.path.exists(cf):
            claim_consumed = True
            print(f"[MAME-{instance_id}] Claim consumido ✓ ({time.time()-t0:.1f}s)")
            break
        # También salir si CLAIM_FILE desapareció (Lua lo movió)
        if not os.path.exists(CLAIM_FILE):
            claim_consumed = True
            print(f"[MAME-{instance_id}] Claim consumido (via rename) ✓ ({time.time()-t0:.1f}s)")
            break
        time.sleep(0.2)

    if not claim_consumed:
        print(f"[MAME-{instance_id}] WARN: Lua no consumió el claim en 25s — "
              f"puede haber colisión de IDs. Continuando de todas formas.")

    # Esperar bridge listo (archivo bridge_version_N.txt creado por el Lua)
    vf = os.path.join(DYN_DIR, f"bridge_version_{instance_id}.txt")
    t1 = time.time()
    while time.time() - t1 < 60.0:
        if proc.poll() is not None:
            print(f"[MAME-{instance_id}] ERROR: proceso muerto esperando bridge")
            return None
        if os.path.exists(vf):
            print(f"[MAME-{instance_id}] Bridge listo ✓  ({time.time()-t1:.1f}s)")
            return proc
        time.sleep(0.3)

    print(f"[MAME-{instance_id}] WARN: bridge no apareció en 60s — continuando")
    return proc

def launch_all(n: int, visible_first: bool = False) -> List[Optional[subprocess.Popen]]:
    """
    Lanza N instancias MAME de forma serializada.

    v2.2: Ya no hay time.sleep(1.0) entre instancias porque launch_one()
    ahora espera a que el Lua consuma el claim antes de retornar. Esto
    garantiza que no haya colisiones de claim entre instancias consecutivas.
    """
    procs: List[Optional[subprocess.Popen]] = []
    for i in range(n):
        vis = visible_first and (i == 0)
        print(f"\n  ── Instancia {i} {'[VISIBLE]' if vis else '[headless]'} ──")
        p = launch_one(i, visible=vis)
        procs.append(p)   # None si falló — watchdog lo relanzará
        # Sin sleep adicional: launch_one() ya esperó el handshake del claim
    time.sleep(2.0)  # Breve pausa final para que todos los bridges estabilicen
    activos = sum(1 for p in procs if p is not None)
    print(f"\n[LAUNCH] {activos}/{n} instancias activas")
    return procs


# ── CALLBACKS ─────────────────────────────────────────────────────────────────

class CheckpointVN(BaseCallback):
    """Guarda modelo + VecNormalize cada save_freq steps."""
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
            if env is None: return


class MameWatchdogCallback(BaseCallback):
    """
    Monitorea los procesos MAME y relanza automáticamente los que mueren.

    Dos criterios de muerte:
      (a) proc.poll() is not None → el proceso OS ha terminado
      (b) state_{i}.txt sin modificar >30s → MAME colgado (bridge bloqueado)
    """
    CHECK_EVERY  = 2048
    STALE_SECS   = 30
    MIN_UPTIME_S = 90

    def __init__(self, procs: List[Optional[subprocess.Popen]],
                 n_envs: int, visible_first: bool = False, verbose: int = 1):
        super().__init__(verbose)
        self.procs         = procs
        self.n_envs        = n_envs
        self.visible_first = visible_first
        self._last_check   = 0
        self._launch_time: List[float] = [time.time()] * n_envs

    def _on_step(self) -> bool:
        if self.num_timesteps - self._last_check < self.CHECK_EVERY:
            return True
        self._last_check = self.num_timesteps

        for i in range(self.n_envs):
            proc = self.procs[i] if i < len(self.procs) else None

            uptime = time.time() - self._launch_time[i]
            if uptime < self.MIN_UPTIME_S:
                continue

            dead   = False
            reason = ""

            if proc is None or proc.poll() is not None:
                dead   = True
                code   = proc.returncode if proc else "None"
                reason = f"proceso muerto (returncode={code})"

            if not dead:
                sf = os.path.join(DYN_DIR, f"state_{i}.txt")
                if os.path.exists(sf):
                    age = time.time() - os.path.getmtime(sf)
                    if age > self.STALE_SECS:
                        dead   = True
                        reason = f"state file sin actualizar {age:.0f}s"
                elif uptime > self.STALE_SECS * 2:
                    dead   = True
                    reason = f"state file no existe tras {uptime:.0f}s"

            if not dead:
                continue

            print(f"\n[WATCHDOG] ⚠️  MAME-{i} caído ({reason}) → relanzando...")

            if proc is not None:
                try: proc.kill()
                except Exception: pass

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

            if new_proc:
                print(f"[WATCHDOG] ✅ MAME-{i} relanzado (PID={new_proc.pid})")
            else:
                print(f"[WATCHDOG] ❌ MAME-{i} falló al relanzar — se reintentará en el próximo check")

        return True


class MetricsCallback(BaseCallback):
    def __init__(self, registry: RivalRegistry, verbose: int = 1):
        super().__init__(verbose)
        self.registry       = registry
        self._ep_wins       = deque(maxlen=100)
        self._ep_lens       = deque(maxlen=100)
        self._ep_p2dmg      = deque(maxlen=100)
        self._ep_rivals_def = deque(maxlen=100)
        self._ep_count      = 0
        self._roll_uses     = 0
        self._elec_uses     = 0
        self._fk_rolls      = 0
        self._ko_wins       = 0
        self._timeout_wins  = 0
        self._arcade_clears = 0
        self._arcade_first_ep = None
        self._any_boss_eps  = 0
        self._boss_eps: dict = {}
        self._boss_first_ep: dict = {}
        self._max_rivals_ep = 0
        self._last_rival    = 0xFF
        self._last_roll     = -1
        self._first_win     = False
        self._total_round_wins = 0
        self._total_match_wins = 0

    def _on_step(self) -> bool:
        infos = self.locals.get("infos", [])
        for info in infos:
            if not isinstance(info, dict): continue

            ra = info.get("action_real", info.get("action", -1))

            if ra in (ACTION_ROLLING_FIERCE, ACTION_ROLLING_STRONG, ACTION_ROLLING_JAB):
                if info.get("macro_just_started", True):
                    self._roll_uses += 1
                if info.get("fk_land", 0) > 0 and info.get("macro_just_started", True):
                    self._fk_rolls += 1

            if ra == ACTION_ELECTRIC_ID and info.get("macro_just_started", True):
                self._elec_uses += 1

            rival = info.get("rival", 0xFF)
            if rival <= 11:
                self._last_rival = rival

            ep_done = ("episode" in info or
                       info.get("terminated", False) or
                       info.get("truncated", False))
            if not ep_done: continue

            self._ep_count += 1
            won          = bool(info.get("won", False))
            t_w          = bool(info.get("timeout_win", False))
            ep_len       = info.get("step", info.get("episode", {}).get("l", 0))
            p2dmg        = info.get("ep_p2_dmg", 0)
            rivals_def   = info.get("rivals_defeated", 0)
            bosses_ids   = info.get("bosses_reached_ids", [])
            bosses_count = info.get("bosses_reached_count", 0)
            arcade_clear = info.get("arcade_cleared", False)

            self._ep_wins.append(float(won))
            self._ep_lens.append(ep_len)
            self._ep_p2dmg.append(p2dmg)
            self._ep_rivals_def.append(rivals_def)

            self._total_round_wins += info.get("ep_round_wins", 0)
            self._total_match_wins += info.get("ep_match_wins", 0)

            if won:
                if t_w: self._timeout_wins += 1
                else:   self._ko_wins      += 1
                if not self._first_win:
                    self._first_win = True
                    rname = CHAR_NAMES.get(rival, f"ID_{rival}")
                    wtype = "TIEMPO" if t_w else "KO"
                    print(f"\n[🏆 PRIMERA VICTORIA F2] ep={self._ep_count} vs {rname} | {wtype} | steps={self.num_timesteps:,}")

            if rivals_def > self._max_rivals_ep:
                self._max_rivals_ep = rivals_def
                rname = CHAR_NAMES.get(rival, f"ID_{rival}")
                print(f"\n[🏆 RÉCORD] {rivals_def} rivales | último: {rname} | steps={self.num_timesteps:,}")

            if bosses_count > 0:
                self._any_boss_eps += 1
                for bid in bosses_ids:
                    self._boss_eps[bid] = self._boss_eps.get(bid, 0) + 1
                    if bid not in self._boss_first_ep:
                        self._boss_first_ep[bid] = self._ep_count
                        bname = CHAR_NAMES.get(bid, f"ID_{bid}")
                        print(f"\n[⚔️  PRIMER BOSS F2: {bname}] ep={self._ep_count} | steps={self.num_timesteps:,}")

            if arcade_clear:
                self._arcade_clears += 1
                if self._arcade_first_ep is None:
                    self._arcade_first_ep = self._ep_count
                    print(f"\n[🎮 ¡¡ARCADE CLEAR F2!! #{self._arcade_clears}] ep={self._ep_count} | steps={self.num_timesteps:,}")

        roll = self.n_calls // PPO_HPARAMS["n_steps"]
        if roll > self._last_roll:
            self._last_roll = roll
            self._log_rollout()

        return True

    def _log_rollout(self):
        if not self._ep_wins: return
        wr         = np.mean(self._ep_wins) * 100
        avg_len    = np.mean(self._ep_lens)       if self._ep_lens       else 0
        avg_dmg    = np.mean(self._ep_p2dmg)      if self._ep_p2dmg      else 0
        avg_rivals = np.mean(self._ep_rivals_def) if self._ep_rivals_def else 0
        rname      = CHAR_NAMES.get(self._last_rival, f"ID_{self._last_rival:02X}")

        print(f"\n{'─'*65}")
        print(f"  [F2] Rollout {self._last_roll} | Steps {self.num_timesteps:,}")
        print(f"  Episodios   : {self._ep_count}")
        print(f"  Win rate    : {wr:.1f}%  (últimos {len(self._ep_wins)})")
        print(f"    KO wins   : {self._ko_wins}  | Timeout wins: {self._timeout_wins}")
        print(f"    Round wins: {self._total_round_wins} | Match wins: {self._total_match_wins}")
        print(f"  Avg ep len  : {avg_len:.0f} | Avg P2 dmg: {avg_dmg:.1f}")
        print(f"  Avg rivals  : {avg_rivals:.2f} | Máx: {self._max_rivals_ep}")
        print(f"  Rolling     : {self._roll_uses} | FK+Roll: {self._fk_rolls}")
        print(f"  Electric    : {self._elec_uses}")
        print(f"  Arcade clear: {self._arcade_clears}")
        print(f"  Rival actual: {rname}")
        print(f"{'─'*65}")

        self.logger.record("sf2/win_rate",          wr)
        self.logger.record("sf2/ko_wins",           self._ko_wins)
        self.logger.record("sf2/timeout_wins",      self._timeout_wins)
        self.logger.record("sf2/round_wins_total",  self._total_round_wins)
        self.logger.record("sf2/match_wins_total",  self._total_match_wins)
        self.logger.record("sf2/avg_ep_len",        avg_len)
        self.logger.record("sf2/avg_p2_damage",     avg_dmg)
        self.logger.record("sf2/avg_rivals_per_ep", avg_rivals)
        self.logger.record("sf2/rolling_uses",      self._roll_uses)
        self.logger.record("sf2/electric_uses",     self._elec_uses)
        self.logger.record("sf2/fk_rolling",        self._fk_rolls)
        self.logger.record("sf2/arcade_clears",     self._arcade_clears)
        self.logger.record("sf2/any_boss_eps",      self._any_boss_eps)
        for bid in _BOSS_ORDER:
            key = _BOSS_KEY[bid]
            self.logger.record(f"sf2/boss_{key}_eps", self._boss_eps.get(bid, 0))
        self.logger.dump(self.num_timesteps)


# ── ENTORNO GYM ───────────────────────────────────────────────────────────────

N_ENVS = 6

def make_env(instance_id, registry):
    def _init():
        env = BlankaEnv(
            instance_id=instance_id,
            max_steps=999_999_999,
            registry=registry,
        )
        env = Monitor(env, os.path.join(LOGS_DIR, f"monitor_{instance_id}"))
        return env
    return _init

# ── ENTRENAMIENTO FASE 2 ──────────────────────────────────────────────────────

def train(resume_path: Optional[str], num_envs: int = 1,
          lr: float = 1e-4, total_steps: int = 5_000_000,
          visible: bool = False):

    print("\n" + "="*65)
    print(f"  SF2CE — PPO Blanka | Fase 2 ({num_envs} env) v2.2")
    print("="*65)
    print(f"  Resume     : {resume_path or 'Nuevo modelo'}")
    print(f"  LR         : {lr}")
    print(f"  Steps      : {total_steps:,}")
    print(f"  clip_range : {PPO_HPARAMS['clip_range']}  ent_coef: {PPO_HPARAMS['ent_coef']}")
    print(f"  Watchdog   : activo (check cada {MameWatchdogCallback.CHECK_EVERY} steps)")
    print(f"  Modo       : {'Primera instancia VISIBLE' if visible else 'Todas HEADLESS'}")
    print("="*65)

    registry = RivalRegistry(STATS_FILE)

    print(f"\n[*] Limpiando archivos previos...")
    clean_all(num_envs)

    print(f"[*] Lanzando {num_envs} instancias MAME (serializado v2.2)...")
    procs = launch_all(num_envs, visible_first=visible)
    activos = sum(1 for p in procs if p is not None)
    if activos == 0:
        print("ERROR: ninguna instancia activa. Abortando.")
        return
    if activos < num_envs:
        print(f"WARN: solo {activos}/{num_envs} instancias activas — watchdog relanzará las muertas")

    env = SubprocVecEnv([make_env(i, registry) for i in range(num_envs)],
                        start_method="spawn")

    is_new = resume_path is None
    vn_exists = os.path.exists(VN_PATH)
    if not is_new and vn_exists:
        try:
            env = VecNormalize.load(VN_PATH, env)
            env.training = True
            print(f"[*] Cargando VecNormalize: {VN_PATH}")
        except Exception as e:
            print(f"[*] WARN: VecNormalize incompatible ({e}), creando nuevo")
            env = VecNormalize(env, norm_obs=True, norm_reward=True,
                               clip_obs=10.0, clip_reward=10.0, gamma=0.99)
    else:
        if not is_new:
            print(f"[*] VecNormalize no encontrado, creando nuevo")
        env = VecNormalize(env, norm_obs=True, norm_reward=True,
                           clip_obs=10.0, clip_reward=10.0, gamma=0.99)

    if not is_new:
        print(f"[*] Cargando pesos desde: {resume_path}")
        model = PPO("MlpPolicy", env, learning_rate=lr,
                    device="auto", **PPO_HPARAMS)
        try:
            _, params, _ = load_from_zip_file(resume_path)
            policy_params = params.get("policy", {})
            SKIP_KEYS = {"action_net.weight", "action_net.bias",
                         "value_net.weight",  "value_net.bias"}
            filtered = {k: v for k, v in policy_params.items() if k not in SKIP_KEYS}
            missing, unexpected = model.policy.load_state_dict(filtered, strict=False)
            print(f"[*] Pesos transferidos: {len(filtered)} tensores")
            print(f"    Omitidos (action/value heads): {SKIP_KEYS}")
            if missing:
                print(f"    Missing (init aleatorio): {missing}")
        except Exception as e:
            print(f"[*] WARN: carga parcial fallida ({e}), iniciando desde cero")

        model.learning_rate = lr
        model.lr_schedule   = lambda _: lr
        for pg in model.policy.optimizer.param_groups:
            pg["lr"] = lr
        model.n_steps    = PPO_HPARAMS["n_steps"]
        model.batch_size = PPO_HPARAMS["batch_size"]
        model.clip_range = lambda _: PPO_HPARAMS["clip_range"]
        model.ent_coef   = PPO_HPARAMS["ent_coef"]
        model.target_kl  = PPO_HPARAMS["target_kl"]
    else:
        model = PPO("MlpPolicy", env, learning_rate=lr,
                    device="auto", **PPO_HPARAMS)

    params = sum(p.numel() for p in model.policy.parameters())
    print(f"[*] Parámetros de la red: {params:,}")
    print(f"[*] Iniciando entrenamiento ({total_steps:,} steps)...")
    print(f"    TensorBoard: tensorboard --logdir {LOGS_DIR}\n")

    ckpt_cb     = CheckpointVN(50_000, MODELS_DIR, "fase2", VN_PATH)
    metrics_cb  = MetricsCallback(registry=registry, verbose=1)
    watchdog_cb = MameWatchdogCallback(procs, num_envs, visible_first=visible)

    t0 = time.time()
    try:
        model.learn(
            total_timesteps     = total_steps,
            callback            = [ckpt_cb, metrics_cb, watchdog_cb],
            reset_num_timesteps = is_new,
            tb_log_name         = "ppo_blanka_fase2",
        )
    except KeyboardInterrupt:
        print("\n[CTRL+C] Interrumpido. Guardando...")

    elapsed   = time.time() - t0
    final_mdl = os.path.join(MODELS_DIR, "fase2_final")
    model.save(final_mdl)
    print(f"  Modelo  → {final_mdl}.zip")

    vn_cand = env
    for _ in range(5):
        if hasattr(vn_cand, "obs_rms"):
            vn_cand.save(VN_PATH)
            print(f"  VecNorm → {VN_PATH}")
            break
        vn_cand = getattr(vn_cand, "venv", None)
        if vn_cand is None: break

    registry.save()
    print(f"  Stats   → {STATS_FILE}")
    print(f"  Tiempo  : {elapsed/3600:.2f}h | Steps: {model.num_timesteps:,}")

    try: env.close()
    except: pass

    print("\n[CLEANUP] Terminando MAMEs...")
    for i, p in enumerate(procs):
        if p is None: continue
        try:
            p.terminate()
            print(f"  MAME-{i} PID={p.pid} terminado")
        except: pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SF2CE PPO Blanka — Fase 2 Arcade completo v2.2")
    parser.add_argument("--resume",   type=str,   default=None,
                        help="Checkpoint previo (sin .zip)")
    parser.add_argument("--lr",       type=float, default=1e-4)
    parser.add_argument("--steps",    type=int,   default=5_000_000)
    parser.add_argument("--envs",     type=int,   default=N_ENVS)
    parser.add_argument("--visible",  action="store_true", default=False,
                        help="Hacer la instancia 0 visible (para debug)")
    args = parser.parse_args()

    train(
        resume_path = args.resume,
        num_envs    = args.envs,
        lr          = args.lr,
        total_steps = args.steps,
        visible     = args.visible,
    )
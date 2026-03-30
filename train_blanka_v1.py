#!/usr/bin/env python3
"""
train_blanka_v1.py — SF2CE PPO Blanka | Flujo único (lanza MAME + entrena)
===========================================================================
Versión: 2.1 (30/03/2026)

CAMBIOS v2.1:
  · [FIX CRÍTICO] El episodio ya NO termina por Game Over.
    El Lua pulsa Continue automáticamente — Blanka nunca para.
    El episodio solo termina por:
      (a) Arcade Clear (Bison KO)  → terminated=True
      (b) MAX_STEPS truncation     → truncated=True
  · [FIX] reset() ya no llama a soft_reset/restart_game — el Lua
    gestiona todo el flujo de menús y continues.
  · [FIX] Rewards durante frames de menú/transición son 0 para no
    contaminar el aprendizaje con frames fuera de combate.
  · [METRIC] arcade_clears tracking en TensorBoard y consola.
  · [METRIC] boss_X_eps tracking (Balrog, Vega, Sagat, Bison).

CAMBIOS v2.0 (mantenidos):
  · registry.save() se llama en Ctrl+C.
  · max_steps del entorno 30000.
  · tracking de rivals_defeated por episodio.

USO:
  # Reanudar desde checkpoint:
  python train_blanka_v1.py --resume models/blanka/fase2/fase2_999912_steps

  # Entrenamiento nuevo:
  python train_blanka_v1.py

  # Ver resumen de rivales:
  python train_blanka_v1.py --stats

FLUJO AUTOMÁTICO:
  1. Este script lanza MAME con autoplay_bridge.lua
  2. El Lua navega los menús y selecciona Blanka automáticamente
  3. El agente juega runs de arcade continuas sin interrupción
  4. Al perder: el Lua pulsa Continue y sigue (NUNCA hay reset desde Python)
  5. El episodio termina solo al pasar el arcade completo (Bison KO)
     o al alcanzar MAX_STEPS
  6. Checkpoints automáticos cada 8192 steps en models/blanka/fase2/
  7. Estadísticas por rival guardadas en rival_stats.json
"""

import argparse
import os
import sys
import time
from collections import deque
from typing import Optional

import numpy as np

# ── PATH SETUP ────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.callbacks import BaseCallback

from env.blanka_env import (
    BlankaEnv, CHAR_NAMES, ROLLING_ACTIONS, ACTION_ELECTRIC,
    BOSS_IDS, BOSS_ORDER, ARCADE_FINAL_BOSS,
)
from core.rival_registry import RivalRegistry

# ── RUTAS ─────────────────────────────────────────────────────────────────────
MAME_DIR   = r"C:\proyectos\MAME"
DYN_DIR    = os.path.join(MAME_DIR, "dinamicos")
MAME_EXE   = r"C:\proyectos\MAME\EMULADOR\mame.exe"
LUA_SCRIPT = r"C:\proyectos\MAME\lua\autoplay_bridge.lua"
MODELS_DIR = os.path.join(MAME_DIR, "models", "blanka", "fase2")
LOGS_DIR   = os.path.join(MAME_DIR, "logs",   "blanka", "fase2")
VN_PATH    = os.path.join(MODELS_DIR, "vecnorm_fase2.pkl")
STATS_FILE = os.path.join(MAME_DIR, "rival_stats.json")
VER_FILE   = os.path.join(DYN_DIR, "bridge_version_0.txt")

os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR,   exist_ok=True)
os.makedirs(DYN_DIR,    exist_ok=True)

ACTION_ROLLING_FIERCE = 15
ACTION_ROLLING_STRONG = 16
ACTION_ROLLING_JAB    = 17
ACTION_ELECTRIC_ID    = 18
ACTION_ROLLING_JUMP   = 25

# ── HIPERPARÁMETROS PPO ───────────────────────────────────────────────────────
PPO_HPARAMS = dict(
    n_steps       = 4096,    # más steps por rollout — episodios más largos
    batch_size    = 128,
    n_epochs      = 4,
    gamma         = 0.99,
    gae_lambda    = 0.95,
    clip_range    = 0.2,
    ent_coef      = 0.03,
    vf_coef       = 0.5,
    max_grad_norm = 0.5,
    target_kl     = 0.02,
    policy_kwargs = dict(net_arch=[256, 256]),
    verbose       = 1,
    tensorboard_log = LOGS_DIR,
)

# ── BOSS TRACKING ─────────────────────────────────────────────────────────────
_BOSS_ORDER = [10, 11, 9, 8]   # Balrog, Vega, Sagat, Bison
_BOSS_KEY   = {10: "balrog", 11: "vega", 9: "sagat", 8: "bison"}


# ── LANZAR MAME ───────────────────────────────────────────────────────────────
def launch_mame(visible: bool = True) -> bool:
    import subprocess

    if not os.path.exists(MAME_EXE):
        print(f"[MAME] ERROR: no existe {MAME_EXE}")
        return False
    if not os.path.exists(LUA_SCRIPT):
        print(f"[MAME] ERROR: no existe {LUA_SCRIPT}")
        return False

    if os.path.exists(VER_FILE):
        try:
            os.remove(VER_FILE)
        except Exception:
            pass

    claim_file = os.path.join(DYN_DIR, "instance_id_claim.txt")
    try:
        with open(claim_file, "w") as f:
            f.write("0")
    except Exception as e:
        print(f"[MAME] WARN: no se pudo escribir claim: {e}")

    video = "d3d" if visible else "none"
    cmd = [
        MAME_EXE, "sf2ce",
        "-rompath",         os.path.join(MAME_DIR, "EMULADOR", "roms"),
        "-autoboot_script", LUA_SCRIPT,
        "-nothrottle",
        "-skip_gameinfo",
        "-video",    video,
        "-window",
        "-nomaximize",
    ]

    print(f"[MAME] Lanzando: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=os.path.dirname(MAME_EXE))
    print(f"[MAME] PID={proc.pid}")

    print("[MAME] Esperando inicialización del Lua bridge (máx 30s)...")
    deadline = time.time() + 30.0
    while time.time() < deadline:
        if proc.poll() is not None:
            print("[MAME] ERROR: MAME terminó inesperadamente")
            return False
        if os.path.exists(VER_FILE):
            try:
                with open(VER_FILE) as f:
                    ver = f.read().strip()
                if ver:
                    print(f"[MAME] Lua listo — versión bridge: {ver}")
                    time.sleep(2.0)
                    return True
            except Exception:
                pass
        time.sleep(0.2)

    print("[MAME] TIMEOUT esperando Lua.")
    return False


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

    v2.1: arcade_clears, boss tracking, sin Game Over como terminación.
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

        # ── boss tracking (v2.1) ──────────────────────────────────────────
        self._boss_eps: dict       = {bid: 0 for bid in _BOSS_ORDER}
        self._boss_first_ep: dict  = {}
        self._any_boss_eps         = 0
        self._max_bosses_ep        = 0

        # ── arcade clears (v2.1) ──────────────────────────────────────────
        self._arcade_clears        = 0
        self._arcade_first_ep: Optional[int] = None

        # ── bonus stages ──────────────────────────────────────────────────
        self._bonus_stage_eps      = 0
        self._bonus_first_ep: Optional[int] = None

    def _on_step(self) -> bool:
        infos  = self.locals.get("infos", [{}])
        info   = infos[0] if infos else {}

        action      = info.get("action",      -1)
        p2hp        = info.get("p2_hp",       144.0)
        fk_land     = info.get("fk_land",     0)
        rival       = info.get("rival",       0xFF)
        timeout_win = info.get("timeout_win", False)

        if action == ACTION_ROLLING_FIERCE:                     self._roll_fierce += 1
        if action == ACTION_ROLLING_STRONG:                     self._roll_strong += 1
        if action == ACTION_ROLLING_JAB:                        self._roll_jab    += 1
        if action == ACTION_ELECTRIC_ID:                        self._elec_uses   += 1
        if action == ACTION_ROLLING_JUMP:                       self._rjump_uses  += 1
        if action in ROLLING_ACTIONS and 0 < fk_land <= 20:    self._fk_rolls    += 1

        if "episode" in info:
            ep            = info["episode"]
            won           = info.get("won",                  False)
            t_w           = info.get("timeout_win",          False)
            rivals_def    = info.get("rivals_defeated",      0)
            reached_bonus = info.get("reached_bonus",        False)
            bosses_ids    = info.get("bosses_reached_ids",   [])
            bosses_count  = info.get("bosses_reached_count", 0)
            arcade_clear  = info.get("arcade_cleared",       False)

            self._ep_count += 1
            self._ep_wins.append(1 if won else 0)
            self._ep_lens.append(ep.get("l", 0))
            self._ep_p2dmg.append(max(0.0, 144.0 - float(p2hp)))
            self._ep_rivals_def.append(rivals_def)

            if rival <= 11:
                self._rival_eps[rival] = self._rival_eps.get(rival, 0) + 1

            # ── récord rivales ────────────────────────────────────────────
            if rivals_def > self._max_rivals_ep:
                self._max_rivals_ep = rivals_def
                rname = CHAR_NAMES.get(rival, f"ID_{rival}")
                print(
                    f"\n[🏆 NUEVO RÉCORD] {rivals_def} rivales derrotados"
                    f" | último: {rname} | steps={self.num_timesteps:,}"
                )

            # ── bonus stage ───────────────────────────────────────────────
            if reached_bonus:
                self._bonus_stage_eps += 1
                if self._bonus_first_ep is None:
                    self._bonus_first_ep = self._ep_count
                    print(
                        f"\n[⭐ PRIMER BONUS STAGE] ep={self._ep_count}"
                        f" | steps={self.num_timesteps:,}"
                    )

            # ── bosses ────────────────────────────────────────────────────
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

            # ── arcade clear ──────────────────────────────────────────────
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

            # ── victorias ─────────────────────────────────────────────────
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
            self._log(rival)

        return True

    def _log(self, last_rival: int):
        if not self._ep_wins:
            return

        wr         = np.mean(self._ep_wins) * 100
        avg_len    = np.mean(self._ep_lens)    if self._ep_lens    else 0
        avg_dmg    = np.mean(self._ep_p2dmg)   if self._ep_p2dmg   else 0
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
        print(f"  RESUMEN FINAL — Fase 2")
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
        env = BlankaEnv(instance_id=instance_id, max_steps=30000, registry=registry)
        env = Monitor(env, LOGS_DIR)
        return env
    return _init


# ── ENTRENAMIENTO ─────────────────────────────────────────────────────────────

def train(resume_path: Optional[str] = None,
          lr: float = 1e-4,
          total_steps: int = 5_000_000,
          visible: bool = True):

    print("\n" + "="*65)
    print("  SF2CE — PPO Blanka | Fase 2 — Arcade Completo (sin Game Over)")
    print("="*65)
    print(f"  Resume  : {resume_path or 'Nuevo modelo'}")
    print(f"  LR      : {lr}")
    print(f"  Steps   : {total_steps:,}")
    print(f"  Visible : {visible}")
    print(f"  Modelos : {MODELS_DIR}")
    print(f"  Logs    : {LOGS_DIR}")
    print(f"  Flujo   : episodio termina solo por ARCADE CLEAR o MAX_STEPS")
    print(f"            el Lua pulsa Continue automáticamente al perder")
    print("="*65)

    print("\n[1/4] Lanzando MAME...")
    ok = launch_mame(visible=visible)
    if not ok:
        print("ERROR: no se pudo lanzar MAME. Abortando.")
        sys.exit(1)
    print("      MAME OK\n")

    print("[2/4] Cargando registro de rivales...")
    registry = RivalRegistry(STATS_FILE)
    registry.print_summary()

    print("[3/4] Creando entorno...")
    env    = DummyVecEnv([make_env(0, registry)])
    is_new = resume_path is None

    fase2_vn = VN_PATH
    if not is_new and os.path.exists(fase2_vn):
        print(f"      Cargando VecNormalize fase2: {fase2_vn}")
        env = VecNormalize.load(fase2_vn, env)
        env.training = True
    else:
        print("      Nuevo VecNormalize (episodios arcade completos)")
        env = VecNormalize(env, norm_obs=True, norm_reward=True,
                           clip_obs=10.0, clip_reward=10.0, gamma=0.99)

    print("[4/4] Preparando modelo PPO...")
    if not is_new:
        print(f"      Cargando pesos desde: {resume_path}")
        model = PPO.load(resume_path, env=env, learning_rate=lr,
                         tensorboard_log=LOGS_DIR, device="auto")
        model.learning_rate = lr
        model.lr_schedule   = lambda _: lr
        for pg in model.policy.optimizer.param_groups:
            pg["lr"] = lr
        if model.n_steps != PPO_HPARAMS["n_steps"]:
            print(f"      Actualizando n_steps: {model.n_steps} → {PPO_HPARAMS['n_steps']}")
            model.n_steps    = PPO_HPARAMS["n_steps"]
            model.batch_size = PPO_HPARAMS["batch_size"]
    else:
        model = PPO("MlpPolicy", env, learning_rate=lr, device="auto", **PPO_HPARAMS)

    params = sum(p.numel() for p in model.policy.parameters())
    print(f"      Parámetros: {params:,}\n")

    ckpt_cb    = CheckpointVN(8192, MODELS_DIR, "fase2", fase2_vn)
    metrics_cb = MetricsCallback(registry=registry, verbose=1)

    print(f"Iniciando entrenamiento ({total_steps:,} steps)...")
    print("  Episodio = run arcade completa hasta ARCADE CLEAR o MAX_STEPS")
    print("  Game Over → Lua pulsa Continue → mismo episodio continúa\n")
    print(f"  TensorBoard: tensorboard --logdir {LOGS_DIR}\n")

    t0 = time.time()
    try:
        model.learn(
            total_timesteps     = total_steps,
            callback            = [ckpt_cb, metrics_cb],
            reset_num_timesteps = is_new,
            tb_log_name         = "ppo_blanka_fase2",
        )
    except KeyboardInterrupt:
        print("\n[CTRL+C] Interrumpido. Guardando modelo y stats...")

    # ── GUARDADO FINAL ────────────────────────────────────────────────────────
    elapsed   = time.time() - t0
    final_mdl = os.path.join(MODELS_DIR, "fase2_final")
    model.save(final_mdl)
    print(f"  Modelo  → {final_mdl}.zip")

    vn_cand = env
    for _ in range(5):
        if hasattr(vn_cand, "obs_rms"):
            vn_cand.save(fase2_vn)
            print(f"  VecNorm → {fase2_vn}")
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
        env.close()
    except Exception:
        pass


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="SF2CE PPO Blanka — Fase 2 Arcade Completo (sin Game Over)")
    parser.add_argument("--resume", type=str, default=None,
        help="Checkpoint previo (sin .zip)")
    parser.add_argument("--lr", type=float, default=None,
        help="Learning rate (default: 1e-4)")
    parser.add_argument("--steps", type=int, default=5_000_000,
        help="Total steps (default: 5M)")
    parser.add_argument("--headless", action="store_true", default=False,
        help="Lanzar MAME sin ventana visible")
    parser.add_argument("--stats", action="store_true", default=False,
        help="Mostrar estadísticas de rivales y salir")
    args = parser.parse_args()

    if args.stats:
        RivalRegistry(STATS_FILE).print_summary()
        sys.exit(0)

    lr = args.lr if args.lr is not None else 1e-4

    train(
        resume_path = args.resume,
        lr          = lr,
        total_steps = args.steps,
        visible     = not args.headless,
    )
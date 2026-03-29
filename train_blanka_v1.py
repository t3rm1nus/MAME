#!/usr/bin/env python3
"""
train_blanka_v1.py — SF2CE PPO Blanka | Flujo único (lanza MAME + entrena)
===========================================================================
Versión: 1.0 (28/03/2026)

USO:
  # Entrenamiento nuevo (lanza MAME automáticamente):
  python train_blanka_v1.py

  # Reanudar desde checkpoint:
  python train_blanka_v1.py --resume models/blanka/blanka_v1_4096_steps

  # Learning rate personalizado:
  python train_blanka_v1.py --resume <ckpt> --lr 1e-4

  # Ver resumen de rivales:
  python train_blanka_v1.py --stats

FLUJO AUTOMÁTICO:
  1. Este script lanza MAME con autoplay_bridge.lua
  2. El Lua navega los menús y selecciona Blanka automáticamente
  3. El Lua pulsa CONTINUE cuando hay Game Over
  4. El agente aprende a jugar en tiempo real — lo ves en MAME
  5. Checkpoints automáticos cada 4096 steps en models/blanka/
  6. Estadísticas por rival guardadas en rival_stats.json

REQUISITOS:
  · pip install stable-baselines3 gymnasium numpy
  · MAME 0.286 en C:/proyectos/MAME/EMULADOR/mame.exe
  · SF2CE ROM en C:/proyectos/MAME/EMULADOR/roms/
  · autoplay_bridge.lua en C:/proyectos/MAME/lua/

MULTI-INSTANCIA (futuro):
  Este script usa 1 instancia visible (instance_id=0, -video d3d).
  Para escalar a 6 instancias cambia N_ENVS=6 y usa -video none.
  La lógica de menú en el Lua funciona igual a cualquier velocidad.
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

from env.blanka_env import BlankaEnv, CHAR_NAMES
from core.rival_registry import RivalRegistry

# ── RUTAS ─────────────────────────────────────────────────────────────────────
MAME_DIR   = r"C:\proyectos\MAME"
MAME_EXE   = r"C:\proyectos\MAME\EMULADOR\mame.exe"
LUA_SCRIPT = r"C:\proyectos\MAME\lua\autoplay_bridge.lua"
MODELS_DIR = os.path.join(MAME_DIR, "models", "blanka")
LOGS_DIR   = os.path.join(MAME_DIR, "logs",   "blanka")
VN_PATH    = os.path.join(MODELS_DIR, "vecnorm_v1.pkl")
STATS_FILE = os.path.join(MAME_DIR, "rival_stats.json")
VER_FILE   = os.path.join(MAME_DIR, "bridge_version_0.txt")

os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR,   exist_ok=True)

# ── HIPERPARÁMETROS PPO ───────────────────────────────────────────────────────
PPO_HPARAMS = dict(
    n_steps       = 2048,
    batch_size    = 64,
    n_epochs      = 4,
    gamma         = 0.99,
    gae_lambda    = 0.95,
    clip_range    = 0.2,
    ent_coef      = 0.02,
    vf_coef       = 0.5,
    max_grad_norm = 0.5,
    target_kl     = 0.02,
    policy_kwargs = dict(net_arch=[256, 256]),
    verbose       = 1,
    tensorboard_log = LOGS_DIR,
)


# ── LANZAR MAME ───────────────────────────────────────────────────────────────

def launch_mame(visible: bool = True) -> bool:
    """
    Lanza MAME con autoplay_bridge.lua.
    visible=True  → -video d3d (se ve en pantalla).
    visible=False → -video none (headless, para multi-instancia futura).
    Espera hasta 30s a que el Lua escriba bridge_version_0.txt.
    """
    import subprocess

    if not os.path.exists(MAME_EXE):
        print(f"[MAME] ERROR: no existe {MAME_EXE}")
        print("       Edita MAME_EXE en train_blanka_v1.py con la ruta correcta.")
        return False
    if not os.path.exists(LUA_SCRIPT):
        print(f"[MAME] ERROR: no existe {LUA_SCRIPT}")
        return False

    # Limpiar version file de ejecuciones anteriores
    if os.path.exists(VER_FILE):
        try:
            os.remove(VER_FILE)
        except Exception:
            pass

    video = "d3d" if visible else "none"
    cmd = [
        MAME_EXE, "sf2ce",
        "-rompath",         os.path.join(MAME_DIR, "EMULADOR", "roms"),
        "-autoboot_script", LUA_SCRIPT,
        "-skip_gameinfo",
        "-sound",    "none",
        "-video",    video,
        "-window",
        "-nomaximize",
    ]

    print(f"[MAME] Lanzando: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=os.path.dirname(MAME_EXE))
    print(f"[MAME] PID={proc.pid}")

    # Esperar a que Lua escriba el version file (señal de que el bridge está activo)
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
                    time.sleep(2.0)  # margen extra para que la máquina de estados arranque
                    return True
            except Exception:
                pass
        time.sleep(0.2)

    print("[MAME] TIMEOUT esperando Lua. Comprueba que MAME arrancó correctamente.")
    return False


# ── CALLBACKS ─────────────────────────────────────────────────────────────────

class CheckpointVN(BaseCallback):
    """Guarda modelo + VecNormalize cada N steps."""

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
    """Métricas en consola + TensorBoard por rollout."""

    def __init__(self, registry: RivalRegistry, verbose: int = 1):
        super().__init__(verbose)
        self.registry    = registry
        self._ep_wins    = deque(maxlen=100)
        self._ep_lens    = deque(maxlen=100)
        self._ep_p2dmg   = deque(maxlen=100)
        self._ep_count   = 0
        self._roll_uses  = 0
        self._elec_uses  = 0
        self._fk_rolls   = 0
        self._last_roll  = -1
        self._first_win  = False
        self._rival_eps: dict = {}

    def _on_step(self) -> bool:
        infos = self.locals.get("infos", [{}])
        info  = infos[0] if infos else {}

        action  = info.get("action",  -1)
        p2hp    = info.get("p2_hp",   144.0)
        fk_land = info.get("fk_land", 0)
        rival   = info.get("rival",   0xFF)

        if action == 15: self._roll_uses += 1
        if action == 16: self._elec_uses += 1
        if action == 15 and 0 < fk_land <= 20: self._fk_rolls += 1

        if "episode" in info:
            ep  = info["episode"]
            won = info.get("won", False)
            self._ep_count += 1
            self._ep_wins.append(1 if won else 0)
            self._ep_lens.append(ep.get("l", 0))
            self._ep_p2dmg.append(144.0 - p2hp)
            if rival <= 11:
                self._rival_eps[rival] = self._rival_eps.get(rival, 0) + 1

            if won and not self._first_win:
                self._first_win = True
                rname = CHAR_NAMES.get(rival, f"ID_{rival}")
                print(f"\n[🏆 PRIMERA VICTORIA] ep={self._ep_count}"
                      f" vs {rname} | steps={self.num_timesteps:,}")

        roll = self.n_calls // PPO_HPARAMS["n_steps"]
        if roll > self._last_roll:
            self._last_roll = roll
            self._log(rival)

        return True

    def _log(self, last_rival: int):
        if not self._ep_wins:
            return
        wr      = np.mean(self._ep_wins) * 100
        avg_len = np.mean(self._ep_lens) if self._ep_lens else 0
        avg_dmg = np.mean(self._ep_p2dmg) if self._ep_p2dmg else 0
        rname   = CHAR_NAMES.get(last_rival, f"ID_{last_rival:02X}")

        print(f"\n{'─'*55}")
        print(f"  Rollout {self._last_roll} | Steps {self.num_timesteps:,}")
        print(f"  Episodios : {self._ep_count}")
        print(f"  Win rate  : {wr:.1f}%  (últimos {len(self._ep_wins)})")
        print(f"  Avg len   : {avg_len:.0f}  | Avg P2 dmg: {avg_dmg:.1f}")
        print(f"  Rolling   : {self._roll_uses} usos")
        print(f"  Electric  : {self._elec_uses} usos")
        print(f"  FK+Rolling: {self._fk_rolls}")
        print(f"  Rival     : {rname}")

        if self._rival_eps:
            rivals_str = ", ".join(
                f"{CHAR_NAMES.get(c,'?')}:{n}"
                for c, n in sorted(self._rival_eps.items()))
            print(f"  Rivales   : {rivals_str}")
        print(f"{'─'*55}")

        self.logger.record("sf2/win_rate",      wr)
        self.logger.record("sf2/avg_len",       avg_len)
        self.logger.record("sf2/avg_p2_damage", avg_dmg)
        self.logger.record("sf2/rolling_uses",  self._roll_uses)
        self.logger.record("sf2/electric_uses", self._elec_uses)
        self.logger.record("sf2/fk_rolling",    self._fk_rolls)
        self.logger.dump(self.num_timesteps)

        if self._last_roll % 20 == 0:
            self.registry.print_summary()


# ── ENTORNO ───────────────────────────────────────────────────────────────────

def make_env(instance_id: int, registry: RivalRegistry):
    def _init():
        env = BlankaEnv(instance_id=instance_id, max_steps=3000, registry=registry)
        env = Monitor(env, LOGS_DIR)
        return env
    return _init


# ── ENTRENAMIENTO ─────────────────────────────────────────────────────────────

def train(resume_path: Optional[str] = None,
          lr: float = 3e-4,
          total_steps: int = 2_000_000,
          visible: bool = True):

    print("\n" + "="*60)
    print("  SF2CE — PPO Blanka | Flujo único automático")
    print("="*60)
    print(f"  Resume  : {resume_path or 'Nuevo modelo'}")
    print(f"  LR      : {lr}")
    print(f"  Steps   : {total_steps:,}")
    print(f"  Visible : {visible}")
    print(f"  Modelos : {MODELS_DIR}")
    print(f"  Logs    : {LOGS_DIR}")
    print("="*60)

    # 1. Lanzar MAME
    print("\n[1/4] Lanzando MAME...")
    ok = launch_mame(visible=visible)
    if not ok:
        print("ERROR: no se pudo lanzar MAME. Abortando.")
        sys.exit(1)
    print("      MAME OK — Lua activo, menús navegándose automáticamente\n")

    # 2. Registro de rivales
    print("[2/4] Cargando registro de rivales...")
    registry = RivalRegistry(STATS_FILE)
    registry.print_summary()

    # 3. Crear entorno
    print("[3/4] Creando entorno...")
    env = DummyVecEnv([make_env(0, registry)])
    is_new = resume_path is None

    if not is_new and os.path.exists(VN_PATH):
        print(f"      Cargando VecNormalize: {VN_PATH}")
        env = VecNormalize.load(VN_PATH, env)
        env.training = True
    else:
        env = VecNormalize(env, norm_obs=True, norm_reward=True,
                           clip_obs=10.0, clip_reward=10.0, gamma=0.99)

    # 4. Modelo PPO
    print("[4/4] Preparando modelo PPO...")
    if not is_new:
        print(f"      Cargando: {resume_path}")
        model = PPO.load(resume_path, env=env, learning_rate=lr,
                         tensorboard_log=LOGS_DIR, device="auto")
        model.learning_rate = lr
        model.lr_schedule   = lambda _: lr
        for pg in model.policy.optimizer.param_groups:
            pg["lr"] = lr
    else:
        model = PPO("MlpPolicy", env, learning_rate=lr, device="auto", **PPO_HPARAMS)

    params = sum(p.numel() for p in model.policy.parameters())
    print(f"      Parámetros: {params:,}\n")

    # Callbacks
    ckpt_cb = CheckpointVN(
        save_freq = 4096,
        save_path = MODELS_DIR,
        prefix    = "blanka_v1",
        vn_path   = VN_PATH,
        verbose   = 1,
    )
    metrics_cb = MetricsCallback(registry=registry, verbose=1)

    print(f"Iniciando entrenamiento ({total_steps:,} steps)...\n")
    print("  El agente empezará a actuar cuando el juego entre en combate.")
    print("  Puedes ver el progreso en la ventana de MAME.\n")
    t0 = time.time()

    try:
        model.learn(
            total_timesteps     = total_steps,
            callback            = [ckpt_cb, metrics_cb],
            reset_num_timesteps = is_new,
            tb_log_name         = "ppo_blanka_v1",
        )
    except KeyboardInterrupt:
        print("\n[CTRL+C] Interrumpido. Guardando...")

    # Guardar final
    elapsed   = time.time() - t0
    final_mdl = os.path.join(MODELS_DIR, "blanka_v1_final")
    model.save(final_mdl)

    vn_cand = env
    for _ in range(5):
        if hasattr(vn_cand, "obs_rms"):
            vn_cand.save(VN_PATH)
            print(f"  VecNormalize → {VN_PATH}")
            break
        vn_cand = getattr(vn_cand, "venv", None)
        if vn_cand is None:
            break

    registry.save()

    print(f"\n{'='*60}")
    print(f"  Entrenamiento finalizado")
    print(f"  Modelo  : {final_mdl}.zip")
    print(f"  Tiempo  : {elapsed/3600:.2f}h")
    print(f"  Steps   : {model.num_timesteps:,}")
    print(f"{'='*60}")
    registry.print_summary()

    try:
        env.close()
    except Exception:
        pass


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="SF2CE PPO Blanka — flujo único (lanza MAME + entrena)")
    parser.add_argument("--resume", type=str, default=None,
        help="Checkpoint previo (sin .zip)")
    parser.add_argument("--lr", type=float, default=None,
        help="Learning rate (default: 3e-4 nuevo, 1e-4 resume)")
    parser.add_argument("--steps", type=int, default=2_000_000,
        help="Total steps (default: 2M)")
    parser.add_argument("--headless", action="store_true", default=False,
        help="Lanzar MAME sin ventana visible (-video none)")
    parser.add_argument("--stats", action="store_true", default=False,
        help="Mostrar estadísticas de rivales y salir")
    args = parser.parse_args()

    if args.stats:
        reg = RivalRegistry(STATS_FILE)
        reg.print_summary()
        sys.exit(0)

    # LR automático según si es resume o nuevo
    if args.lr is not None:
        lr = args.lr
    elif args.resume is not None:
        lr = 1e-4
        print(f"[INFO] Resume detectado → lr automático = {lr}")
    else:
        lr = 3e-4

    train(
        resume_path = args.resume,
        lr          = lr,
        total_steps = args.steps,
        visible     = not args.headless,
    )
"""
blanka_env.py — Entorno Gymnasium Blanka vs Arcade (SF2CE / MAME 0.286)
========================================================================
Versión: 2.8 (29/03/2026)

Cambios v2.8:
  · [NUEVO] Flag ROLLING_ONLY (línea ~45) para forzar acción 15 en cada step.
    Útil para verificar que el Rolling funciona en ambos lados y medir hit rate.
    Ponlo a True para activar, False para volver al modo PPO normal.
    El agente sigue recibiendo obs/recompensas normales — solo se puentea
    la selección de acción. No se borra ni comenta ningún otro movimiento.
  · [MANTIENE] Todos los fixes de v2.7 (recompensas Rolling elevadas,
    timer fiable por frames internos del Lua v1.13).
"""

import os, time, sys
import gymnasium as gym
from gymnasium import spaces
import numpy as np
from collections import deque
from typing import Optional, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from mame_bridge import MAMEBridge
from core.rival_registry import RivalRegistry

# ── MODO DEPURACIÓN ───────────────────────────────────────────────────────────
# [v2.8] Pon ROLLING_ONLY = True para que Blanka ejecute Rolling en CADA step,
# ignorando la política del agente. Útil para:
#   · Verificar que el rolling funciona en ambos lados (izquierda y derecha).
#   · Medir cuántos rollings conectan por combate (hit rate bruto).
#   · Confirmar que la macro de 69f se ejecuta correctamente end-to-end.
# El entorno sigue calculando obs, reward e info normalmente.
# Pon False para volver al entrenamiento PPO normal.
ROLLING_ONLY: bool = False

# ── CONSTANTES ────────────────────────────────────────────────────────────────
MAX_HP           = 144.0
MAX_X            = 1400.0
STUN_MAX         = 200.0
TIMER_MAX        = 99.0
EPISODE_MIN_HP   = 100
CHARGE_REQUIRED  = 68
BOOM_FLIGHT_STEPS= 51
FK_YVEL_THR      = 256
LANDING_WINDOW   = 8

_MAX_BRIDGE_ERRORS = 10

CHAR_NAMES = {
    0:"Ryu",1:"Honda",2:"Blanka",3:"Guile",4:"Ken",5:"Chun-Li",
    6:"Zangief",7:"Dhalsim",8:"M.Bison",9:"Sagat",10:"Balrog",11:"Vega",
}

# ── ACTION SPACE ──────────────────────────────────────────────────────────────
SINGLE_FRAME_ACTIONS: List[List[int]] = [
    [0,0,0,0,0,0,0,0,0,0,0,0],  #  0  NOOP
    [1,0,0,0,0,0,0,0,0,0,0,0],  #  1  UP
    [0,1,0,0,0,0,0,0,0,0,0,0],  #  2  DOWN
    [0,0,1,0,0,0,0,0,0,0,0,0],  #  3  LEFT
    [0,0,0,1,0,0,0,0,0,0,0,0],  #  4  RIGHT
    [0,0,0,0,1,0,0,0,0,0,0,0],  #  5  JAB
    [0,0,0,0,0,1,0,0,0,0,0,0],  #  6  STRONG
    [0,0,0,0,0,0,1,0,0,0,0,0],  #  7  FIERCE
    [0,0,0,0,0,0,0,1,0,0,0,0],  #  8  SHORT
    [0,0,0,0,0,0,0,0,1,0,0,0],  #  9  FORWARD
    [0,0,0,0,0,0,0,0,0,1,0,0],  # 10  ROUNDHOUSE
    [0,1,0,0,1,0,0,0,0,0,0,0],  # 11  DOWN+JAB
    [0,1,0,0,0,0,1,0,0,0,0,0],  # 12  DOWN+FIERCE
    [0,1,0,0,0,0,0,1,0,0,0,0],  # 13  DOWN+SHORT
    [0,1,0,0,0,0,0,0,0,1,0,0],  # 14  DOWN+RH
    # 15-23 → macros
]

# ── MACROS ────────────────────────────────────────────────────────────────────
MACRO_ROLLING: List[List[int]] = (
    [[0,0,1,0,0,0,0,0,0,0,0,0]] * 68 +   # ← 68 frames (carga real SF2CE)
    [[0,0,0,1,0,0,1,0,0,0,0,0]]           # → + FIERCE
)

MACRO_ELECTRIC: List[List[int]] = [[0,0,0,0,1,0,0,0,0,0,0,0]] * 5

_UP_F = 4
_AIR  = 18
_ATK  = 3

MACRO_JUMP_FWD_FIERCE: List[List[int]] = (
    [[1,0,0,1,0,0,0,0,0,0,0,0]] * _UP_F +
    [[0,0,0,1,0,0,0,0,0,0,0,0]] * _AIR  +
    [[0,0,0,1,0,0,1,0,0,0,0,0]] * _ATK
)
MACRO_JUMP_FWD_FORWARD: List[List[int]] = (
    [[1,0,0,1,0,0,0,0,0,0,0,0]] * _UP_F +
    [[0,0,0,1,0,0,0,0,0,0,0,0]] * _AIR  +
    [[0,0,0,1,0,0,0,0,1,0,0,0]] * _ATK
)
MACRO_JUMP_FWD_RH: List[List[int]] = (
    [[1,0,0,1,0,0,0,0,0,0,0,0]] * _UP_F +
    [[0,0,0,1,0,0,0,0,0,0,0,0]] * _AIR  +
    [[0,0,0,1,0,0,0,0,0,1,0,0]] * _ATK
)
MACRO_JUMP_NEU_FIERCE: List[List[int]] = (
    [[1,0,0,0,0,0,0,0,0,0,0,0]] * _UP_F +
    [[0,0,0,0,0,0,0,0,0,0,0,0]] * _AIR  +
    [[0,0,0,0,0,0,1,0,0,0,0,0]] * _ATK
)
MACRO_JUMP_BACK_FIERCE: List[List[int]] = (
    [[1,0,1,0,0,0,0,0,0,0,0,0]] * _UP_F +
    [[0,0,1,0,0,0,0,0,0,0,0,0]] * _AIR  +
    [[0,0,1,0,0,0,1,0,0,0,0,0]] * _ATK
)
MACRO_JUMP_BACK_FORWARD: List[List[int]] = (
    [[1,0,1,0,0,0,0,0,0,0,0,0]] * _UP_F +
    [[0,0,1,0,0,0,0,0,0,0,0,0]] * _AIR  +
    [[0,0,1,0,0,0,0,0,1,0,0,0]] * _ATK
)
MACRO_ROLLING_JUMP: List[List[int]] = (
    [[0,0,0,1,0,0,1,0,0,0,0,0]]
)

MACROS: Dict[int, List[List[int]]] = {
    15: MACRO_ROLLING,
    16: MACRO_ELECTRIC,
    17: MACRO_JUMP_FWD_FIERCE,
    18: MACRO_JUMP_FWD_FORWARD,
    19: MACRO_JUMP_FWD_RH,
    20: MACRO_JUMP_NEU_FIERCE,
    21: MACRO_JUMP_BACK_FIERCE,
    22: MACRO_JUMP_BACK_FORWARD,
    23: MACRO_ROLLING_JUMP,
}

FLIP_ACTIONS = {15, 17, 18, 19, 21, 22, 23}

NOOP = SINGLE_FRAME_ACTIONS[0]
N_ACTIONS = 24

# ID de la acción Rolling (para ROLLING_ONLY)
ACTION_ROLLING = 15


def fk_phase_value(anim: int, p2_airborne: bool) -> float:
    if not p2_airborne: return 0.0
    if anim == 0x0C: return 0.2
    if anim == 0x02: return 0.4
    if anim == 0x00: return 0.6
    if anim == 0x04: return 0.8
    return 0.1


class BlankaEnv(gym.Env):
    """
    observation_space: Box(30,) float32
    action_space:      Discrete(24)
      0-14  → single frame
      15    → MACRO_ROLLING (69 frames)
      16    → MACRO_ELECTRIC (5 frames)
      17-19 → salto adelante + ataque (25 frames)
      20    → salto neutro + Fierce (25 frames)
      21-22 → salto atrás + ataque (25 frames)
      23    → Rolling al aterrizar (1 frame, solo en ventana)

    [v2.8] Si ROLLING_ONLY=True, step() ignora la acción del agente
    y siempre ejecuta ACTION_ROLLING (15). La macro maneja el flip
    de dirección automáticamente según p1_dir, por lo que el rolling
    funciona correctamente tanto mirando a la derecha como a la izquierda.
    """
    metadata = {"render_modes": ["human"]}

    def __init__(self, instance_id: int = 0, max_steps: int = 3000,
                 render_mode=None, registry: Optional[RivalRegistry] = None):
        super().__init__()
        self.instance_id = instance_id
        self.MAX_STEPS   = max_steps
        self.render_mode = render_mode
        self.registry    = registry
        self.bridge      = MAMEBridge(instance_id=instance_id)

        self.observation_space = spaces.Box(-1.0, 1.0, shape=(30,), dtype=np.float32)
        self.action_space      = spaces.Discrete(N_ACTIONS)

        self._prev_p1_hp    = MAX_HP
        self._prev_p2_hp    = MAX_HP
        self._ep_step       = 0
        self._last_action   = 0
        self._last_p1_dir   = 1
        self._current_rival = 0xFF
        self._p1_hp_hist    = deque(maxlen=5)
        self._p2_hp_hist    = deque(maxlen=5)
        self._charge        = 0
        self._boom_timer    = BOOM_FLIGHT_STEPS
        self._boom_est_x    = 0.0
        self._p2_was_air    = False
        self._fk_land_steps = 0
        self._gnd_steps     = 0
        self._macro_active  = False
        self._macro_seq: List[List[int]] = []
        self._macro_buf     = 0
        self._ep_p1_dmg     = 0.0
        self._ep_p2_dmg     = 0.0
        self._soft_fails    = 0
        self._MAX_SF        = 3
        self._bridge_error_count = 0
        self._p1_was_air       = False
        self._p1_land_steps    = 0
        self._jump_back_charge = 0
        self._rolling_jump_rdy = False

        # [v2.8] Estadísticas de ROLLING_ONLY para diagnóstico por episodio
        self._rolling_count  = 0
        self._rolling_hits   = 0

    # ── OBSERVACIÓN ──────────────────────────────────────────────────────────
    def _get_obs(self, st: Optional[Dict]) -> np.ndarray:
        if st is None:
            return np.zeros(30, dtype=np.float32)
        p1hp  = float(st.get("p1_hp",      MAX_HP))
        p2hp  = float(st.get("p2_hp",      MAX_HP))
        p1x   = float(st.get("p1_x",       700.0))
        p2x   = float(st.get("p2_x",       700.0))
        p1dir = float(st.get("p1_dir",     1))
        p1air = bool (st.get("p1_airborne",False))
        p1stn = float(st.get("p1_stun",    0))
        p2air = bool (st.get("p2_airborne",False))
        p2stn = float(st.get("p2_stun",    0))
        p2his = int  (st.get("p2_hitstop", 0))
        p2anim= int  (st.get("p2_anim",    0))
        # [v2.7] timer ya es fiable: calculado por frames internos en Lua v1.13+
        timer = float(st.get("timer",      99))
        prj   = bool (st.get("boom_slot_active", False))

        dist     = abs(p1x - p2x)
        charge_n = min(self._charge / float(CHARGE_REQUIRED), 1.0)
        boom_t_n = min(self._boom_timer / float(BOOM_FLIGHT_STEPS), 1.0)
        sb_active= self._boom_timer < BOOM_FLIGHT_STEPS

        d1 = max(0.0, max(self._p1_hp_hist) - p1hp) / MAX_HP if self._p1_hp_hist else 0.0
        d2 = max(0.0, max(self._p2_hp_hist) - p2hp) / MAX_HP if self._p2_hp_hist else 0.0

        p2cr   = bool(st.get("p2_crouch", False))
        p1land = bool(st.get("p1_landing_this_frame", False)) if st else False

        obs = np.array([
            p1hp / MAX_HP,                       # 0  HP Blanka
            p1x  / MAX_X,                        # 1  X Blanka
            float(p1air),                        # 2  Blanka en el aire
            p1dir,                               # 3  Dirección
            min(p1stn, STUN_MAX) / STUN_MAX,     # 4  Stun Blanka
            p2hp / MAX_HP,                       # 5  HP rival
            p2x  / MAX_X,                        # 6  X rival
            float(p2cr),                         # 7  Rival agachado
            float(p2air),                        # 8  Rival en el aire
            min(p2stn, STUN_MAX) / STUN_MAX,     # 9  Stun rival
            dist  / MAX_X,                       # 10 Distancia
            (p1x - p2x) / MAX_X,                # 11 Distancia con signo
            timer / TIMER_MAX,                   # 12 Timer (fiable desde v2.7)
            float(p1x < 150 or p1x > 1250),     # 13 Blanka en esquina
            float(p2x < 150 or p2x > 1250),     # 14 Rival en esquina
            (p1hp - p2hp) / MAX_HP,             # 15 Diferencia de vida
            d1,                                  # 16 Daño recibido reciente
            d2,                                  # 17 Daño infligido reciente
            fk_phase_value(p2anim, p2air),       # 18 Fase FK
            float(sb_active),                    # 19 Boom en vuelo
            (self._boom_est_x / MAX_X) if sb_active else 0.0,  # 20 X estimada boom
            float(prj),                          # 21 Slot proyectil activo
            boom_t_n,                            # 22 Timer boom
            charge_n,                            # 23 Carga acumulada (← manual)
            float(self._gnd_steps > 30),         # 24 Rival mucho tiempo en tierra
            float(p2his > 0),                    # 25 Rival en hitstop
            float(self._fk_land_steps > 0 and self._fk_land_steps <= 20),  # 26 Ventana post-FK
            self._last_action / float(N_ACTIONS - 1),            # 27 Última acción
            float(p1land or (self._p1_land_steps > 0 and self._p1_land_steps <= LANDING_WINDOW)),  # 28 Blanka aterrizando
            float(self._rolling_jump_rdy),       # 29 Rolling-jump listo
        ], dtype=np.float32)
        return np.clip(obs, -1.0, 1.0)

    # ── REWARD ───────────────────────────────────────────────────────────────
    def _calc_reward(self, p1hp: float, p2hp: float, st: Dict, action: int) -> float:
        dp1 = max(0.0, self._prev_p1_hp - p1hp)
        dp2 = max(0.0, self._prev_p2_hp - p2hp)
        p1x = float(st.get("p1_x", 700.0))
        p2x = float(st.get("p2_x", 700.0))
        p1a = bool(st.get("p1_airborne", False))
        p2a = bool(st.get("p2_airborne", False))
        prj = bool(st.get("boom_slot_active", False))
        dist = abs(p1x - p2x)
        r = dp2 * 2.0 - dp1 * 1.5

        if p2hp <= 0 and p1hp > 0:    r += 100.0
        elif p1hp <= 0 and p2hp > 0:  r -= 50.0
        elif p1hp <= 0 and p2hp <= 0: r -= 20.0

        sb  = self._boom_timer < BOOM_FLIGHT_STEPS
        sbi = self._boom_timer < 15
        if sb and p1a:                          r += 8.0
        if sbi and p1a:                         r += 5.0
        if sbi and not p1a and action == 0:     r -= 6.0
        if prj and dp1 > 0:                     r -= 8.0
        if sb and not p1a and action == 3:      r += 1.5

        # ── Rolling Attack (acción 15) ────────────────────────────────────────
        # [v2.7] Recompensas elevadas para compensar el crédito tardío
        # de la macro de 69 frames. Sin esto PPO aprende que rolling = malo.
        if action == ACTION_ROLLING:
            in_fk_window = 0 < self._fk_land_steps <= 20
            good_dist    = 200 <= dist <= 600

            if in_fk_window:
                r += 35.0 if dp2 > 0 else 8.0
            elif dp2 > 0:
                r += 30.0 if good_dist else 20.0
            else:
                if dist < 150:
                    r -= 2.0
                elif dist > 700:
                    r -= 2.0
                else:
                    r -= 1.0

        # ── Bonus de carga acumulada (← sostenido) ───────────────────────────
        back = 3 if self._last_p1_dir == 1 else 4
        if action == back or action in (21, 22):
            r += 0.05

        # ── Electricidad (acción 16) ──────────────────────────────────────────
        if action == 16:
            r += 5.0 if (p2a and dp2 > 0) else (-2.0 if not p2a else 0.0)

        # ── Penalización inactividad ──────────────────────────────────────────
        if self._ep_step > 50 and dp2 == 0 and dp1 == 0: r -= 0.002

        # ── Rival en esquina ─────────────────────────────────────────────────
        if p2x < 100 or p2x > 1300: r += 1.0

        # ── Rolling desde salto atrás (acción 23) ────────────────────────────
        if action == 23:
            in_window = self._p1_land_steps > 0 and self._p1_land_steps <= LANDING_WINDOW
            had_charge = self._jump_back_charge >= CHARGE_REQUIRED
            if in_window and dp2 > 0:         r += 22.0
            elif in_window and had_charge:     r += 6.0
            elif dp2 > 0:                      r += 8.0
            else:                              r -= 3.0

        # ── Saltos con ataque (acciones 17-22) ───────────────────────────────
        JUMP_ATTACKS = (17, 18, 19, 20, 21, 22)
        if action in JUMP_ATTACKS:
            if dp2 > 0:
                r += 6.0
                if 150 <= dist <= 500: r += 4.0
            elif p1a and dp1 > 0:
                r -= 3.0
            if action in (17, 18, 19) and sb: r += 6.0
            if not sb and self._fk_land_steps == 0 and dist > 600: r -= 1.0

        return float(r)

    # ── MACRO ENGINE ─────────────────────────────────────────────────────────
    def _resolve(self, action: int) -> List[int]:
        if self._macro_active:
            if self._macro_seq:
                return self._macro_seq.pop(0)
            self._macro_active = False
            self._macro_buf = 0

        if self._macro_buf > 0:
            self._macro_buf -= 1
            return NOOP

        if action in MACROS:
            if action == 23:
                in_window = self._p1_land_steps > 0 and self._p1_land_steps <= LANDING_WINDOW
                if not in_window:
                    return NOOP

            seq = [list(f) for f in MACROS[action]]

            if action in FLIP_ACTIONS and self._last_p1_dir == 0:
                seq = [[f[0],f[1],f[3],f[2]]+f[4:] for f in seq]

            self._macro_active = True
            self._macro_seq = seq
            if self._macro_seq:
                return self._macro_seq.pop(0)
            self._macro_active = False
            return NOOP

        if action < len(SINGLE_FRAME_ACTIONS):
            return list(SINGLE_FRAME_ACTIONS[action])

        return NOOP

    def _update_charge(self, action: int):
        if action in (15, 23):
            self._charge = 0
            return
        back = 3 if self._last_p1_dir == 1 else 4
        if action == back or action in (21, 22):
            self._charge = min(self._charge + 1, CHARGE_REQUIRED)
        else:
            self._charge = max(0, self._charge - 1)

    def _update_internals(self, st: Dict):
        p2a  = bool(st.get("p2_airborne", False))
        proj = bool(st.get("boom_slot_active", False))
        p2x  = float(st.get("p2_x", 700.0))
        cid  = int(st.get("p2_char", 0xFF))

        p1a         = bool(st.get("p1_airborne", False))
        p1land_frame= bool(st.get("p1_landing_this_frame", False))

        back_jump = self._last_action in (21, 22)
        back_dir  = (self._last_action == 3 and self._last_p1_dir == 1) or \
                    (self._last_action == 4 and self._last_p1_dir == 0)
        back_held = back_jump or back_dir

        if p1a:
            if back_held:
                self._jump_back_charge = min(self._jump_back_charge + 1, CHARGE_REQUIRED + 10)
        else:
            if not self._p1_was_air:
                self._jump_back_charge = max(0, self._jump_back_charge - 1)

        if p1land_frame:
            self._p1_land_steps = 1
        elif self._p1_land_steps > 0:
            self._p1_land_steps += 1
            if self._p1_land_steps > LANDING_WINDOW + 5:
                self._p1_land_steps = 0
        self._p1_was_air = p1a

        self._rolling_jump_rdy = (
            self._jump_back_charge >= CHARGE_REQUIRED and
            self._p1_land_steps > 0 and
            self._p1_land_steps <= LANDING_WINDOW
        )

        if proj and self._boom_timer >= BOOM_FLIGHT_STEPS:
            self._boom_timer = 0
        else:
            self._boom_timer = min(self._boom_timer + 1, BOOM_FLIGHT_STEPS)
        self._boom_est_x = (
            max(0.0, p2x - self._boom_timer * 25)
            if self._boom_timer < BOOM_FLIGHT_STEPS else 0.0
        )

        if self._p2_was_air and not p2a: self._fk_land_steps = 1
        elif self._fk_land_steps > 0:
            self._fk_land_steps += 1
            if self._fk_land_steps > 30: self._fk_land_steps = 0
        self._p2_was_air = p2a
        self._gnd_steps  = 0 if p2a else min(self._gnd_steps + 1, 60)
        self._last_p1_dir = int(st.get("p1_dir", 1))
        if cid <= 11: self._current_rival = cid

    # ── RESET ────────────────────────────────────────────────────────────────
    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self._prev_p1_hp = MAX_HP; self._prev_p2_hp = MAX_HP
        self._ep_step = 0; self._last_action = 0; self._last_p1_dir = 1
        self._charge = 0; self._boom_timer = BOOM_FLIGHT_STEPS; self._boom_est_x = 0.0
        self._p2_was_air = False; self._fk_land_steps = 0; self._gnd_steps = 0
        self._macro_active = False; self._macro_seq = []; self._macro_buf = 0
        self._ep_p1_dmg = 0.0; self._ep_p2_dmg = 0.0
        self._p1_hp_hist.clear(); self._p2_hp_hist.clear()
        self._bridge_error_count = 0
        self._p1_was_air = False; self._p1_land_steps = 0
        self._jump_back_charge = 0; self._rolling_jump_rdy = False

        # [v2.8] Stats rolling_only por episodio
        if ROLLING_ONLY and self._rolling_count > 0:
            hit_rate = self._rolling_hits / max(1, self._rolling_count) * 100
            print(f"[RollingOnly] EP anterior: {self._rolling_count} rollings, "
                  f"{self._rolling_hits} hits ({hit_rate:.1f}%)")
        self._rolling_count = 0
        self._rolling_hits  = 0

        print(f"[BlankaEnv#{self.instance_id}] Reset..."
              + (" [ROLLING_ONLY]" if ROLLING_ONLY else ""))

        if self._soft_fails < self._MAX_SF:
            if not self.bridge.soft_reset(timeout=90.0):
                self._soft_fails += 1
            else:
                self._soft_fails = 0
        else:
            print(f"[BlankaEnv#{self.instance_id}] Hard restart...")
            self._soft_fails = 0
            self.bridge.restart_game()
            time.sleep(3.0)

        st = None
        deadline = time.time() + 60.0
        while time.time() < deadline:
            st = self.bridge.step([0]*12)
            if st:
                in_combat = bool(st.get("in_combat", True))
                p1 = st.get("p1_hp", 0)
                p2 = st.get("p2_hp", 0)
                if in_combat and p1 >= EPISODE_MIN_HP and p2 >= EPISODE_MIN_HP:
                    break
            time.sleep(0.05)

        if st is None:
            return np.zeros(30, dtype=np.float32), {}

        p1  = st.get("p1_hp", 0); p2 = st.get("p2_hp", 0)
        cid = st.get("p2_char", 0xFF)
        self._last_p1_dir = int(st.get("p1_dir", 1))
        print(f"[BlankaEnv#{self.instance_id}] vs {CHAR_NAMES.get(cid,'?')} | "
              f"P1={p1} P2={p2} dir={'→' if self._last_p1_dir==1 else '←'}")
        self._prev_p1_hp = float(p1); self._prev_p2_hp = float(p2)
        self._current_rival = cid if cid <= 11 else 0xFF
        return self._get_obs(st), {"p1_hp": p1, "p2_hp": p2, "rival": cid, "p1_dir": self._last_p1_dir}

    # ── STEP ─────────────────────────────────────────────────────────────────
    def step(self, action: int):
        self._ep_step += 1
        action = int(action)

        # [v2.8] ROLLING_ONLY: ignora la política del agente, fuerza rolling.
        # El flip de dirección (izquierda/derecha) lo gestiona _resolve()
        # automáticamente usando self._last_p1_dir, así que funciona en
        # ambos lados sin ningún cambio adicional.
        if ROLLING_ONLY:
            action = ACTION_ROLLING

        st = self.bridge.step(self._resolve(action))

        if st is None:
            self._bridge_error_count += 1
            if self._bridge_error_count >= _MAX_BRIDGE_ERRORS:
                print(f"[BlankaEnv#{self.instance_id}] BRIDGE ERROR x{_MAX_BRIDGE_ERRORS} — truncando")
                return self._get_obs(None), -1.0, False, True, {"bridge_error": True}
            last = self.bridge._last_state
            return self._get_obs(last), 0.0, False, False, {"bridge_retry": True}
        else:
            self._bridge_error_count = 0

        self._update_charge(action)
        self._update_internals(st)
        p1hp = float(st.get("p1_hp", MAX_HP)); p2hp = float(st.get("p2_hp", MAX_HP))
        self._p1_hp_hist.append(self._prev_p1_hp); self._p2_hp_hist.append(self._prev_p2_hp)

        dp2 = max(0.0, self._prev_p2_hp - p2hp)
        self._ep_p1_dmg += max(0.0, self._prev_p1_hp - p1hp)
        self._ep_p2_dmg += dp2

        # [v2.8] Acumular stats de rolling_only
        if ROLLING_ONLY and action == ACTION_ROLLING and not self._macro_active:
            # Contamos el rolling cuando se dispara (macro_active acaba de activarse)
            # En realidad lo contamos en el step que lo inicia.
            # Detectamos inicio: macro_buf == 0 y no había macro antes
            self._rolling_count += 1
            if dp2 > 0:
                self._rolling_hits += 1

        in_combat  = bool(st.get("in_combat", True))
        won        = p2hp <= 0 and p1hp > 0
        terminated = not in_combat if "in_combat" in st else (p1hp <= 0 or p2hp <= 0)
        truncated  = (not terminated) and self._ep_step >= self.MAX_STEPS

        if (terminated or truncated) and self.registry and self._current_rival <= 11:
            self.registry.record_episode(self._current_rival, won, self._ep_p1_dmg, self._ep_p2_dmg)

        r = self._calc_reward(p1hp, p2hp, st, action)
        self._prev_p1_hp = p1hp; self._prev_p2_hp = p2hp; self._last_action = action

        return self._get_obs(st), r, terminated, truncated, {
            "p1_hp": p1hp, "p2_hp": p2hp, "won": won, "step": self._ep_step,
            "action": action, "rival": self._current_rival,
            "boom_t": self._boom_timer, "fk_land": self._fk_land_steps,
            "charge": self._charge,
            "rolling_jump": int(action == 23),
            "p1_land": self._p1_land_steps,
            "rolling_jump_rdy": int(self._rolling_jump_rdy),
            "rolling_only": ROLLING_ONLY,
        }

    def render(self): pass

    def close(self):
        try: self.bridge.disconnect()
        except Exception: pass
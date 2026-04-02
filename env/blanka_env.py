"""
blanka_env.py — Entorno Gymnasium Blanka vs Arcade (SF2CE / MAME 0.286)
========================================================================
Version: 5.11 (02/04/2026)

CAMBIOS v5.10 — FIX CRÍTICO: SIN RESET MID-ARCADE / EPISODIO = ARCADE COMPLETO

  PROBLEMA RAÍZ IDENTIFICADO:
  ─────────────────────────────
  En v5.8, cuando SB3 llamaba reset() al alcanzar MAX_STEPS (truncation),
  reset() entraba en un busy-wait enviando NOOPs vía bridge.step([0]*12)
  durante hasta 60 segundos. Esos NOOPs llegaban al MAME mientras Blanka
  estaba en medio de un combate → Blanka se quedaba quieta → perdía por
  inactividad → Game Over → Lua insertaba coin y arrancaba desde el
  principio del arcade → siempre aparecía Guile (primer rival de la ruta).

  El resultado visible en los logs: siempre "vs Guile", nunca otro rival.
  El agente NUNCA avanzaba en el arcade porque cada truncation lo reiniciaba.

  FIX:
  ─────
  1. El episodio NO termina nunca por truncation (MAX_STEPS eliminado como
     condición de terminación). Solo termina cuando:
       (a) Arcade clear (Bison derrotado)  → terminated=True
       (b) Crash del bridge (>10 errores)  → truncated=True (emergencia)
     Esto garantiza que el agente vive TODO el arcade sin interrupciones.

  2. reset() ya NO envía NOOPs al bridge. Solo llama bridge.step([0]*12)
     UNA VEZ para leer el estado actual. Si no hay estado válido, espera
     leyendo pasivamente (sin enviar inputs) hasta que MAME esté en combate.
     Así el Lua sigue ejecutando los inputs del episodio anterior sin
     interferencia hasta que termina naturalmente.

  3. Se añade _ep_total_steps como contador informativo (sin límite).
     Los checkpoints de SB3 se basan en total_timesteps del trainer,
     no en el episodio, así que esto no afecta el guardado de modelos.

  4. La lógica de "Victoria por TIEMPO" en terminación se elimina porque
     ya no hay truncation por pasos. Solo queda como fallback de emergencia
     si el bridge falla catastrófica mente.

  INVARIANTE ACTUALIZADO:
  ────────────────────────
  · El episodio comienza cuando Blanka entra en el primer combate del arcade.
  · El episodio termina SOLO cuando:
      - Blanka derrota a M.Bison (arcade clear) → terminated=True
      - El bridge falla >10 veces seguidas (crash MAME) → truncated=True
  · Durante el episodio, el agente atraviesa todos los rivales del arcade
    sin interrupciones. Los continues se gestionan automáticamente por el Lua.

CAMBIOS v5.8 (mantenidos):
  · Timing fix reward Rolling/Electric (_macro_action_id).
  · info dict con action_real y macro_just_started.

CAMBIOS v5.7 (mantenidos):
  · Fix _arcade_cleared, _rivals_defeated, _ep_rounds_played/matches_played.

CAMBIOS v5.6 (mantenidos):
  · Fix orden _update_internals/_update_round_tracking.

CAMBIOS v5.5 (mantenidos):
  · _HP_DEAD_THRESHOLD=0, _HP_DEAD_CONFIRM_FRAMES=1.
  · Detección de kill en frame de transición FSM.

CAMBIOS v5.3 (mantenidos):
  · P2 Danger Zone reward per-step.

CAMBIOS v5.2 (mantenidos):
  · HP=255 sanitizado. Rewards ronda: +100/+200/-50. Fix flush doble.

CAMBIOS v5.1 (mantenidos):
  · Bugs #1/#2/#3 de CL-1 (real_action en reward, LEFT/RIGHT en CL-1).

CAMBIOS v5.0 (mantenidos):
  · ROLLING_AND_ELECTRIC_ONLY=True. Reset guardado. Penalizaciones rebalanceadas.
"""

import os, time, sys
import gymnasium as gym
from gymnasium import spaces
import numpy as np
from collections import deque
from typing import Optional, Dict, List, Tuple, Set
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import constants

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from mame_bridge import MAMEBridge
from core.rival_registry import RivalRegistry

# ── CONFIG ───────────────────────────────────────────────────────────────────
ROLLING_AND_ELECTRIC_ONLY: bool = True   # DEPRECATED — usar cl1_mode en BlankaEnv

# ── CONSTANTES ───────────────────────────────────────────────────────────────
MAX_HP            = 144.0
MAX_X             = 1400.0
STUN_MAX          = 200.0
TIMER_MAX         = 99.0
EPISODE_MIN_HP    = 100
CHARGE_REQUIRED   = 68
BOOM_FLIGHT_STEPS = 51
FK_YVEL_THR       = 256
LANDING_WINDOW    = 8

ELECTRIC_MAX_DIST   = 150
_MAX_BRIDGE_ERRORS  = 10

_HP_DEAD_THRESHOLD      = 0
_HP_DEAD_CONFIRM_FRAMES = 1
_HP_RESTORED_THRESHOLD  = 100

# ── BOSSES Y ARCADE ──────────────────────────────────────────────────────────
BOSS_IDS: frozenset = frozenset({8, 9, 10, 11})
BOSS_ORDER          = [10, 11, 9, 8]
ARCADE_FINAL_BOSS   = 8

ARCADE_CLEAR_REWARD = 200.0
BONUS_STAGE_FRAMES  = 60

CHAR_NAMES = {
    0:"Ryu",1:"Honda",2:"Blanka",3:"Guile",4:"Ken",5:"Chun-Li",
    6:"Zangief",7:"Dhalsim",8:"M.Bison",9:"Sagat",10:"Balrog",11:"Vega",
}

# ── ACTION SPACE ─────────────────────────────────────────────────────────────
SINGLE_FRAME_ACTIONS: List[List[int]] = [
    [0,0,0,0,0,0,0,0,0,0,0,0],  # 0  NOOP
    [1,0,0,0,0,0,0,0,0,0,0,0],  # 1  UP
    [0,1,0,0,0,0,0,0,0,0,0,0],  # 2  DOWN
    [0,0,1,0,0,0,0,0,0,0,0,0],  # 3  LEFT
    [0,0,0,1,0,0,0,0,0,0,0,0],  # 4  RIGHT
    [0,0,0,0,1,0,0,0,0,0,0,0],  # 5  JAB
    [0,0,0,0,0,1,0,0,0,0,0,0],  # 6  STRONG
    [0,0,0,0,0,0,1,0,0,0,0,0],  # 7  FIERCE
    [0,0,0,0,0,0,0,1,0,0,0,0],  # 8  SHORT
    [0,0,0,0,0,0,0,0,1,0,0,0],  # 9  FORWARD
    [0,0,0,0,0,0,0,0,0,1,0,0],  # 10 ROUNDHOUSE
    [0,1,0,0,1,0,0,0,0,0,0,0],  # 11 DOWN+JAB
    [0,1,0,0,0,0,1,0,0,0,0,0],  # 12 DOWN+FIERCE
    [0,1,0,0,0,0,0,1,0,0,0,0],  # 13 DOWN+SHORT
    [0,1,0,0,0,0,0,0,0,1,0,0],  # 14 DOWN+RH
]

# ── MACROS ───────────────────────────────────────────────────────────────────
_CHARGE     = [[0,0,1,0,0,0,0,0,0,0,0,0]] * 68
_NOOP1      = [[0,0,0,0,0,0,0,0,0,0,0,0]] * 1
_FWD_FIERCE = [[0,0,0,1,0,0,1,0,0,0,0,0]] * 4
_FWD_STRONG = [[0,0,0,1,0,1,0,0,0,0,0,0]] * 4
_FWD_JAB    = [[0,0,0,1,1,0,0,0,0,0,0,0]] * 4

MACRO_ROLLING_FIERCE: List[List[int]] = _CHARGE + _NOOP1 + _FWD_FIERCE
MACRO_ROLLING_STRONG: List[List[int]] = _CHARGE + _NOOP1 + _FWD_STRONG
MACRO_ROLLING_JAB:    List[List[int]] = _CHARGE + _NOOP1 + _FWD_JAB

_JAB  = [0,0,0,0,1,0,0,0,0,0,0,0]
_NO   = [0,0,0,0,0,0,0,0,0,0,0,0]
MACRO_ELECTRIC: List[List[int]] = [_JAB, _NO, _JAB, _NO, _JAB, _NO, _JAB]

_UP_F = 4
_AIR  = 18
_ATK  = 3

MACRO_JUMP_FWD_FIERCE: List[List[int]] = (
    [[1,0,0,1,0,0,0,0,0,0,0,0]] * _UP_F +
    [[0,0,0,1,0,0,0,0,0,0,0,0]] * _AIR +
    [[0,0,0,1,0,0,1,0,0,0,0,0]] * _ATK
)
MACRO_JUMP_FWD_FORWARD: List[List[int]] = (
    [[1,0,0,1,0,0,0,0,0,0,0,0]] * _UP_F +
    [[0,0,0,1,0,0,0,0,0,0,0,0]] * _AIR +
    [[0,0,0,1,0,0,0,0,1,0,0,0]] * _ATK
)
MACRO_JUMP_FWD_RH: List[List[int]] = (
    [[1,0,0,1,0,0,0,0,0,0,0,0]] * _UP_F +
    [[0,0,0,1,0,0,0,0,0,0,0,0]] * _AIR +
    [[0,0,0,1,0,0,0,0,0,1,0,0]] * _ATK
)
MACRO_JUMP_NEU_FIERCE: List[List[int]] = (
    [[1,0,0,0,0,0,0,0,0,0,0,0]] * _UP_F +
    [[0,0,0,0,0,0,0,0,0,0,0,0]] * _AIR +
    [[0,0,0,0,0,0,1,0,0,0,0,0]] * _ATK
)
MACRO_JUMP_BACK_FIERCE: List[List[int]] = (
    [[1,0,1,0,0,0,0,0,0,0,0,0]] * _UP_F +
    [[0,0,1,0,0,0,0,0,0,0,0,0]] * _AIR +
    [[0,0,1,0,0,0,1,0,0,0,0,0]] * _ATK
)
MACRO_JUMP_BACK_FORWARD: List[List[int]] = (
    [[1,0,1,0,0,0,0,0,0,0,0,0]] * _UP_F +
    [[0,0,1,0,0,0,0,0,0,0,0,0]] * _AIR +
    [[0,0,1,0,0,0,0,0,1,0,0,0]] * _ATK
)
MACRO_ROLLING_JUMP: List[List[int]] = [[0,0,0,1,0,0,1,0,0,0,0,0]]

MACROS: Dict[int, List[List[int]]] = {
    15: MACRO_ROLLING_FIERCE,
    16: MACRO_ROLLING_STRONG,
    17: MACRO_ROLLING_JAB,
    18: MACRO_ELECTRIC,
    19: MACRO_JUMP_FWD_FIERCE,
    20: MACRO_JUMP_FWD_FORWARD,
    21: MACRO_JUMP_FWD_RH,
    22: MACRO_JUMP_NEU_FIERCE,
    23: MACRO_JUMP_BACK_FIERCE,
    24: MACRO_JUMP_BACK_FORWARD,
    25: MACRO_ROLLING_JUMP,
}

FLIP_ACTIONS    = {15, 16, 17, 19, 20, 21, 23, 24, 25}
NOOP            = SINGLE_FRAME_ACTIONS[0]
N_ACTIONS       = 26
ROLLING_ACTIONS = {15, 16, 17}
ACTION_ROLLING  = 15
ACTION_ELECTRIC = 18

_CL1_N_ACTIONS  = 7
_CL1_ACTION_MAP = [0, 3, 4, 15, 16, 17, 18]  # CL1_idx → real_action_idx


def fk_phase_value(anim: int, p2_airborne: bool) -> float:
    if not p2_airborne: return 0.0
    if anim == 0x0C:    return 0.2
    if anim == 0x02:    return 0.4
    if anim == 0x00:    return 0.6
    if anim == 0x04:    return 0.8
    return 0.1


class BlankaEnv(gym.Env):
    metadata = {"render_modes": ["human"]}

    def __init__(self, instance_id: int = 0, max_steps: int = 50_000,
                 render_mode=None, registry: Optional[RivalRegistry] = None,
                 cl1_mode: bool = True):
        """
        cl1_mode=True  → Fase 1 / CL-1: 7 acciones
        cl1_mode=False → Fase 2 completo: 26 acciones

        max_steps: IGNORADO desde v5.10. Se mantiene como parámetro por
        compatibilidad con el train script, pero el episodio ya NO termina
        por pasos. Solo termina por arcade clear o crash de bridge.
        """
        super().__init__()
        self.instance_id  = instance_id
        self.MAX_STEPS    = max_steps   # mantenido por compatibilidad, no usado
        self.render_mode  = render_mode
        self.registry     = registry
        self.bridge       = MAMEBridge(instance_id=instance_id)
        self._cl1_mode    = cl1_mode
        self._cid_candidate = 0xFF
        self._cid_candidate_count = 0

        self.observation_space = spaces.Box(-1.0, 1.0, shape=(30,), dtype=np.float32)

        if self._cl1_mode:
            self.action_space = spaces.Discrete(_CL1_N_ACTIONS)
        else:
            self.action_space = spaces.Discrete(N_ACTIONS)

        # Estado interno — reset() inicializa todo
        self._prev_p1_hp:   float = MAX_HP
        self._prev_p2_hp:   float = MAX_HP
        self._ep_step:      int   = 0
        self._ep_total_steps: int = 0   # v5.10: contador informativo sin límite
        self._last_action:  int   = 0
        self._last_p1_dir:  int   = 1
        self._current_rival: int  = 0xFF
        self._p1_hp_hist = deque(maxlen=5)
        self._p2_hp_hist = deque(maxlen=5)
        self._charge:       int   = 0
        self._boom_timer:   int   = BOOM_FLIGHT_STEPS
        self._boom_est_x:   float = 0.0
        self._p2_was_air:   bool  = False
        self._fk_land_steps: int  = 0
        self._gnd_steps:    int   = 0
        self._macro_active: bool  = False
        self._macro_seq:    List[List[int]] = []
        self._macro_buf:    int   = 0
        self._ep_p1_dmg:    float = 0.0
        self._ep_p2_dmg:    float = 0.0
        self._soft_fails:   int   = 0
        self._MAX_SF:       int   = 3
        self._bridge_error_count: int = 0
        self._p1_was_air:   bool  = False
        self._p1_land_steps: int  = 0
        self._jump_back_charge: int = 0
        self._rolling_jump_rdy: bool = False
        self._won_round:    bool  = False
        self._rolling_count:  int = 0
        self._rolling_hits:   int = 0
        self._electric_count: int = 0
        self._electric_hits:  int = 0
        self._arcade_rival_seq: List[int] = []
        self._reached_bonus:    bool  = False
        self._bonus_frames:     int   = 0
        self._prev_rival:       int   = 0xFF
        self._ep_wins:          int   = 0
        self._out_of_combat_frames: int   = 0
        self._combat_p1_dmg:       float  = 0.0
        self._combat_p2_dmg:       float  = 0.0
        self._combat_won:          bool   = False
        self._combat_timeout_win:  bool   = False
        self._rivals_defeated:     int    = 0
        self._bosses_reached:    Set[int] = set()
        self._arcade_cleared:    bool     = False
        self._arcade_just_cleared: bool   = False
        self._ep_round_wins:    int = 0
        self._ep_match_wins:    int = 0
        self._ep_rounds_played: int = 0
        self._ep_matches_played: int = 0
        self._prev_in_combat:   bool  = False
        self._first_combat_seen: bool = False
        self._p1_low_hp_frames:  int  = 0
        self._p2_low_hp_frames:  int  = 0
        self._round_state:       str  = "fighting"
        self._match_p1_wins:     int  = 0
        self._match_p2_wins:     int  = 0
        self._round_processed:   bool = False
        self._round_bonus_pending: float = 0.0
        self._macro_action_id:    int  = -1
        self._macro_just_started: bool = False

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
            p1hp / MAX_HP, p1x / MAX_X, float(p1air), p1dir,
            min(p1stn, STUN_MAX) / STUN_MAX,
            p2hp / MAX_HP, p2x / MAX_X, float(p2cr), float(p2air),
            min(p2stn, STUN_MAX) / STUN_MAX,
            dist / MAX_X, (p1x - p2x) / MAX_X, timer / TIMER_MAX,
            float(p1x < 150 or p1x > 1250),
            float(p2x < 150 or p2x > 1250),
            (p1hp - p2hp) / MAX_HP, d1, d2,
            fk_phase_value(p2anim, p2air),
            float(sb_active),
            (self._boom_est_x / MAX_X) if sb_active else 0.0,
            float(prj), boom_t_n, charge_n,
            float(self._gnd_steps > 30), float(p2his > 0),
            float(self._fk_land_steps > 0 and self._fk_land_steps <= 20),
            self._last_action / float(N_ACTIONS - 1),
            float(p1land or (self._p1_land_steps > 0 and self._p1_land_steps <= LANDING_WINDOW)),
            float(self._rolling_jump_rdy),
        ], dtype=np.float32)
        return np.clip(obs, -1.0, 1.0)

    # ── REWARD ───────────────────────────────────────────────────────────────
    def _calc_reward(self, p1hp: float, p2hp: float, st: Dict, action: int,
                     extra_bonus: float = 0.0) -> float:
        real_action = action
        if self._cl1_mode and 0 <= action < _CL1_N_ACTIONS:
            real_action = _CL1_ACTION_MAP[action]

        dp1 = max(0.0, self._prev_p1_hp - p1hp)
        dp2 = max(0.0, self._prev_p2_hp - p2hp)

        p1x  = float(st.get("p1_x", 700.0))
        p2x  = float(st.get("p2_x", 700.0))
        p1a  = bool(st.get("p1_airborne", False))
        dist = abs(p1x - p2x)

        r = extra_bonus

        # ── Daño básico ──────────────────────────────────────────────────────
        r += dp2 * 8.0
        r -= dp1 * 5.0

        # ── Remate ───────────────────────────────────────────────────────────
        if p2hp <= 0 and dp2 > 0:
            r += 60.0
        elif p2hp < 40 and dp2 > 0:
            r += 35.0 if p2hp < 20 else 18.0
        elif p2hp < 70 and dp2 > 0:
            r += 8.0

        # ── P2 Danger Zone ───────────────────────────────────────────────────
        p2hp_frac = p2hp / MAX_HP
        if p2hp_frac < 0.65:
            r += (0.65 - p2hp_frac) / 0.65 * 3.0

        # ── Rolling Attack (v5.8 timing fix) ─────────────────────────────────
        if real_action in ROLLING_ACTIONS:
            in_fk_window = 0 < self._fk_land_steps <= 20
            if in_fk_window:
                r += 9.0
            elif dist < 150 or dist > 720:
                r -= 1.5
        elif self._macro_action_id in ROLLING_ACTIONS and dp2 > 0:
            in_fk_window = 0 < self._fk_land_steps <= 20
            good_dist    = 180 <= dist <= 650
            if in_fk_window:
                r += 38.0
            else:
                r += 32.0 if good_dist else 22.0

        # ── Electricidad (v5.8 timing fix) ───────────────────────────────────
        if real_action == ACTION_ELECTRIC:
            if dp2 > 0:
                r += 14.0 if dist < ELECTRIC_MAX_DIST else 7.0
            else:
                r -= 3.0 if dist > ELECTRIC_MAX_DIST else 1.2
        elif self._macro_action_id == ACTION_ELECTRIC and dp2 > 0:
            r += 14.0 if dist < ELECTRIC_MAX_DIST else 7.0

        # ── Bonus de carga back ───────────────────────────────────────────────
        back = 3 if self._last_p1_dir == 1 else 4
        if real_action == back or real_action in (23, 24):
            r += 0.20

        # ── Penalización inactividad ─────────────────────────────────────────
        dp1_raw = max(0.0, self._prev_p1_hp - p1hp)
        dp2_raw = max(0.0, self._prev_p2_hp - p2hp)
        if self._ep_step > 60 and dp2_raw == 0 and dp1_raw == 0:
            r -= 3.0

        # ── Penalización por alejarse ─────────────────────────────────────────
        if dist > 700 and dp2_raw == 0:
            r -= 0.05

        # ── Rival en esquina ─────────────────────────────────────────────────
        if p2x < 120 or p2x > 1280:
            r += 0.3

        # ── Rolling Jump ─────────────────────────────────────────────────────
        if real_action == 25:
            in_window = self._p1_land_steps > 0 and self._p1_land_steps <= LANDING_WINDOW
            if in_window and dp2 > 0:
                r += 28.0
            elif in_window:
                r += 7.0
            else:
                r -= 4.0

        # ── Saltos con ataque ────────────────────────────────────────────────
        if real_action in (19, 20, 21, 22, 23, 24):
            if dp2 > 0:
                r += 7.0
                if 140 <= dist <= 520:
                    r += 5.0
            elif p1a and dp1 > 0:
                r -= 4.0

        # ── Arcade clear ─────────────────────────────────────────────────────
        if self._arcade_just_cleared:
            r += ARCADE_CLEAR_REWARD
            self._arcade_just_cleared = False

        return float(r)

    # ── MACRO ENGINE ─────────────────────────────────────────────────────────
    def _resolve(self, action: int) -> List[int]:
        self._macro_just_started = False

        if self._cl1_mode:
            if 0 <= action < _CL1_N_ACTIONS:
                action = _CL1_ACTION_MAP[action]
            else:
                return NOOP

        if self._macro_active:
            if self._macro_seq:
                return self._macro_seq.pop(0)
            self._macro_active    = False
            self._macro_action_id = -1
            self._macro_buf = 0

        if self._macro_buf > 0:
            self._macro_buf -= 1
            return NOOP

        if action in MACROS:
            if action == 25:
                in_window = self._p1_land_steps > 0 and self._p1_land_steps <= LANDING_WINDOW
                if not in_window:
                    return NOOP

            seq = [list(f) for f in MACROS[action]]

            if action in FLIP_ACTIONS and self._last_p1_dir == 0:
                seq = [[f[0], f[1], f[3], f[2]] + f[4:] for f in seq]

            self._macro_active       = True
            self._macro_action_id    = action
            self._macro_just_started = True
            self._macro_seq    = seq
            if self._macro_seq:
                return self._macro_seq.pop(0)
            self._macro_active    = False
            self._macro_action_id = -1
            return NOOP

        if action < len(SINGLE_FRAME_ACTIONS):
            return list(SINGLE_FRAME_ACTIONS[action])

        return NOOP

    def _update_charge(self, action: int):
        real_action = action
        if self._cl1_mode and 0 <= action < _CL1_N_ACTIONS:
            real_action = _CL1_ACTION_MAP[action]

        if real_action in ROLLING_ACTIONS or real_action == 25:
            self._charge = 0
            return
        back = 3 if self._last_p1_dir == 1 else 4
        if real_action == back or real_action in (23, 24):
            self._charge = min(self._charge + 1, CHARGE_REQUIRED)
        else:
            self._charge = max(0, self._charge - 1)

    def _update_internals(self, st: Dict):
        p2a  = bool(st.get("p2_airborne", False))
        proj = bool(st.get("boom_slot_active", False))
        p2x  = float(st.get("p2_x", 700.0))
        cid  = int(st.get("p2_char", 0xFF))

        p1a          = bool(st.get("p1_airborne", False))
        p1land_frame = bool(st.get("p1_landing_this_frame", False))
        self._last_p1_dir = int(st.get("p1_dir", 1))

        back_jump = self._last_action in (23, 24)
        back_dir  = (self._last_action == 3 and self._last_p1_dir == 1) or \
                    (self._last_action == 4 and self._last_p1_dir == 0)
        back_held = back_jump or back_dir

        if p1a:
            if back_held:
                self._jump_back_charge = min(
                    self._jump_back_charge + 1, CHARGE_REQUIRED + 10)
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

        if self._p2_was_air and not p2a:
            self._fk_land_steps = 1
        elif self._fk_land_steps > 0:
            self._fk_land_steps += 1
            if self._fk_land_steps > 30:
                self._fk_land_steps = 0

        self._p2_was_air = p2a
        self._gnd_steps  = 0 if p2a else min(self._gnd_steps + 1, 60)

        if cid <= 11:
            if cid != self._prev_rival and self._prev_rival != 0xFF:
                if cid not in self._arcade_rival_seq:
                    self._arcade_rival_seq.append(cid)
                # Log de cambio de rival
                prev_name = CHAR_NAMES.get(self._prev_rival, f"ID_{self._prev_rival}")
                new_name  = CHAR_NAMES.get(cid, f"ID_{cid}")
                print(
                    f"[BlankaEnv#{self.instance_id}] 🔄 Rival cambiado:"
                    f" {prev_name} → Blanka vs {new_name} [cid={cid}]"
                )

                if cid in BOSS_IDS and cid not in self._bosses_reached:
                    self._bosses_reached.add(cid)
                    bname = CHAR_NAMES.get(cid, f"ID_{cid}")
                    print(
                        f"[BlankaEnv#{self.instance_id}] "
                        f"⚔️  BOSS ALCANZADO: {bname} "
                        f"| step={self._ep_step} "
                        f"| bosses={len(self._bosses_reached)}/4"
                    )

            self._prev_rival = cid
            self._bonus_frames = 0
            self._current_rival = cid
            self._current_rival_name = CHAR_NAMES.get(cid, f"Rival_{cid}")

        else:
            # cid > 11 o 0xFF — puede ser Bonus Stage o un fallo de lectura RAM
            self._bonus_frames += 1
            if self._bonus_frames == BONUS_STAGE_FRAMES:
                if not self._reached_bonus:
                    print(
                        f"[BlankaEnv#{self.instance_id}] "
                        f"⭐ BONUS STAGE DETECTADO | step={self._ep_step}"
                    )
                self._reached_bonus = True
            elif self._bonus_frames == 1:
                # Primera vez que cid es inválido — loguear para diagnóstico
                print(
                    f"[BlankaEnv#{self.instance_id}] ⚠️  CID INVÁLIDO:"
                    f" p2_char={cid} ({cid:#04x}) | prev_rival={self._prev_rival}"
                    f" | step={self._ep_step} — posible fallo lectura RAM o transición"
                )

            self._current_rival = cid
            self._current_rival_name = "Bonus Stage"

    # ── TRACKING DE RONDAS ────────────────────────────────────────────────────
    def _update_round_tracking(self, p1hp: float, p2hp: float, cid: int,
                               in_combat: bool) -> Tuple[bool, bool, float]:
        round_won_this_step = False
        match_won_this_step = False
        extra_reward        = 0.0

        if not in_combat:
            self._p1_low_hp_frames = 0
            self._p2_low_hp_frames = 0
            if (p1hp >= _HP_RESTORED_THRESHOLD and p2hp >= _HP_RESTORED_THRESHOLD
                    and self._round_state in ("p1_dead", "p2_dead")):
                self._round_state     = "fighting"
                self._round_processed = False
            return False, False, 0.0

        if (cid <= 11 and cid != self._current_rival
                and self._current_rival <= 11):
            self._match_p1_wins    = 0
            self._match_p2_wins    = 0
            self._round_state      = "fighting"
            self._round_processed  = False
            self._p1_low_hp_frames = 0
            self._p2_low_hp_frames = 0

        if (p1hp >= _HP_RESTORED_THRESHOLD and p2hp >= _HP_RESTORED_THRESHOLD
                and self._round_state in ("p1_dead", "p2_dead")):
            self._round_state      = "fighting"
            self._round_processed  = False
            self._p1_low_hp_frames = 0
            self._p2_low_hp_frames = 0

        if self._round_state != "fighting" or self._round_processed:
            return False, False, 0.0

        # ── P2 muriendo → Blanka gana la ronda ───────────────────────────────
        if p2hp <= _HP_DEAD_THRESHOLD:
            if not self._round_processed:
                self._p2_low_hp_frames += 1

                if self._p2_low_hp_frames >= _HP_DEAD_CONFIRM_FRAMES:
                    self._round_processed = True
                    self._round_state     = "p2_dead"
                    self._match_p1_wins  += 1
                    self._ep_round_wins  += 1
                    self._ep_rounds_played += 1
                    round_won_this_step   = True

                    rname = CHAR_NAMES.get(cid, f"ID_{cid:#04x}")
                    extra_reward += 100.0

                    print(
                        f"[BlankaEnv#{self.instance_id}] ✅ Round GANADO"
                        f" Blanka vs {rname}"
                        f" | Marcador: {self._match_p1_wins}-{self._match_p2_wins}"
                        f" [cid={cid}]"
                    )

                    if self._match_p1_wins >= 2:
                        self._ep_match_wins  += 1
                        self._ep_matches_played += 1
                        match_won_this_step   = True
                        extra_reward         += 200.0
                        self._rivals_defeated += 1

                        is_boss = " [BOSS]" if cid in BOSS_IDS else ""
                        print(
                            f"[BlankaEnv#{self.instance_id}] 🏆 MATCH GANADO"
                            f" Blanka vs {rname}"
                            f" | {self._match_p1_wins}-{self._match_p2_wins}{is_boss}"
                            f" | Total rivales: {self._rivals_defeated}"
                            f" [cid={cid}]"
                        )

                        if cid == ARCADE_FINAL_BOSS:
                            self._arcade_cleared      = True
                            self._arcade_just_cleared = True
                            print(f"[BlankaEnv#{self.instance_id}] 🎮 *** ARCADE CLEARED *** 🎮")

                        self._match_p1_wins = 0
                        self._match_p2_wins = 0
        else:
            self._round_processed = False
            self._p2_low_hp_frames = 0

        # ── P1 muriendo → Blanka pierde la ronda ─────────────────────────────
        if p1hp <= _HP_DEAD_THRESHOLD and not self._round_processed:
            self._p1_low_hp_frames += 1
            if self._p1_low_hp_frames >= _HP_DEAD_CONFIRM_FRAMES:
                self._round_processed  = True
                self._round_state      = "p1_dead"
                self._ep_rounds_played += 1
                self._match_p2_wins    += 1
                extra_reward           -= 50.0
                rname_lost = CHAR_NAMES.get(cid, f"ID_{cid:#04x}")
                print(
                    f"[BlankaEnv#{self.instance_id}] ❌ Round PERDIDO"
                    f" Blanka vs {rname_lost}"
                    f" | Marcador: {self._match_p1_wins}-{self._match_p2_wins}"
                    f" [cid={cid}]"
                )

                if self._match_p2_wins >= 2:
                    self._ep_matches_played += 1
                    rname = CHAR_NAMES.get(cid, f"ID_{cid:#04x}")
                    print(
                        f"[BlankaEnv#{self.instance_id}] 💀 MATCH PERDIDO"
                        f" Blanka vs {rname}"
                        f" | {self._match_p1_wins}-2 | Esperando Continue..."
                        f" [cid={cid}]"
                    )
                    self._flush_combat_to_registry(timeout_win=False)
                    self._match_p1_wins = 0
                    self._match_p2_wins = 0
        else:
            if not self._round_processed:
                self._p1_low_hp_frames = 0

        return round_won_this_step, match_won_this_step, extra_reward

    # ── REGISTRO DE COMBATE ───────────────────────────────────────────────────
    def _flush_combat_to_registry(self, timeout_win: bool = False):
        if self.registry and self._current_rival <= 11:
            self.registry.record_episode(
                self._current_rival,
                self._combat_won,
                self._combat_p1_dmg,
                self._combat_p2_dmg,
                extras={
                    "arcade_sequence":  list(self._arcade_rival_seq),
                    "reached_bonus":    self._reached_bonus,
                    "round_wins":       self._ep_wins,
                    "timeout_win":      timeout_win,
                    "is_boss":          self._current_rival in BOSS_IDS,
                    "bosses_reached":   sorted(list(self._bosses_reached)),
                    "arcade_cleared":   self._arcade_cleared,
                }
            )
        self._combat_p1_dmg      = 0.0
        self._combat_p2_dmg      = 0.0
        self._combat_won         = False
        self._combat_timeout_win = False

    # ── INFO DICT ─────────────────────────────────────────────────────────────
    def _build_info(self, st, p1hp, p2hp, action, action_real, p2_just_died, won, 
                    round_won_this_step, match_won_this_step, in_combat, 
                    timeout_win, terminated, truncated):
    
        # Extraemos el game_state de forma segura
        gst = st.get("game_state", 0) if st else 0
        
        # Variable local para saber si el episodio terminó (reemplaza a ep_done)
        is_done = terminated or truncated

        return {
            "game_state": gst,
            "p1_hp": p1hp,
            "p2_hp": p2hp,
            "won": won,
            "timeout_win": timeout_win,
            "p2_just_died": p2_just_died,
            "step": self._ep_step,
            "action": action,
            "action_real": action_real,
            "macro_just_started": self._macro_just_started,
            "rival": self._current_rival, # <--- Corregido de _current_rival_id
            "boom_t": self._boom_timer,
            "fk_land": self._fk_land_steps,
            "charge": self._charge,
            "rolling_jump": int(action == 25),
            "p1_land": self._p1_land_steps,
            "rolling_jump_rdy": int(self._rolling_jump_rdy),
            "arcade_sequence": list(self._arcade_rival_seq),
            "reached_bonus": self._reached_bonus,
            "round_wins": self._ep_wins,
            "rivals_defeated": self._rivals_defeated,
            "out_of_combat_frames": self._out_of_combat_frames,
            "is_boss": self._current_rival in BOSS_IDS,
            "in_bonus_stage": self._bonus_frames > 0,
            "bosses_reached_count": len(self._bosses_reached),
            "bosses_reached_ids": sorted(list(self._bosses_reached)),
            "arcade_cleared": self._arcade_cleared,
            "in_combat": in_combat,
            "ep_p2_dmg": self._ep_p2_dmg,
            "ep_p1_dmg": self._ep_p1_dmg,
            "match_p1_wins": self._match_p1_wins,
            "match_p2_wins": self._match_p2_wins,
            "match_over": False,
            "round_won_this_step": round_won_this_step,
            "match_won_this_step": match_won_this_step,
            "ep_round_wins": self._ep_round_wins,
            "ep_match_wins": self._ep_match_wins,
            "ep_rounds_played": self._ep_rounds_played,
            "ep_matches_played": self._ep_matches_played,
            "ep_round_win_rate": (
                self._ep_round_wins / max(self._ep_rounds_played, 1)
                if is_done else 0.0
            ),
            "ep_match_win_rate": (
                self._ep_match_wins / max(self._ep_matches_played, 1)
                if is_done else 0.0
            ),
        }

    # ── RESET ─────────────────────────────────────────────────────────────────
    def reset(self, *, seed=None, options=None) -> Tuple[np.ndarray, Dict]:
        """
        v5.10 — reset() NO envía inputs al bridge.
        
        El episodio anterior terminó por arcade clear o crash del bridge.
        En ambos casos MAME está en un estado conocido (pantalla de resultados
        o muerto). El Lua gestionará automáticamente el regreso al combate
        (insertar coin, seleccionar Blanka, etc.).
        
        Este método simplemente espera leyendo el estado SIN enviar inputs,
        hasta que el Lua reporte in_combat=True con ambos HP válidos.
        No se envían NOOPs que puedan interferir con la FSM del Lua.
        """
        super().reset(seed=seed)

        self._prev_p1_hp    = MAX_HP
        self._prev_p2_hp    = MAX_HP
        self._ep_step       = 0
        # _ep_total_steps NO se resetea — es un contador global del lifetime
        self._last_action   = 0
        self._last_p1_dir   = 1
        self._charge        = 0
        self._boom_timer    = BOOM_FLIGHT_STEPS
        self._boom_est_x    = 0.0
        self._p2_was_air    = False
        self._fk_land_steps = 0
        self._gnd_steps     = 0
        self._macro_active  = False
        self._macro_seq     = []
        self._macro_buf     = 0
        self._macro_action_id    = -1
        self._macro_just_started = False
        self._ep_p1_dmg     = 0.0
        self._ep_p2_dmg     = 0.0
        self._p1_hp_hist.clear()
        self._p2_hp_hist.clear()
        self._bridge_error_count = 0
        self._p1_was_air    = False
        self._p1_land_steps = 0
        self._jump_back_charge  = 0
        self._rolling_jump_rdy  = False
        self._ep_wins       = 0
        self._won_round     = False
        self._rolling_count = 0
        self._rolling_hits  = 0
        self._electric_count= 0
        self._electric_hits = 0
        self._arcade_rival_seq  = []
        self._reached_bonus = False
        self._bonus_frames  = 0
        self._prev_rival    = 0xFF
        self._current_rival = 0xFF
        self._rivals_defeated     = 0
        self._out_of_combat_frames = 0
        self._combat_p1_dmg  = 0.0
        self._combat_p2_dmg  = 0.0
        self._combat_won     = False
        self._combat_timeout_win = False
        self._bosses_reached      = set()
        self._arcade_cleared      = False
        self._arcade_just_cleared = False
        self._prev_in_combat      = False
        self._first_combat_seen   = False
        self._ep_round_wins     = 0
        self._ep_match_wins     = 0
        self._ep_rounds_played  = 0
        self._ep_matches_played = 0
        self._p1_low_hp_frames  = 0
        self._p2_low_hp_frames  = 0
        self._round_state       = "fighting"
        self._match_p1_wins     = 0
        self._match_p2_wins     = 0
        self._round_processed   = False
        self._round_bonus_pending = 0.0

        mode_str = "CL-1 (7 acciones)" if self._cl1_mode else "COMPLETO (26 acciones)"
        print(f"[BlankaEnv#{self.instance_id}] Reset... [{mode_str}]")

        # ── v5.11: esperar combate SIN enviar inputs ──────────────────────────
        # Solo leemos el estado. El bridge lee state_N.txt que escribe el Lua.
        # Nunca escribimos mame_input_N.txt durante el reset — así el Lua
        # sigue ejecutando su FSM (menus, char select, etc.) sin interferencia.
        # Cuando el Lua reporta in_combat=True con ambos HP >= EPISODE_MIN_HP,
        # el combate está listo y arrancamos el episodio.
        #
        # v5.11 FIX: Añadido logging periódico cada 10s para diagnosticar
        # cuelgues en reset. Si el Lua no escribe estado (claim race condition)
        # o está en menús muy largo, el log mostrará el estado exacto.
        st = None
        valid_st = None
        t_reset_start = time.time()
        deadline = t_reset_start + 120.0  # 2 min max (arcade clear puede tardar)
        _last_log = t_reset_start
        while time.time() < deadline:
            # Leer estado SIN enviar inputs (paso pasivo)
            st = self.bridge._parse_state_file()
            now = time.time()

            # Log diagnóstico cada 10s para detectar cuelgues
            if now - _last_log >= 10.0:
                _last_log = now
                elapsed_r = now - t_reset_start
                if st:
                    print(f"[BlankaEnv#{self.instance_id}] reset() esperando... "
                          f"{elapsed_r:.0f}s | in_combat={st.get('in_combat')} "
                          f"p1_hp={st.get('p1_hp',0)} p2_hp={st.get('p2_hp',0)} "
                          f"frame={st.get('frame','?')} gs={st.get('game_state','?')}")
                else:
                    print(f"[BlankaEnv#{self.instance_id}] reset() esperando... "
                          f"{elapsed_r:.0f}s | state_file=None (Lua no escribe todavia)")

            if st:
                in_combat = bool(st.get("in_combat", False))
                p1 = float(st.get("p1_hp", 0))
                p2 = float(st.get("p2_hp", 0))
                if in_combat and p1 >= EPISODE_MIN_HP and p2 >= EPISODE_MIN_HP:
                    valid_st = st
                    break
            time.sleep(0.05)

        if valid_st is None:
            print(f"[BlankaEnv#{self.instance_id}] WARNING: timeout en reset(), obs cero")
            return np.zeros(30, dtype=np.float32), self._empty_info()

        st = valid_st
        p1  = float(st.get("p1_hp", MAX_HP))
        p2  = float(st.get("p2_hp", MAX_HP))
        if p1 > MAX_HP: p1 = MAX_HP
        if p2 > MAX_HP: p2 = MAX_HP
        cid = int(st.get("p2_char", 0xFF))
        self._last_p1_dir = int(st.get("p1_dir", 1))

        self._prev_p1_hp     = p1
        self._prev_p2_hp     = p2
        self._current_rival  = cid if cid <= 11 else 0xFF
        self._prev_in_combat = True
        self._first_combat_seen = True

        if cid <= 11:
            self._arcade_rival_seq = [cid]
            self._prev_rival = cid
            if cid in BOSS_IDS:
                self._bosses_reached.add(cid)

        print(
            f"[BlankaEnv#{self.instance_id}] "
            f"Blanka vs {CHAR_NAMES.get(cid,'ID_'+str(cid))} "
            f"[cid={cid}] "
            f"| P1={p1:.0f} P2={p2:.0f} "
            f"dir={'→' if self._last_p1_dir==1 else '←'}"
            + (" [BOSS]" if cid in BOSS_IDS else "")
        )

        # Al final de def reset(self, ...):
        return self._get_obs(st), self._build_info(
            st=st,
            p1hp=p1, p2hp=p2, action=0, action_real=0,
            p2_just_died=False, won=False,
            round_won_this_step=False, match_won_this_step=False,
            in_combat=True, timeout_win=False,
            terminated=False, truncated=False
        )

    def _empty_info(self) -> Dict:
        return {
            "rival": 0xFF, "p1_dir": 1, "arcade_sequence": [],
            "reached_bonus": False, "bosses_reached_count": 0,
            "bosses_reached_ids": [], "arcade_cleared": False,
            "is_boss": False, "in_bonus_stage": False,
            "ep_round_wins": 0, "ep_match_wins": 0,
            "ep_rounds_played": 0, "ep_matches_played": 0,
            "ep_round_win_rate": None, "ep_match_win_rate": None,
            "round_won_this_step": False, "match_won_this_step": False,
            "out_of_combat_frames": 0, "in_combat": False,
            "match_p1_wins": 0, "match_p2_wins": 0, "match_over": False,
        }

    # ── STEP ─────────────────────────────────────────────────────────────────
    def step(self, action: int):
        self._ep_step        += 1
        self._ep_total_steps += 1
        action = int(action)

        # 1. Ejecutar acción en el Bridge
        raw_input = self._resolve(action)
        st = self.bridge.step(raw_input)

        # ── Manejo de Error de Bridge (Crash o Desconexión) ───────────────────
        if st is None:
            self._bridge_error_count += 1
            if self._bridge_error_count >= _MAX_BRIDGE_ERRORS:
                print(f"[BlankaEnv#{self.instance_id}] ❌ CRASH DEL BRIDGE — Truncando episodio")
                # Al truncar por error, devolvemos info mínima
                return self._get_obs(None), -1.0, False, True, {"bridge_error": True}
            
            # Reintento: usamos el último estado conocido
            return self._get_obs(self.bridge._last_state), 0.0, False, False, {"bridge_retry": True}
        
        # Si el bridge responde, reseteamos contador de errores
        self._bridge_error_count = 0

        # 2. Actualizar estado interno (Cargas de Blanka)
        self._update_charge(action)

        # 3. Lectura y Validación de HP (Evitar valores basura de RAM)
        p1hp = float(st.get("p1_hp", 144))
        p2hp = float(st.get("p2_hp", 144))
        if p1hp > 144: p1hp = self._prev_p1_hp if self._prev_p1_hp <= 144 else 144
        if p2hp > 144: p2hp = self._prev_p2_hp if self._prev_p2_hp <= 144 else 144

        # 4. Filtro de ID de Rival (Anti-Flicker)
        raw_cid   = int(st.get("p2_char", 0xFF))
        in_combat = bool(st.get("in_combat", False))
        
        # Detectar cambio de rival (usando el nombre de variable corregido)
        if in_combat and p1hp > 10 and p2hp > 10:
            if raw_cid != self._current_rival and raw_cid <= 11:
                if not hasattr(self, '_cid_candidate'):
                    self._cid_candidate = raw_cid
                    self._cid_candidate_count = 0
                if raw_cid == self._cid_candidate:
                    self._cid_candidate_count += 1
                else:
                    self._cid_candidate = raw_cid
                    self._cid_candidate_count = 1
                
                if self._cid_candidate_count >= 8:
                    self._cid_candidate_count = 0
                    # aquí va el código actual de cambio de rival
                    g_state = st.get("game_state", 0)
                    print(f"[BlankaEnv#{self.instance_id}] 🔄 NUEVO RIVAL: ...")
                    self._current_rival = raw_cid
                    if raw_cid not in self._arcade_rival_seq:
                        self._arcade_rival_seq.append(raw_cid)
            else:
                self._cid_candidate = raw_cid
                self._cid_candidate_count = 0
        cid = self._current_rival

        # 5. Resolver acción real (CL1 vs Fase 2)
        if self._cl1_mode:
            clamped_action = min(action, len(_CL1_ACTION_MAP) - 1)
            _action_real = _CL1_ACTION_MAP[clamped_action]
        else:
            _action_real = action

        # Históricos para reward shaping
        self._p1_hp_hist.append(p1hp)
        self._p2_hp_hist.append(p2hp)

        # Variables de control de Gymnasium
        terminated = False
        truncated = False # Por defecto es False, ya que quitamos el límite de steps

        # ── CASO A: FUERA DE COMBATE (Menús, intros, transición) ──────────────
        if not in_combat:
            self._out_of_combat_frames += 1
            self._update_internals(st)
            
            # Si veníamos de combate y alguien murió, procesar último flanco
            if self._prev_in_combat and (p1hp <= 2 or p2hp <= 2):
                _, _, _er = self._update_round_tracking(p1hp, p2hp, cid, in_combat=True)
                if _er: self._round_bonus_pending += _er

            self._update_round_tracking(p1hp, p2hp, cid, in_combat=False)
            self._prev_in_combat = False
            
            terminated = self._arcade_cleared
            if terminated: self._finalize_episode()

            return self._get_obs(st), 0.0, terminated, truncated, self._build_info(
                st=st, p1hp=p1hp, p2hp=p2hp, action=action, action_real=_action_real, 
                p2_just_died=False, won=(self._ep_match_wins > 0),
                round_won_this_step=False, match_won_this_step=False,
                in_combat=False, timeout_win=False, 
                terminated=terminated, truncated=truncated
            )

        # ── CASO B: EN COMBATE (Acción real) ──────────────────────────────────
        self._out_of_combat_frames = 0
        dp1 = max(0.0, self._prev_p1_hp - p1hp)
        dp2 = max(0.0, self._prev_p2_hp - p2hp)
        
        self._ep_p1_dmg += dp1
        self._ep_p2_dmg += dp2
        self._combat_p1_dmg += dp1
        self._combat_p2_dmg += dp2

        p2_just_died = (p2hp <= 0 and dp2 > 0)
        
        # Seguimiento de rondas y recompensas por KO
        round_won, match_won, extra_reward = self._update_round_tracking(p1hp, p2hp, cid, in_combat=True)
        
        # Aplicar bonus pendientes de la transición
        extra_reward += self._round_bonus_pending
        self._round_bonus_pending = 0.0
        
        self._update_internals(st)
        self._prev_in_combat = True

        terminated = self._arcade_cleared
        if terminated: self._finalize_episode()

        # Cálculo de recompensa (Reward Shaping)
        reward = self._calc_reward(p1hp, p2hp, st, action, extra_bonus=extra_reward)
        
        # Actualizar previos para el siguiente step
        self._prev_p1_hp, self._prev_p2_hp = p1hp, p2hp
        self._last_action = _action_real

        return self._get_obs(st), reward, terminated, truncated, self._build_info(
            st=st,
            p1hp=p1hp, p2hp=p2hp, action=action, action_real=_action_real,
            p2_just_died=p2_just_died, won=(self._ep_match_wins > 0),
            round_won_this_step=round_won, match_won_this_step=match_won, 
            in_combat=True, timeout_win=False, 
            terminated=terminated, truncated=truncated
        )
    
    def _finalize_episode(self, timeout_win: bool = False):
        """Llamado al terminar el episodio (arcade clear o emergencia)."""
        if self._combat_p1_dmg > 0 or self._combat_p2_dmg > 0:
            self._flush_combat_to_registry(timeout_win=timeout_win)

        rival_seq_names = [
            f"{CHAR_NAMES.get(c, f'ID_{c}')}(cid={c})"
            for c in self._arcade_rival_seq
        ]
        tag = "ARCADE CLEAR ✅" if self._arcade_cleared else "FIN EMERGENCIA"
        print(
            f"[BlankaEnv#{self.instance_id}] {tag} "
            f"| rivales={self._rivals_defeated} "
            f"| secuencia={rival_seq_names} "
            f"| round_wr={self._ep_round_wins}/{self._ep_rounds_played} "
            f"| match_wr={self._ep_match_wins}/{self._ep_matches_played} "
            f"| steps={self._ep_step}"
        )

    def render(self): pass

    def close(self):
        try: self.bridge.disconnect()
        except Exception: pass
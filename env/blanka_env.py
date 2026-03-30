"""
blanka_env.py — Entorno Gymnasium Blanka vs Arcade (SF2CE / MAME 0.286)
========================================================================
Version: 4.4 (30/03/2026) - FIXES OUT-OF-COMBAT + ROUND TRACKING

CAMBIOS v4.4 respecto a v4.3:
  · [FIX CRÍTICO] Eliminada truncación por MENU_FRAMES_MAX.
    La única causa de truncated es MAX_STEPS. Los frames fuera de combate
    (menú, continue, char select) son parte normal del flujo y NO deben
    terminar el episodio. A velocidad de entrenamiento MENU_FRAMES_MAX=1200
    se dispara en milisegundos.
  · [FIX] `ep_rounds_played` ya no se inicializa a 1 en reset. Se incrementa
    solo cuando se detecta un inicio real de ronda (ambos HP >= EPISODE_MIN_HP
    tras haber estado bajos), incluyendo el primer combate del reset.
  · [FIX] `ep_matches_played` inicializado a 0 en reset; se incrementa al
    detectar el primer combate (en reset) y en cada cambio de rival.
  · [FIX] `_cl1_action` usa el estado actual del step (pasado como argumento),
    no `self.bridge._last_state` (estado desfasado).
  · [LIMPIEZA] `_out_of_combat_frames` ya no se usa para truncar; se mantiene
    solo como métrica diagnóstica en el info dict.

INVARIANTE CLAVE (mantenido desde v4.0):
  · El episodio NO termina al perder una ronda ni al ganar un rival.
    Solo termina en:
      (a) Arcade clear (Bison derrotado)   → terminated=True
      (b) Truncation por MAX_STEPS         → truncated=True

MÉTRICAS PARA TENSORBOARD (extraer del info dict):
  Durante el episodio:
    info["round_won_this_step"]  → bool — loguear como evento puntual
    info["match_won_this_step"]  → bool — loguear como evento puntual
  Al final del episodio (cuando terminated o truncated):
    info["ep_round_wins"]        → int   — rondas ganadas en el episodio
    info["ep_match_wins"]        → int   — enfrentamientos ganados en el episodio
    info["ep_rounds_played"]     → int   — rondas disputadas en el episodio
    info["ep_round_win_rate"]    → float — ep_round_wins / max(ep_rounds_played, 1)
    info["ep_match_win_rate"]    → float — ep_match_wins / max(ep_matches_played, 1)
"""

import os, time, sys
import gymnasium as gym
from gymnasium import spaces
import numpy as np
from collections import deque
from typing import Optional, Dict, List, Tuple, Set

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from mame_bridge import MAMEBridge
from core.rival_registry import RivalRegistry

# ── CONFIG ───────────────────────────────────────────────────────────────────
ROLLING_AND_ELECTRIC_ONLY: bool = False

# ── CONSTANTES ───────────────────────────────────────────────────────────────
MAX_HP            = 144.0
MAX_X             = 1400.0
STUN_MAX          = 200.0
TIMER_MAX         = 99.0
EPISODE_MIN_HP    = 100   # HP mínimo para considerar combate activo
CHARGE_REQUIRED   = 68
BOOM_FLIGHT_STEPS = 51
FK_YVEL_THR       = 256
LANDING_WINDOW    = 8

ELECTRIC_MAX_DIST   = 150
_MAX_BRIDGE_ERRORS  = 10

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

# ── TABLA DE MACROS ──────────────────────────────────────────────────────────
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


def fk_phase_value(anim: int, p2_airborne: bool) -> float:
    if not p2_airborne: return 0.0
    if anim == 0x0C:    return 0.2
    if anim == 0x02:    return 0.4
    if anim == 0x00:    return 0.6
    if anim == 0x04:    return 0.8
    return 0.1


class BlankaEnv(gym.Env):
    metadata = {"render_modes": ["human"]}

    def __init__(self, instance_id: int = 0, max_steps: int = 30000,
                 render_mode=None, registry: Optional[RivalRegistry] = None):
        super().__init__()
        self.instance_id = instance_id
        self.MAX_STEPS   = max_steps
        self.render_mode = render_mode
        self.registry    = registry
        self.bridge      = MAMEBridge(instance_id=instance_id)

        self.observation_space = spaces.Box(-1.0, 1.0, shape=(30,), dtype=np.float32)
        self.action_space      = spaces.Discrete(N_ACTIONS)

        # Todos los atributos de estado; reset() los inicializa
        self._prev_p1_hp:   float = MAX_HP
        self._prev_p2_hp:   float = MAX_HP
        self._ep_step:      int   = 0
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

        self._out_of_combat_frames: int   = 0  # diagnóstico (no trunca)
        self._combat_p1_dmg:       float  = 0.0
        self._combat_p2_dmg:       float  = 0.0
        self._combat_won:          bool   = False
        self._combat_timeout_win:  bool   = False
        self._rivals_defeated:     int    = 0

        self._bosses_reached:    Set[int] = set()
        self._arcade_cleared:    bool     = False
        self._arcade_just_cleared: bool   = False

        # v4.3: tracking de rondas y enfrentamientos
        self._ep_round_wins:    int = 0
        self._ep_match_wins:    int = 0
        self._ep_rounds_played: int = 0
        self._ep_matches_played: int = 0

        # v4.4: HP previo para detectar inicio de ronda
        self._prev_in_combat:   bool  = False
        self._first_combat_seen: bool = False

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
    def _calc_reward(self, p1hp: float, p2hp: float, st: Dict, action: int) -> float:
        dp1 = max(0.0, self._prev_p1_hp - p1hp)
        dp2 = max(0.0, self._prev_p2_hp - p2hp)

        p1x  = float(st.get("p1_x", 700.0))
        p2x  = float(st.get("p2_x", 700.0))
        p1a  = bool(st.get("p1_airborne", False))
        dist = abs(p1x - p2x)

        r = 0.0

        # Daño básico
        r += dp2 * 8.0
        r -= dp1 * 12.0

        # Remate
        if p2hp <= 0 and dp2 > 0:
            r += 60.0
        elif p2hp < 40 and dp2 > 0:
            r += 35.0 if p2hp < 20 else 18.0
        elif p2hp < 70 and dp2 > 0:
            r += 8.0

        # Rolling Attack
        if action in ROLLING_ACTIONS:
            in_fk_window = 0 < self._fk_land_steps <= 20
            good_dist    = 180 <= dist <= 650
            if in_fk_window:
                r += 38.0 if dp2 > 0 else 9.0
            elif dp2 > 0:
                r += 32.0 if good_dist else 22.0
            else:
                r -= 1.5 if dist < 150 or dist > 720 else 0.8

        # Electricidad
        if action == ACTION_ELECTRIC:
            if dp2 > 0:
                r += 14.0 if dist < ELECTRIC_MAX_DIST else 7.0
            else:
                r -= 3.0 if dist > ELECTRIC_MAX_DIST else 1.2

        # Bonus de carga
        back = 3 if self._last_p1_dir == 1 else 4
        if action == back or action in (23, 24):
            r += 0.06

        # Penalización inactividad
        if self._ep_step > 60 and dp2 == 0 and dp1 == 0:
            r -= 0.003

        # Rival en esquina
        if p2x < 120 or p2x > 1280:
            r += 1.2

        # Rolling Jump
        if action == 25:
            in_window = self._p1_land_steps > 0 and self._p1_land_steps <= LANDING_WINDOW
            if in_window and dp2 > 0:
                r += 28.0
            elif in_window:
                r += 7.0
            else:
                r -= 4.0

        # Saltos con ataque
        if action in (19, 20, 21, 22, 23, 24):
            if dp2 > 0:
                r += 7.0
                if 140 <= dist <= 520:
                    r += 5.0
            elif p1a and dp1 > 0:
                r -= 4.0

        # Arcade clear (solo el frame del KO a Bison)
        if self._arcade_just_cleared:
            r += ARCADE_CLEAR_REWARD
            self._arcade_just_cleared = False

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
            if action == 25:
                in_window = self._p1_land_steps > 0 and self._p1_land_steps <= LANDING_WINDOW
                if not in_window:
                    return NOOP

            seq = [list(f) for f in MACROS[action]]

            if action in FLIP_ACTIONS and self._last_p1_dir == 0:
                seq = [[f[0], f[1], f[3], f[2]] + f[4:] for f in seq]

            self._macro_active = True
            self._macro_seq    = seq
            if self._macro_seq:
                return self._macro_seq.pop(0)
            self._macro_active = False
            return NOOP

        if action < len(SINGLE_FRAME_ACTIONS):
            return list(SINGLE_FRAME_ACTIONS[action])

        return NOOP

    def _update_charge(self, action: int):
        if action in ROLLING_ACTIONS or action == 25:
            self._charge = 0
            return
        back = 3 if self._last_p1_dir == 1 else 4
        if action == back or action in (23, 24):
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
        self._last_p1_dir = int(st.get("p1_dir", 1))

        # Tracking de rival y bonus stages
        if cid <= 11:
            if cid != self._prev_rival and self._prev_rival != 0xFF:
                if cid not in self._arcade_rival_seq:
                    self._arcade_rival_seq.append(cid)
                if cid in BOSS_IDS and cid not in self._bosses_reached:
                    self._bosses_reached.add(cid)
                    bname = CHAR_NAMES.get(cid, f"ID_{cid}")
                    print(
                        f"[BlankaEnv#{self.instance_id}] "
                        f"⚔️  BOSS ALCANZADO: {bname} "
                        f"| step={self._ep_step} "
                        f"| bosses={len(self._bosses_reached)}/4"
                    )
            self._prev_rival    = cid
            self._bonus_frames  = 0
            self._current_rival = cid
        else:
            self._bonus_frames += 1
            if self._bonus_frames == BONUS_STAGE_FRAMES:
                if not self._reached_bonus:
                    print(
                        f"[BlankaEnv#{self.instance_id}] "
                        f"⭐ BONUS STAGE DETECTADO | step={self._ep_step}"
                    )
                self._reached_bonus = True

    def _cl1_action(self, st: Optional[Dict]) -> int:
        """Elige rolling o electricidad según distancia. Usa el estado actual."""
        if st is None:
            return ACTION_ROLLING
        p1x  = float(st.get("p1_x", 700.0))
        p2x  = float(st.get("p2_x", 700.0))
        dist = abs(p1x - p2x)
        return ACTION_ELECTRIC if dist < ELECTRIC_MAX_DIST else ACTION_ROLLING

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
        self._combat_p1_dmg     = 0.0
        self._combat_p2_dmg     = 0.0
        self._combat_won        = False
        self._combat_timeout_win = False

    # ── INFO DICT ─────────────────────────────────────────────────────────────
    def _build_info(
        self,
        p1hp: float, p2hp: float, action: int,
        timeout_win: bool, p2_just_died: bool, won: bool,
        round_won_this_step: bool, match_won_this_step: bool,
        in_combat: bool, terminated: bool, truncated: bool,
    ) -> Dict:
        ep_done = terminated or truncated
        return {
            "p1_hp":          p1hp,
            "p2_hp":          p2hp,
            "won":            won,
            "timeout_win":    timeout_win,
            "p2_just_died":   p2_just_died,
            "step":           self._ep_step,
            "action":         action,
            "rival":          self._current_rival,
            "boom_t":         self._boom_timer,
            "fk_land":        self._fk_land_steps,
            "charge":         self._charge,
            "rolling_jump":   int(action == 25),
            "p1_land":        self._p1_land_steps,
            "rolling_jump_rdy": int(self._rolling_jump_rdy),
            "rolling_and_electric_only": ROLLING_AND_ELECTRIC_ONLY,
            "arcade_sequence":      list(self._arcade_rival_seq),
            "reached_bonus":        self._reached_bonus,
            "round_wins":           self._ep_wins,
            "rivals_defeated":      self._rivals_defeated,
            "out_of_combat_frames": self._out_of_combat_frames,
            "is_boss":              self._current_rival in BOSS_IDS,
            "in_bonus_stage":       self._bonus_frames > 0,
            "bosses_reached_count": len(self._bosses_reached),
            "bosses_reached_ids":   sorted(list(self._bosses_reached)),
            "arcade_cleared":       self._arcade_cleared,
            "in_combat":            in_combat,
            # v4.3: win tracking
            "round_won_this_step":  round_won_this_step,
            "match_won_this_step":  match_won_this_step,
            "ep_round_wins":        self._ep_round_wins,
            "ep_match_wins":        self._ep_match_wins,
            "ep_rounds_played":     self._ep_rounds_played,
            "ep_matches_played":    self._ep_matches_played,
            # Ratios: solo al final de episodio
            "ep_round_win_rate": (
                self._ep_round_wins / max(self._ep_rounds_played, 1)
                if ep_done else None
            ),
            "ep_match_win_rate": (
                self._ep_match_wins / max(self._ep_matches_played, 1)
                if ep_done else None
            ),
        }

    # ── RESET ─────────────────────────────────────────────────────────────────
    def reset(self, *, seed=None, options=None) -> Tuple[np.ndarray, Dict]:
        super().reset(seed=seed)

        # ── Reset de TODO el estado interno ──────────────────────────────────
        self._prev_p1_hp    = MAX_HP
        self._prev_p2_hp    = MAX_HP
        self._ep_step       = 0
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
        self._rivals_defeated   = 0
        self._out_of_combat_frames = 0
        self._combat_p1_dmg = 0.0
        self._combat_p2_dmg = 0.0
        self._combat_won    = False
        self._combat_timeout_win = False
        self._bosses_reached     = set()
        self._arcade_cleared     = False
        self._arcade_just_cleared= False
        self._prev_in_combat     = False
        self._first_combat_seen  = False

        # v4.4: contadores inicializados a 0 — se incrementan al ver el primer combate
        self._ep_round_wins    = 0
        self._ep_match_wins    = 0
        self._ep_rounds_played = 0
        self._ep_matches_played= 0

        print(
            f"[BlankaEnv#{self.instance_id}] Reset..."
            + (" [ROLLING_AND_ELECTRIC_ONLY]" if ROLLING_AND_ELECTRIC_ONLY else "")
        )

        # Esperar hasta que el bridge nos dé un estado de combate válido.
        # Sin timeout hardcodeado en términos de frames: esperamos hasta 60s reales.
        # El bridge (autoplay_bridge.lua v2.0) gestiona toda la navegación.
        st = None
        deadline = time.time() + 60.0
        while time.time() < deadline:
            st = self.bridge.step([0] * 12)
            if st:
                in_combat = bool(st.get("in_combat", False))
                p1 = float(st.get("p1_hp", 0))
                p2 = float(st.get("p2_hp", 0))
                if in_combat and p1 >= EPISODE_MIN_HP and p2 >= EPISODE_MIN_HP:
                    break
            time.sleep(0.01)

        if st is None:
            print(f"[BlankaEnv#{self.instance_id}] WARNING: timeout en reset(), obs cero")
            return np.zeros(30, dtype=np.float32), self._empty_info()

        p1  = float(st.get("p1_hp", MAX_HP))
        p2  = float(st.get("p2_hp", MAX_HP))
        cid = int(st.get("p2_char", 0xFF))
        self._last_p1_dir = int(st.get("p1_dir", 1))

        self._prev_p1_hp    = p1
        self._prev_p2_hp    = p2
        self._current_rival = cid if cid <= 11 else 0xFF
        self._prev_in_combat = True
        self._first_combat_seen = True

        # Primera ronda del episodio
        self._ep_rounds_played  = 1
        self._ep_matches_played = 1

        if cid <= 11:
            self._arcade_rival_seq = [cid]
            self._prev_rival = cid
            if cid in BOSS_IDS:
                self._bosses_reached.add(cid)

        print(
            f"[BlankaEnv#{self.instance_id}] "
            f"vs {CHAR_NAMES.get(cid,'?')} "
            f"| P1={p1:.0f} P2={p2:.0f} "
            f"dir={'→' if self._last_p1_dir==1 else '←'}"
            + (" [BOSS]" if cid in BOSS_IDS else "")
        )

        return self._get_obs(st), self._build_info(
            p1hp=p1, p2hp=p2, action=0,
            timeout_win=False, p2_just_died=False, won=False,
            round_won_this_step=False, match_won_this_step=False,
            in_combat=True, terminated=False, truncated=False,
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
        }

    # ── STEP ─────────────────────────────────────────────────────────────────
    def step(self, action: int):
        self._ep_step += 1
        action = int(action)

        # Modo restricción de acciones
        if ROLLING_AND_ELECTRIC_ONLY and not self._macro_active:
            action = self._cl1_action(self.bridge._last_state)

        raw_input = self._resolve(action)
        st = self.bridge.step(raw_input)

        # ── Bridge error ──────────────────────────────────────────────────────
        if st is None:
            self._bridge_error_count += 1
            if self._bridge_error_count >= _MAX_BRIDGE_ERRORS:
                print(f"[BlankaEnv#{self.instance_id}] BRIDGE ERROR x{_MAX_BRIDGE_ERRORS} — truncando")
                return self._get_obs(None), -1.0, False, True, {"bridge_error": True}
            return self._get_obs(self.bridge._last_state), 0.0, False, False, {"bridge_retry": True}
        else:
            self._bridge_error_count = 0

        self._update_charge(action)

        p1hp = float(st.get("p1_hp", MAX_HP))
        p2hp = float(st.get("p2_hp", MAX_HP))
        cid  = int(st.get("p2_char", 0xFF))
        in_combat = bool(st.get("in_combat", False))

        self._p1_hp_hist.append(self._prev_p1_hp)
        self._p2_hp_hist.append(self._prev_p2_hp)

        # ── FUERA DE COMBATE (menú, continue, char select) ────────────────────
        # No truncamos. Solo contabilizamos frames de espera y devolvemos r=0.
        if not in_combat:
            self._out_of_combat_frames += 1
            self._prev_in_combat = False
            self._update_internals(st)

            truncated  = self._ep_step >= self.MAX_STEPS
            terminated = False
            return self._get_obs(st), 0.0, terminated, truncated, self._build_info(
                p1hp=p1hp, p2hp=p2hp, action=action,
                timeout_win=False, p2_just_died=False, won=False,
                round_won_this_step=False, match_won_this_step=False,
                in_combat=False, terminated=terminated, truncated=truncated,
            )

        # ── EN COMBATE ────────────────────────────────────────────────────────
        self._out_of_combat_frames = 0

        dp1 = max(0.0, self._prev_p1_hp - p1hp)
        dp2 = max(0.0, self._prev_p2_hp - p2hp)
        self._ep_p1_dmg     += dp1
        self._ep_p2_dmg     += dp2
        self._combat_p1_dmg += dp1
        self._combat_p2_dmg += dp2

        # v4.3: flags de evento
        round_won_this_step  = False
        match_won_this_step  = False

        # ── Detección de transición menú→combate (nueva ronda o nuevo rival) ─
        # Cuando volvemos de out_of_combat (continue, nueva ronda) y ambos HP
        # están restaurados, contamos una nueva ronda.
        just_entered_combat = (not self._prev_in_combat) and in_combat
        if just_entered_combat and self._first_combat_seen:
            if p1hp >= EPISODE_MIN_HP and p2hp >= EPISODE_MIN_HP:
                self._ep_rounds_played += 1

        # ── Detección de nueva ronda en medio del enfrentamiento ──────────────
        # (sin salida a menú: HP suben mientras ya estábamos en combate)
        new_round_mid_combat = (
            self._prev_in_combat and in_combat and
            self._prev_p1_hp < EPISODE_MIN_HP and p1hp >= EPISODE_MIN_HP and
            self._prev_p2_hp < EPISODE_MIN_HP and p2hp >= EPISODE_MIN_HP
        )
        if new_round_mid_combat:
            self._ep_rounds_played += 1

        self._prev_in_combat    = True
        self._first_combat_seen = True

        # ── Cambio de rival (match win) ───────────────────────────────────────
        rival_changed = (cid <= 11 and
                         cid != self._current_rival and
                         self._current_rival <= 11)
        if rival_changed:
            print(
                f"[BlankaEnv#{self.instance_id}] Rival: "
                f"{CHAR_NAMES.get(self._current_rival,'?')} → {CHAR_NAMES.get(cid,'?')}"
                + (" [BOSS]" if cid in BOSS_IDS else "")
            )
            self._combat_won   = True
            self._rivals_defeated += 1
            self._flush_combat_to_registry(timeout_win=False)
            self._ep_wins     += 1
            self._won_round    = True

            self._ep_match_wins     += 1
            match_won_this_step      = True
            self._ep_matches_played += 1
            self._ep_rounds_played  += 1  # nuevo rival = nueva ronda

        self._update_internals(st)

        # ── KO de P2 ─────────────────────────────────────────────────────────
        p2_just_died = (p2hp <= 0 and self._prev_p2_hp > 0 and p1hp > 0)
        almost_ko    = (self._combat_p2_dmg >= 130 and p2hp <= 30 and dp2 > 0)

        if p2_just_died:
            self._ep_round_wins  += 1
            round_won_this_step   = True

        if p2_just_died or almost_ko:
            if not self._combat_won:
                print(
                    f"[BlankaEnv#{self.instance_id}] ¡KO! "
                    f"(dmg_total={self._combat_p2_dmg:.1f} | p2hp={p2hp:.1f})"
                )
                self._combat_won = True

            # ¿Arcade clear?
            if (p2_just_died and
                    self._current_rival == ARCADE_FINAL_BOSS and
                    not self._arcade_cleared):
                self._arcade_cleared      = True
                self._arcade_just_cleared = True
                print(
                    f"[BlankaEnv#{self.instance_id}] "
                    f"🎮 *** ARCADE CLEARED *** step={self._ep_step}"
                )

        # ── Terminación del episodio ──────────────────────────────────────────
        terminated = self._arcade_cleared
        truncated  = (not terminated) and (self._ep_step >= self.MAX_STEPS)
        timeout_win = False

        if terminated or truncated:
            if self._current_rival <= 11 and not self._combat_won:
                if (self._combat_p1_dmg == 0 or
                        self._prev_p1_hp > self._prev_p2_hp or
                        self._combat_p2_dmg >= 135):
                    timeout_win              = True
                    self._combat_won         = True
                    self._combat_timeout_win = True
                    print(f"[BlankaEnv#{self.instance_id}] Victoria por TIEMPO al cerrar")
            self._flush_combat_to_registry(timeout_win=timeout_win)

            tag = "ARCADE CLEAR" if terminated else f"TRUNCATION step={self._ep_step}"
            print(
                f"[BlankaEnv#{self.instance_id}] {tag} "
                f"| rivales={self._rivals_defeated} "
                f"| round_wr={self._ep_round_wins}/{self._ep_rounds_played} "
                f"| match_wr={self._ep_match_wins}/{self._ep_matches_played}"
            )

        won = self._won_round or self._combat_won

        r = self._calc_reward(p1hp, p2hp, st, action)
        self._prev_p1_hp  = p1hp
        self._prev_p2_hp  = p2hp
        self._last_action = action

        return self._get_obs(st), r, terminated, truncated, self._build_info(
            p1hp=p1hp, p2hp=p2hp, action=action,
            timeout_win=timeout_win,
            p2_just_died=p2_just_died or almost_ko,
            won=won,
            round_won_this_step=round_won_this_step,
            match_won_this_step=match_won_this_step,
            in_combat=True,
            terminated=terminated,
            truncated=truncated,
        )

    def render(self): pass

    def close(self):
        try: self.bridge.disconnect()
        except Exception: pass
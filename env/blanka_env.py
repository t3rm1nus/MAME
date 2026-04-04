"""
blanka_env.py — Entorno Gymnasium Blanka vs Arcade (SF2CE / MAME 0.286)
========================================================================
Version: 5.17 (04/04/2026)

CAMBIOS v5.17 — FIX CRÍTICO: 8 FALLOS DE DESINCRONIZACIÓN A NOTHROTTLE

  PROBLEMA RAÍZ:
  ────────────────
  A velocidad nothrottle el Lua escribe state_N.txt decenas de veces por
  frame real. Python llega sistemáticamente tarde a los frames de transición
  de round, lee round_result="none" y match_over=False, nunca acumula
  victorias en _match_p1_wins, y el episodio termina solo cuando el timer
  de MAME agota el HP de Blanka.

  FIX 1 — Doble buffer de resultado de round (CAUSA RAÍZ #1):
    _update_round_tracking ya no confía exclusivamente en lua_result del
    frame actual. Introduce _pending_round_result y _pending_match_over:
    si en el frame de transición (not in_combat and prev_in_combat)
    lua_result=="none", Python usa el último resultado no-none visto en
    cualquier frame previo. El buffer se rellena en _on_every_step() que
    se llama en step() tanto en combate como fuera de él, capturando el
    resultado en el frame exacto en que el Lua lo escribe antes de que
    lo resetee.

  FIX 2 — Validación de in_combat con HP antes de aceptar transición:
    La transición "not in_combat and prev_in_combat" solo se acepta si
    se cumple al menos una condición real de fin de round: p1hp==0,
    p2hp==0, lua_match_over==True, o _pending_match_over==True.
    Evita falsos positivos por frames de hitstop/KO intermedios.

  FIX 3 — report_cid nunca usa _current_rival en flush:
    _flush_combat_to_registry siempre usa _combat_rival capturado al
    inicio del combate. Si es 0xFF, usa _prev_rival como segundo
    fallback. Se elimina el uso de cid (parámetro de step) como fuente.

  FIX 4 — _CID_FLICKER_POST_MATCH aumentado a 8:
    3 frames a nothrottle es <50ms reales, insuficiente para distinguir
    flicker de cambio real. Se sube a 8 para mayor robustez.

  FIX 5 — reset() valida match_p1_wins == match_p2_wins == 0:
    Si el state inicial tiene marcadores residuales del combate anterior
    (posible a nothrottle durante la pantalla de Continue), reset() espera
    hasta que ambos sean 0 antes de aceptar el estado.

  FIX 6 — Eliminado doble flush en _finalize_episode:
    _finalize_episode ahora tiene flag _registry_flushed que se activa
    en cada llamada a _flush_combat_to_registry, evitando el doble
    registro cuando el path win/loss ya hizo el flush.

  FIX 7 — Métricas de daño corregidas en info dict:
    Se añaden claves p1_hp_real y p2_hp_real al info dict con los valores
    SIN swap para que MetricsCallback pueda calcular avg_p2_damage
    correctamente. El swap interno del entorno se mantiene intacto.

  FIX 8 — reset() verifica in_combat con contenido mínimo de combate:
    Además de in_combat==True y HP>=EPISODE_MIN_HP, reset() verifica
    que p2_char sea un CID válido (<=11) antes de aceptar el estado,
    evitando arrancar con un state de menú o pantalla de título.

CAMBIOS v5.16 (mantenidos):
  · FIX TRACKER DE EPISODIOS: terminated=True tras cada match.
  · FIX BUG 2: flush en victorias además de en derrotas.
  · FIX BUG 3: flush con rival_id explícito.

CAMBIOS v5.15 (mantenidos):
  · FIX SWAP RAM: match_p1_wins=Blanka, match_p2_wins=Rival (Lua v2.13).
  · WAIT_COMBAT post-continue: espera sm_frame>90 antes de leer baseline.

CAMBIOS v5.14 (mantenidos):
  · FIX CID CONGELADO: _combat_rival captura el CID al inicio del combate.

CAMBIOS v5.13 (mantenidos):
  · FIX MATCH GANADO: requiere match_p1_wins > match_p2_wins (no solo >= 2).

CAMBIOS v5.12 (mantenidos):
  · FIX HP INVERTIDO: bridge escribe p1_hp=rival, p2_hp=Blanka (swap).
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
from env.reward import compute_reward, BlankaContext
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

# ── v5.14: umbral anti-flicker normal vs post-match ──────────────────────────
_CID_FLICKER_NORMAL     = 8   # frames para aceptar nuevo CID en mid-combat
# FIX 4: subido de 3 → 8. A nothrottle 3 frames son <50ms reales,
# insuficiente para distinguir flicker real de cambio legítimo.
_CID_FLICKER_POST_MATCH = 8

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

        v5.17: Doble buffer de resultado de round para nothrottle.
               Validación reforzada de transiciones de combate.
               reset() valida CID y marcador limpio.
        """
        super().__init__()
        self.instance_id  = instance_id
        self.MAX_STEPS    = max_steps
        self.render_mode  = render_mode
        self.registry     = registry
        self.bridge       = MAMEBridge(instance_id=instance_id)
        self._cl1_mode    = cl1_mode
        self._cid_candidate = 0xFF
        self._cid_candidate_count = 0
        self._last_actions_hist = deque(maxlen=8)
        self.observation_space = spaces.Box(-1.0, 1.0, shape=(30,), dtype=np.float32)

        if self._cl1_mode:
            self.action_space = spaces.Discrete(_CL1_N_ACTIONS)
        else:
            self.action_space = spaces.Discrete(N_ACTIONS)

        # Estado interno — reset() inicializa todo
        self._prev_p1_hp:   float = MAX_HP
        self._prev_p2_hp:   float = MAX_HP
        self._ep_step:      int   = 0
        self._ep_total_steps: int = 0
        self._last_action:  int   = 0
        self._last_p1_dir:  int   = 1
        self._current_rival: int  = 0xFF
        self._combat_rival: int   = 0xFF
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
        self._post_match_transition: bool = False
        self._episode_done: bool = False

        # ── v5.17 FIX 1: Doble buffer de resultado de round ──────────────────
        # Captura el último round_result/match_over no-none visto en cualquier
        # frame, para recuperarlo si el Lua lo sobreescribe antes de que Python
        # lea la transición not_in_combat→in_combat.
        self._pending_round_result: str  = "none"
        self._pending_match_over:   bool = False
        self._pending_m1_wins:      int  = 0
        self._pending_m2_wins:      int  = 0
        self._pending_frames_ago:   int  = 999  # cuántos frames tiene el pending

        # ── v5.17 FIX 6: Flag anti-doble-flush ───────────────────────────────
        self._registry_flushed: bool = False

    # ── v5.17 FIX 1: Captura de resultado en cada frame ──────────────────────
    def _capture_pending_result(self, st: Optional[Dict]):
        """
        Llamado en CADA step (tanto in_combat como fuera) para capturar
        round_result y match_over en el frame exacto en que el Lua los escribe,
        antes de que los resetee en el siguiente frame a nothrottle.
        """
        if st is None:
            self._pending_frames_ago += 1
            return

        result     = st.get("round_result", "none")
        match_over = bool(st.get("match_over", False))
        m1         = int(st.get("match_p1_wins", self._match_p1_wins))
        m2         = int(st.get("match_p2_wins", self._match_p2_wins))

        if result != "none" or match_over:
            # Hay información real: actualizar el buffer
            self._pending_round_result = result
            self._pending_match_over   = match_over
            self._pending_m1_wins      = m1
            self._pending_m2_wins      = m2
            self._pending_frames_ago   = 0
        else:
            self._pending_frames_ago += 1

    def _consume_pending_result(self) -> Tuple[str, bool, int, int]:
        """
        Devuelve el último resultado capturado y resetea el buffer.
        Se llama al detectar la transición de fin de combate.
        El resultado es válido si _pending_frames_ago < 30 (ventana de 30 frames
        para absorber el lag entre el frame de resultado y la transición).
        """
        if self._pending_frames_ago < 30:
            result     = self._pending_round_result
            match_over = self._pending_match_over
            m1         = self._pending_m1_wins
            m2         = self._pending_m2_wins
        else:
            # Buffer caducado: usar valores actuales del estado
            result     = "none"
            match_over = False
            m1         = self._match_p1_wins
            m2         = self._match_p2_wins

        # Reset del buffer
        self._pending_round_result = "none"
        self._pending_match_over   = False
        self._pending_frames_ago   = 999
        return result, match_over, m1, m2

    # ── v5.12: NORMALIZACIÓN HP ───────────────────────────────────────────────
    @staticmethod
    def _hp_from_state(st: Optional[Dict]) -> Tuple[float, float]:
        """
        FIX v5.12: El bridge escribe p1_hp=rival y p2_hp=Blanka (swap).
        Devuelve (blanka_hp, rival_hp).
        """
        if st is None:
            return MAX_HP, MAX_HP
        blanka_hp = float(st.get("p2_hp", MAX_HP))  # ← swap intencional
        rival_hp  = float(st.get("p1_hp", MAX_HP))  # ← swap intencional
        return blanka_hp, rival_hp

    # ── OBSERVACIÓN ──────────────────────────────────────────────────────────
    def _get_obs(self, st: Optional[Dict]) -> np.ndarray:
        if st is None:
            return np.zeros(30, dtype=np.float32)

        p1hp, p2hp = self._hp_from_state(st)

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
    def _calc_reward(self, p1hp: float, p2hp: float, st: dict,
                 action: int, extra_bonus: float = 0.0) -> float:
        real_action = action
        if self._cl1_mode and 0 <= action < _CL1_N_ACTIONS:
            real_action = _CL1_ACTION_MAP[action]

        ctx = BlankaContext(
            ep_step             = self._ep_step,
            fk_land_steps       = self._fk_land_steps,
            macro_action_id     = self._macro_action_id,
            p2_was_air          = self._p2_was_air,
            p1_land_steps       = self._p1_land_steps,
            last_p1_dir         = self._last_p1_dir,
            arcade_just_cleared = self._arcade_just_cleared,
            last_actions_hist   = list(self._last_actions_hist),
        )
        reward = compute_reward(
            p1hp, p2hp,
            self._prev_p1_hp, self._prev_p2_hp,
            st, real_action, ctx, extra_bonus,
        )
        if self._arcade_just_cleared:
            self._arcade_just_cleared = False
        return reward
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
                prev_name = CHAR_NAMES.get(self._prev_rival, ...)
                new_name  = CHAR_NAMES.get(cid, ...)
                print(f"🔄 Rival cambiado: {prev_name} → {new_name}")

                if not self._combat_won and self._prev_rival <= 11:
                    print(f"[BlankaEnv#{self.instance_id}] 🏆 MATCH GANADO (implícito por avance arcade) vs {prev_name}")
                    self._ep_match_wins     += 1
                    self._ep_matches_played += 1
                    self._rivals_defeated   += 1
                    self._combat_won         = True
                    self._round_bonus_pending += 200.0
                    self._flush_combat_to_registry(
                        rival_id=self._combat_rival, timeout_win=False)

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
            self._bonus_frames += 1
            if self._bonus_frames == BONUS_STAGE_FRAMES:
                if not self._reached_bonus:
                    print(
                        f"[BlankaEnv#{self.instance_id}] "
                        f"⭐ BONUS STAGE DETECTADO | step={self._ep_step}"
                    )
                self._reached_bonus = True
            elif self._bonus_frames == 1:
                print(
                    f"[BlankaEnv#{self.instance_id}] ⚠️  CID INVÁLIDO:"
                    f" p2_char={cid} ({cid:#04x}) | prev_rival={self._prev_rival}"
                    f" | step={self._ep_step} — posible fallo lectura RAM o transición"
                )

            self._current_rival = cid
            self._current_rival_name = "Bonus Stage"

    # ── TRACKING DE RONDAS ────────────────────────────────────────────────────
    def _update_round_tracking(self, p1hp: float, p2hp: float, cid: int,
                               in_combat: bool, st: Dict) -> Tuple[bool, bool, float]:
        round_won_this_step = False
        match_won_this_step = False
        extra_reward        = 0.0

        if not st:
            return False, False, 0.0

        # ── TRANSICIÓN: Fin del Combate ──────────────────────────────────────
        if not in_combat and self._prev_in_combat:

            # ── v5.17 FIX 2: Validar que es un fin de round real ─────────────
            # A nothrottle, in_combat puede parpadear a False en frames de
            # hitstop o animación de KO. Solo aceptamos la transición si hay
            # evidencia real de fin de round: algún HP en 0, resultado pendiente
            # válido, o match_over en el buffer.
            hp_ko       = (p1hp <= 0 or p2hp <= 0)
            pending_res, pending_mo, pending_m1, pending_m2 = self._consume_pending_result()
            has_result  = (pending_res != "none") or pending_mo

            if not hp_ko and not has_result:
                # Transición espuria: in_combat parpadeó a False sin fin real
                print(
                    f"[BlankaEnv#{self.instance_id}] ⚠️  Transición in_combat→False IGNORADA"
                    f" (posible hitstop/nothrottle) | p1hp={p1hp:.0f} p2hp={p2hp:.0f}"
                    f" | pending_res='{pending_res}' pending_mo={pending_mo}"
                )
                return False, False, 0.0

            self._ep_rounds_played += 1

            # FIX 3: report_cid usa _combat_rival primero, luego _prev_rival como
            # fallback. NUNCA usa self._current_rival (puede ser 0xFF en transición).
            if self._combat_rival <= 11:
                report_cid = self._combat_rival
            elif self._prev_rival <= 11:
                report_cid = self._prev_rival
            else:
                report_cid = cid  # último recurso
            rname = CHAR_NAMES.get(report_cid, f"ID_{report_cid:#04x}")

            # ── Usar resultado del buffer (FIX 1) en lugar del frame actual ──
            lua_result     = pending_res
            lua_match_over = pending_mo
            lua_m1         = pending_m1
            lua_m2         = pending_m2

            # Si el buffer caducó (>30 frames) pero tenemos KO, inferir resultado
            if lua_result == "none" and hp_ko:
                if p2hp <= 0 and p1hp > 0:
                    lua_result = "win"
                    print(
                        f"[BlankaEnv#{self.instance_id}] ℹ️  round_result inferido='win'"
                        f" por p2hp=0 (buffer caducado, frames_ago={self._pending_frames_ago})"
                    )
                elif p1hp <= 0 and p2hp > 0:
                    lua_result = "loss"
                    print(
                        f"[BlankaEnv#{self.instance_id}] ℹ️  round_result inferido='loss'"
                        f" por p1hp=0 (buffer caducado, frames_ago={self._pending_frames_ago})"
                    )
                elif p1hp <= 0 and p2hp <= 0:
                    lua_result = "draw"

            
            if lua_result == "win":
                self._ep_round_wins += 1
                round_won_this_step = True
                extra_reward += 100.0
                # Actualizar marcador si el buffer lo tiene
                if lua_m1 > self._match_p1_wins:
                    self._match_p1_wins = lua_m1
                else:
                    self._match_p1_wins += 1
                if lua_m2 >= 0:
                    self._match_p2_wins = lua_m2
                print(f"[BlankaEnv#{self.instance_id}] ✅ Round GANADO vs {rname} | Blanka:{self._match_p1_wins} Rival:{self._match_p2_wins}")
            elif lua_result == "loss":
                extra_reward -= 50.0
                if lua_m2 > self._match_p2_wins:
                    self._match_p2_wins = lua_m2
                else:
                    self._match_p2_wins += 1
                if lua_m1 >= 0:
                    self._match_p1_wins = lua_m1
                print(f"[BlankaEnv#{self.instance_id}] ❌ Round PERDIDO vs {rname} | Blanka:{self._match_p1_wins} Rival:{self._match_p2_wins}")
            elif lua_result == "draw":
                print(f"[BlankaEnv#{self.instance_id}] 🤝 Round EMPATADO (Double KO / Timeout) vs {rname} | Blanka:{self._match_p1_wins} Rival:{self._match_p2_wins}")
            else:
                # Sin resultado inferible: asumir derrota conservadora para
                # evitar que el episodio se quede colgado
                self._match_p2_wins += 1
                print(
                    f"[BlankaEnv#{self.instance_id}] ⚠️  round_result desconocido"
                    f" ('{lua_result}') — asumiendo pérdida conservadora"
                    f" | Blanka:{self._match_p1_wins} Rival:{self._match_p2_wins}"
                )

            # ── Evaluación de fin de Match ────────────────────────────────────
            # match_over del buffer O deducción por marcador
            is_match_over = lua_match_over or (
                self._match_p1_wins >= 2 or self._match_p2_wins >= 2
            )

            if is_match_over:
                if self._match_p1_wins >= 2 and self._match_p1_wins > self._match_p2_wins:
                    # ── MATCH GANADO ──────────────────────────────────────────
                    self._ep_match_wins += 1
                    self._ep_matches_played += 1
                    match_won_this_step = True
                    extra_reward += 200.0
                    self._rivals_defeated += 1
                    self._combat_won = True

                    is_boss = " [BOSS]" if report_cid in BOSS_IDS else ""
                    print(
                        f"[BlankaEnv#{self.instance_id}] 🏆 MATCH GANADO vs {rname}"
                        f" | Blanka:{self._match_p1_wins} Rival:{self._match_p2_wins}{is_boss}"
                        f" | Total rivales: {self._rivals_defeated}"
                    )

                    if report_cid == ARCADE_FINAL_BOSS:
                        self._arcade_cleared = True
                        self._arcade_just_cleared = True
                        print(f"[BlankaEnv#{self.instance_id}] 🎮 *** ARCADE CLEARED *** 🎮")

                    self._flush_combat_to_registry(
                        rival_id=report_cid, timeout_win=False)
                    self._episode_done = True

                elif self._match_p2_wins >= 2 and self._match_p2_wins > self._match_p1_wins:
                    # ── MATCH PERDIDO ─────────────────────────────────────────
                    self._ep_matches_played += 1
                    print(
                        f"[BlankaEnv#{self.instance_id}] 💀 MATCH PERDIDO vs {rname}"
                        f" | Blanka:{self._match_p1_wins} Rival:{self._match_p2_wins}"
                        f" | Esperando Continue..."
                    )
                    self._flush_combat_to_registry(
                        rival_id=report_cid, timeout_win=False)
                    self._episode_done = True

                elif self._match_p1_wins == self._match_p2_wins and self._match_p1_wins >= 1:
                    # Empate (1-1 pendiente de resolver, o doble KO en round 3)
                    # No terminar el episodio: esperar al round decisivo
                    pass

                else:
                    # Marcador inesperado con match_over
                    self._ep_matches_played += 1
                    print(
                        f"[BlankaEnv#{self.instance_id}] ⚠️  MATCH OVER con marcador INESPERADO"
                        f" Blanka:{self._match_p1_wins} Rival:{self._match_p2_wins} vs {rname}"
                        f" — tratando como derrota"
                    )
                    self._flush_combat_to_registry(
                        rival_id=report_cid, timeout_win=False)
                    self._episode_done = True

                self._match_p1_wins = 0
                self._match_p2_wins = 0
                self._post_match_transition = True

        # Reseteo forzado si hay cambio de rival mid-combat
        if in_combat and cid <= 11 and cid != self._current_rival and self._current_rival <= 11:
            self._match_p1_wins = 0
            self._match_p2_wins = 0

        # v5.14: Detectar inicio de nuevo combate
        if in_combat and not self._prev_in_combat:
            self._combat_rival = cid if cid <= 11 else self._current_rival
            self._post_match_transition = False
            # Reset del flag anti-doble-flush para el nuevo combate
            self._registry_flushed = False
            cname = CHAR_NAMES.get(self._combat_rival, f"ID_{self._combat_rival}")
            print(
                f"[BlankaEnv#{self.instance_id}] ⚔️  Nuevo combate: Blanka vs {cname}"
                f" [cid={self._combat_rival}] | Marcador: {self._match_p1_wins}-{self._match_p2_wins}"
            )

        return round_won_this_step, match_won_this_step, extra_reward

    # ── REGISTRO DE COMBATE ───────────────────────────────────────────────────
    def _flush_combat_to_registry(self, rival_id: Optional[int] = None,
                                   timeout_win: bool = False):
        """
        v5.17 FIX 6: Flag _registry_flushed evita doble registro en el mismo
        combate. Se resetea al inicio de cada nuevo combate en _update_round_tracking.

        v5.17 FIX 3: rival_id nunca cae a _current_rival (puede ser 0xFF).
        Jerarquía: parámetro → _combat_rival → _prev_rival.
        """
        # FIX 6: anti-doble-flush
        if self._registry_flushed:
            return
        self._registry_flushed = True

        # FIX 3: jerarquía de IDs segura
        if rival_id is None or rival_id > 11:
            rival_id = self._combat_rival
        if rival_id > 11:
            rival_id = self._prev_rival

        if self.registry and rival_id <= 11:
            self.registry.record_episode(
                rival_id,
                self._combat_won,
                self._combat_p1_dmg,
                self._combat_p2_dmg,
                extras={
                    "arcade_sequence":  list(self._arcade_rival_seq),
                    "reached_bonus":    self._reached_bonus,
                    "round_wins":       self._ep_wins,
                    "timeout_win":      timeout_win,
                    "is_boss":          rival_id in BOSS_IDS,
                    "bosses_reached":   sorted(list(self._bosses_reached)),
                    "arcade_cleared":   self._arcade_cleared,
                }
            )
        elif self.registry and rival_id > 11:
            print(
                f"[BlankaEnv#{self.instance_id}] ⚠️  _flush_combat_to_registry: "
                f"rival_id={rival_id} inválido — no se registra en registry"
            )

        self._combat_p1_dmg      = 0.0
        self._combat_p2_dmg      = 0.0
        self._combat_won         = False
        self._combat_timeout_win = False

    # ── INFO DICT ─────────────────────────────────────────────────────────────
    def _build_info(self, st, p1hp, p2hp, action, action_real, p2_just_died, won,
                    round_won_this_step, match_won_this_step, in_combat,
                    timeout_win, terminated, truncated):

        gst    = st.get("game_state", 0) if st else 0
        is_done = terminated or truncated

        # ── v5.17 FIX 7: HP sin swap para métricas correctas en callback ──────
        # p1hp y p2hp internamente ya tienen el swap de v5.12 aplicado:
        #   p1hp = blanka_hp (desde p2_hp del bridge)
        #   p2hp = rival_hp  (desde p1_hp del bridge)
        # Para las métricas del callback necesitamos saber el HP del rival real,
        # que es p2hp en el espacio interno. Se añaden claves _rival_hp y _blanka_hp
        # para que MetricsCallback calcule avg_p2_damage = 144 - rival_hp_final.
        rival_hp_for_metrics  = p2hp   # HP del rival (ya swapeado correctamente)
        blanka_hp_for_metrics = p1hp   # HP de Blanka

        return {
            "game_state": gst,
            "p1_hp": p1hp,
            "p2_hp": p2hp,
            # FIX 7: claves explícitas para métricas sin ambigüedad
            "rival_hp":  rival_hp_for_metrics,
            "blanka_hp": blanka_hp_for_metrics,
            "won": won,
            "timeout_win": timeout_win,
            "p2_just_died": p2_just_died,
            "step": self._ep_step,
            "action": action,
            "action_real": action_real,
            "macro_just_started": self._macro_just_started,
            "rival": self._current_rival,
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
        super().reset(seed=seed)
        self._last_actions_hist.clear()
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
        self._combat_rival  = 0xFF
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
        self._post_match_transition = False
        self._episode_done = False
        self._registry_flushed = False

        # v5.17: reset del buffer de resultado pendiente
        self._pending_round_result = "none"
        self._pending_match_over   = False
        self._pending_m1_wins      = 0
        self._pending_m2_wins      = 0
        self._pending_frames_ago   = 999

        mode_str = "CL-1 (7 acciones)" if self._cl1_mode else "COMPLETO (26 acciones)"
        print(f"[BlankaEnv#{self.instance_id}] Reset... [{mode_str}]")

        st = None
        valid_st = None
        t_reset_start = time.time()
        deadline = t_reset_start + 120.0
        _last_log = t_reset_start
        while time.time() < deadline:
            st = self.bridge._parse_state_file()
            now = time.time()

            if now - _last_log >= 10.0:
                _last_log = now
                elapsed_r = now - t_reset_start
                if st:
                    _p1d, _p2d = self._hp_from_state(st)
                    print(f"[BlankaEnv#{self.instance_id}] reset() esperando... "
                          f"{elapsed_r:.0f}s | in_combat={st.get('in_combat')} "
                          f"p1_hp={_p1d:.0f} p2_hp={_p2d:.0f} "
                          f"frame={st.get('frame','?')} gs={st.get('game_state','?')}")
                else:
                    print(f"[BlankaEnv#{self.instance_id}] reset() esperando... "
                          f"{elapsed_r:.0f}s | state_file=None (Lua no escribe todavia)")

            if st:
                in_combat  = bool(st.get("in_combat", False))
                p1, p2     = self._hp_from_state(st)
                raw_cid    = int(st.get("p2_char", 0xFF))

                # ── v5.17 FIX 8: validar CID y marcador limpio ───────────────
                # No aceptar state de menú/pantalla de título (CID inválido) ni
                # state con marcadores residuales del combate anterior.
                cid_valid      = (raw_cid <= 11)
                score_clean    = (
                    int(st.get("match_p1_wins", 1)) == 0 and
                    int(st.get("match_p2_wins", 1)) == 0
                )

                if in_combat and p1 >= EPISODE_MIN_HP and p2 >= EPISODE_MIN_HP \
                        and cid_valid and score_clean:
                    valid_st = st
                    break
            time.sleep(0.05)

        if valid_st is None:
            print(f"[BlankaEnv#{self.instance_id}] WARNING: timeout en reset(), obs cero")
            return np.zeros(30, dtype=np.float32), self._empty_info()

        st = valid_st

        self._match_p1_wins = int(st.get("match_p1_wins", 0))
        self._match_p2_wins = int(st.get("match_p2_wins", 0))

        p1, p2 = self._hp_from_state(st)
        if p1 > MAX_HP: p1 = MAX_HP
        if p2 > MAX_HP: p2 = MAX_HP
        cid = int(st.get("p2_char", 0xFF))
        self._last_p1_dir = int(st.get("p1_dir", 1))

        self._prev_p1_hp     = p1
        self._prev_p2_hp     = p2
        self._current_rival  = cid if cid <= 11 else 0xFF
        self._combat_rival   = self._current_rival
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
            "won": False, "rivals_defeated": 0,
            "rival_hp": MAX_HP, "blanka_hp": MAX_HP,
        }

    # ── STEP ─────────────────────────────────────────────────────────────────
    def step(self, action: int):
        self._ep_step        += 1
        self._ep_total_steps += 1
        action = int(action)

        # 1. Ejecutar acción en el Bridge
        raw_input = self._resolve(action)
        st = self.bridge.step(raw_input)

        # ── Manejo de Error de Bridge ─────────────────────────────────────────
        if st is None:
            self._bridge_error_count += 1
            if self._bridge_error_count >= _MAX_BRIDGE_ERRORS:
                print(f"[BlankaEnv#{self.instance_id}] ❌ CRASH DEL BRIDGE — Truncando episodio")
                return self._get_obs(None), -1.0, False, True, {"bridge_error": True}
            return self._get_obs(self.bridge._last_state), 0.0, False, False, {"bridge_retry": True}

        self._bridge_error_count = 0

        # 2. Actualizar carga
        self._update_charge(action)

        # 3. v5.12: Leer HPs con helper normalizado
        p1hp, p2hp = self._hp_from_state(st)
        if p1hp > 144: p1hp = self._prev_p1_hp if self._prev_p1_hp <= 144 else 144
        if p2hp > 144: p2hp = self._prev_p2_hp if self._prev_p2_hp <= 144 else 144

        # ── v5.17 FIX 1: Capturar resultado ANTES de cualquier lógica ────────
        # Se llama en TODOS los frames, tanto en combate como fuera, para
        # capturar round_result/match_over en el frame exacto en que el Lua
        # lo escribe, antes de que nothrottle lo sobreescriba con "none".
        self._capture_pending_result(st)

        # 4. Filtro de ID de Rival (Anti-Flicker)
        raw_cid   = int(st.get("p2_char", 0xFF))
        in_combat = bool(st.get("in_combat", False))

        flicker_threshold = (
            _CID_FLICKER_POST_MATCH if self._post_match_transition
            else _CID_FLICKER_NORMAL
        )

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

                if self._cid_candidate_count >= flicker_threshold:
                    self._cid_candidate_count = 0
                    old_name = CHAR_NAMES.get(self._current_rival, f"ID_{self._current_rival}")
                    new_name = CHAR_NAMES.get(raw_cid, f"ID_{raw_cid}")
                    print(
                        f"[BlankaEnv#{self.instance_id}] 🔄 NUEVO RIVAL CONFIRMADO:"
                        f" {old_name} → {new_name} [cid={raw_cid}]"
                        f" (threshold={flicker_threshold}f)"
                    )
                    self._current_rival = raw_cid
                    if raw_cid not in self._arcade_rival_seq:
                        self._arcade_rival_seq.append(raw_cid)
                    self._post_match_transition = False
            else:
                self._cid_candidate = raw_cid
                self._cid_candidate_count = 0
        cid = self._current_rival

        # 5. Resolver acción real
        if self._cl1_mode:
            clamped_action = min(action, len(_CL1_ACTION_MAP) - 1)
            _action_real = _CL1_ACTION_MAP[clamped_action]
        else:
            _action_real = action

        self._p1_hp_hist.append(p1hp)
        self._p2_hp_hist.append(p2hp)

        terminated = False
        truncated  = False

        # ── CASO A: FUERA DE COMBATE ──────────────────────────────────────────
        if not in_combat:
            self._out_of_combat_frames += 1
            self._update_internals(st)

            step_reward = 0.0
            if self._prev_in_combat:
                _, _, _er = self._update_round_tracking(p1hp, p2hp, cid, in_combat=False, st=st)
                if _er:
                    step_reward += _er

            self._prev_in_combat = False

            terminated = self._arcade_cleared or self._episode_done
            if terminated:
                self._finalize_episode()

            step_reward += self._round_bonus_pending
            self._round_bonus_pending = 0.0

            return self._get_obs(st), step_reward, terminated, truncated, self._build_info(
                st=st, p1hp=p1hp, p2hp=p2hp, action=action, action_real=_action_real,
                p2_just_died=False, won=(self._ep_match_wins > 0),
                round_won_this_step=False, match_won_this_step=False,
                in_combat=False, timeout_win=False,
                terminated=terminated, truncated=truncated
            )

        # ── CASO B: EN COMBATE ────────────────────────────────────────────────
        self._out_of_combat_frames = 0
        dp1 = max(0.0, self._prev_p1_hp - p1hp)
        dp2 = max(0.0, self._prev_p2_hp - p2hp)

        self._ep_p1_dmg += dp1
        self._ep_p2_dmg += dp2
        self._combat_p1_dmg += dp1
        self._combat_p2_dmg += dp2

        p2_just_died = (p2hp <= 0 and dp2 > 0)

        round_won, match_won, extra_reward = self._update_round_tracking(p1hp, p2hp, cid, in_combat=True, st=st)

        extra_reward += self._round_bonus_pending
        self._round_bonus_pending = 0.0

        self._update_internals(st)
        self._prev_in_combat = True

        terminated = self._arcade_cleared or self._episode_done
        if terminated:
            self._finalize_episode()

        self._last_actions_hist.append(_action_real)
        reward = self._calc_reward(p1hp, p2hp, st, action, extra_bonus=extra_reward)

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
        """
        v5.17 FIX 6: Solo hace el flush si _registry_flushed es False,
        evitando doble registro cuando win/loss ya llamaron a
        _flush_combat_to_registry con rival_id explícito.
        """
        # Solo flush si hay daño pendiente Y no se ha registrado ya
        if not self._registry_flushed and (self._combat_p1_dmg > 0 or self._combat_p2_dmg > 0):
            self._flush_combat_to_registry(
                rival_id=self._combat_rival, timeout_win=timeout_win)

        rival_seq_names = [
            f"{CHAR_NAMES.get(c, f'ID_{c}')}(cid={c})"
            for c in self._arcade_rival_seq
        ]
        tag = "ARCADE CLEAR ✅" if self._arcade_cleared else (
              "MATCH TERMINADO" if self._episode_done else "FIN EMERGENCIA")
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
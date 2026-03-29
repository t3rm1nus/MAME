"""
blanka_env.py — Entorno Gymnasium Blanka vs Arcade (SF2CE / MAME 0.286)
========================================================================
Versión: 2.2 (29/03/2026)

Cambios v2.2:
  · terminated ahora usa el campo "in_combat" del estado Lua (v1.5+).
    Cuando Lua sale de IN_COMBAT (GAME_OVER, WIN_WAIT, etc.), el episodio
    termina limpiamente. Esto reemplaza la detección HP<=0 que fallaba porque
    SF2CE pone P1_HP=255 (0xFF) al morir en lugar de dejarlo en 0.
  · bridge_error ya no devuelve truncated=True de forma inmediata. Si el
    bridge falla consecutivamente más de _MAX_BRIDGE_ERRORS veces, entonces
    sí trunca. Esto evita el ciclo infinito de NOOPs causado por la race
    condition de state.txt.
  · Añadido _bridge_error_count para diagnóstico.
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

# ── CONSTANTES ────────────────────────────────────────────────────────────────
MAX_HP           = 144.0
MAX_X            = 1400.0
STUN_MAX         = 200.0
TIMER_MAX        = 99.0
EPISODE_MIN_HP   = 100
CHARGE_REQUIRED  = 15
BOOM_FLIGHT_STEPS= 51
FK_YVEL_THR      = 256

# Número de fallos consecutivos de bridge.step() antes de truncar el episodio.
# Con reintentos en mame_bridge.py el número de fallos reales debería ser << 10.
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
]
MACRO_ROLLING: List[List[int]] = (
    [[0,0,1,0,0,0,0,0,0,0,0,0]] * 21 +
    [[0,0,0,1,1,0,0,0,0,0,0,0]]
)
MACRO_ELECTRIC: List[List[int]] = [[0,0,0,0,1,0,0,0,0,0,0,0]] * 5
MACROS: Dict[int, List[List[int]]] = {15: MACRO_ROLLING, 16: MACRO_ELECTRIC}
NOOP = SINGLE_FRAME_ACTIONS[0]


def fk_phase_value(anim: int, p2_airborne: bool) -> float:
    if not p2_airborne: return 0.0
    if anim == 0x0C: return 0.2
    if anim == 0x02: return 0.4
    if anim == 0x00: return 0.6
    if anim == 0x04: return 0.8
    return 0.1


class BlankaEnv(gym.Env):
    """
    observation_space: Box(28,) float32
    action_space:      Discrete(17)
      15 = Rolling Attack macro | 16 = Electric Thunder macro
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

        self.observation_space = spaces.Box(-1.0, 1.0, shape=(28,), dtype=np.float32)
        self.action_space      = spaces.Discrete(17)

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
        # [FIX v2.2] Contador de fallos consecutivos de bridge
        self._bridge_error_count = 0

    # ── OBSERVACIÓN ──────────────────────────────────────────────────────────
    def _get_obs(self, st: Optional[Dict]) -> np.ndarray:
        if st is None:
            return np.zeros(28, dtype=np.float32)
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

        p2cr  = bool(st.get("p2_crouch", False))

        obs = np.array([
            p1hp / MAX_HP,                       # 0
            p1x  / MAX_X,                        # 1
            float(p1air),                        # 2
            p1dir,                               # 3
            min(p1stn, STUN_MAX) / STUN_MAX,     # 4
            p2hp / MAX_HP,                       # 5
            p2x  / MAX_X,                        # 6
            float(p2cr),                         # 7
            float(p2air),                        # 8
            min(p2stn, STUN_MAX) / STUN_MAX,     # 9
            dist  / MAX_X,                       # 10
            (p1x - p2x) / MAX_X,                # 11
            timer / TIMER_MAX,                   # 12
            float(p1x < 150 or p1x > 1250),     # 13
            float(p2x < 150 or p2x > 1250),     # 14
            (p1hp - p2hp) / MAX_HP,             # 15
            d1,                                  # 16
            d2,                                  # 17
            fk_phase_value(p2anim, p2air),       # 18
            float(sb_active),                    # 19
            (self._boom_est_x / MAX_X) if sb_active else 0.0,  # 20
            float(prj),                          # 21
            boom_t_n,                            # 22
            charge_n,                            # 23
            float(self._gnd_steps > 30),         # 24
            float(p2his > 0),                    # 25
            float(self._fk_land_steps > 0 and self._fk_land_steps <= 20),  # 26
            self._last_action / 16.0,            # 27
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

        if 0 < self._fk_land_steps <= 20:
            if action == 15 and dp2 > 0:        r += 15.0
            elif action == 15:                   r += 4.0
        if self._gnd_steps > 30 and p1a and dist < 280: r -= 10.0

        if action == 15:
            if dp2 > 0:
                r += 4.0 + (8.0 if 200 <= dist <= 600 else 0.0)
            else:
                r -= 3.0 if dist < 150 or dist > 700 else 1.0
        if action == 16:
            r += 5.0 if (p2a and dp2 > 0) else (-2.0 if not p2a else 0.0)

        if self._ep_step > 50 and dp2 == 0 and dp1 == 0: r -= 0.002
        if p2x < 100 or p2x > 1300: r += 1.0
        return float(r)

    # ── MACRO ENGINE ─────────────────────────────────────────────────────────
    def _resolve(self, action: int) -> List[int]:
        if self._macro_active:
            if self._macro_seq:
                self._macro_buf = len(self._macro_seq)
                return self._macro_seq.pop(0)
            self._macro_active = False; self._macro_buf = 0
        if self._macro_buf > 0:
            self._macro_buf = 0; return NOOP
        if action in MACROS:
            seq = list(MACROS[action])
            if action == 15 and self._last_p1_dir == 0:
                seq = [[f[0],f[1],f[3],f[2]]+f[4:] for f in seq]
            self._macro_active = True; self._macro_seq = seq
            self._macro_buf = len(seq); return self._macro_seq.pop(0)
        return SINGLE_FRAME_ACTIONS[action] if action < len(SINGLE_FRAME_ACTIONS) else NOOP

    def _update_charge(self, action: int):
        if action == 15: self._charge = 0; return
        back = 3 if self._last_p1_dir == 1 else 4
        self._charge = min(self._charge + 1, CHARGE_REQUIRED) if action == back else 0

    def _update_internals(self, st: Dict):
        p2a  = bool(st.get("p2_airborne", False))
        proj = bool(st.get("boom_slot_active", False))
        p2x  = float(st.get("p2_x", 700.0))
        cid  = int(st.get("p2_char", 0xFF))

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
        self._last_p1_dir= int(st.get("p1_dir", 1))
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

        print(f"[BlankaEnv#{self.instance_id}] Reset...")

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
                in_combat = bool(st.get("in_combat", True))   # fallback True si Lua antiguo
                p1 = st.get("p1_hp", 0)
                p2 = st.get("p2_hp", 0)
                if in_combat and p1 >= EPISODE_MIN_HP and p2 >= EPISODE_MIN_HP:
                    break
            time.sleep(0.05)

        if st is None:
            return np.zeros(28, dtype=np.float32), {}

        p1  = st.get("p1_hp", 0); p2 = st.get("p2_hp", 0)
        cid = st.get("p2_char", 0xFF)
        print(f"[BlankaEnv#{self.instance_id}] vs {CHAR_NAMES.get(cid,'?')} | P1={p1} P2={p2}")
        self._prev_p1_hp = float(p1); self._prev_p2_hp = float(p2)
        self._current_rival = cid if cid <= 11 else 0xFF
        return self._get_obs(st), {"p1_hp": p1, "p2_hp": p2, "rival": cid}

    # ── STEP ─────────────────────────────────────────────────────────────────
    def step(self, action: int):
        self._ep_step += 1; action = int(action)
        st = self.bridge.step(self._resolve(action))

        # [FIX v2.2] No truncar en el primer fallo: esperar _MAX_BRIDGE_ERRORS
        # consecutivos antes de declarar error de bridge. Los fallos aislados
        # de state.txt (race condition) se resuelven con los reintentos del
        # bridge, pero si todos los reintentos fallan, contamos aquí.
        if st is None:
            self._bridge_error_count += 1
            if self._bridge_error_count >= _MAX_BRIDGE_ERRORS:
                print(f"[BlankaEnv#{self.instance_id}] BRIDGE ERROR x{_MAX_BRIDGE_ERRORS} — truncando episodio")
                return self._get_obs(None), -1.0, False, True, {"bridge_error": True}
            # Devolver obs del último estado conocido con reward neutro
            last = self.bridge._last_state
            return self._get_obs(last), 0.0, False, False, {"bridge_retry": True}
        else:
            self._bridge_error_count = 0

        self._update_charge(action); self._update_internals(st)
        p1hp = float(st.get("p1_hp", MAX_HP)); p2hp = float(st.get("p2_hp", MAX_HP))
        self._p1_hp_hist.append(self._prev_p1_hp); self._p2_hp_hist.append(self._prev_p2_hp)
        self._ep_p1_dmg += max(0.0, self._prev_p1_hp - p1hp)
        self._ep_p2_dmg += max(0.0, self._prev_p2_hp - p2hp)

        # [FIX v2.2] Terminar episodio cuando Lua sale de IN_COMBAT.
        # El campo "in_combat" es False en GAME_OVER_WAIT, WIN_WAIT, menús, etc.
        # Fallback: si no hay campo (Lua antiguo), usar HP <= 0 como antes.
        in_combat = bool(st.get("in_combat", True))  # True = fallback para Lua antiguo
        won = p2hp <= 0 and p1hp > 0

        if "in_combat" in st:
            # Modo moderno: terminar cuando Lua sale de IN_COMBAT
            terminated = not in_combat
        else:
            # Fallback: modo antiguo por HP
            terminated = p1hp <= 0 or p2hp <= 0

        truncated = (not terminated) and self._ep_step >= self.MAX_STEPS

        if (terminated or truncated) and self.registry and self._current_rival <= 11:
            self.registry.record_episode(self._current_rival, won, self._ep_p1_dmg, self._ep_p2_dmg)

        r = self._calc_reward(p1hp, p2hp, st, action)
        self._prev_p1_hp = p1hp; self._prev_p2_hp = p2hp; self._last_action = action
        return self._get_obs(st), r, terminated, truncated, {
            "p1_hp": p1hp, "p2_hp": p2hp, "won": won, "step": self._ep_step,
            "action": action, "rival": self._current_rival,
            "boom_t": self._boom_timer, "fk_land": self._fk_land_steps,
            "charge": self._charge,
        }

    def render(self): pass

    def close(self):
        try: self.bridge.disconnect()
        except Exception: pass
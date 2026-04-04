# env/reward.py
# =============================================================================
# Reward function para Blanka — Fase Única (26 acciones, sin restricción)
# =============================================================================
# Versión: 1.0 (04/04/2026)
#
# INTERFAZ PRINCIPAL:
#   reward = compute_reward(p1hp, p2hp, prev_p1hp, prev_p2hp, st, action, ctx)
#
# INTEGRACIÓN EN blanka_env.py:
#   Ver sección "CAMBIOS EN blanka_env.py" al final de este archivo.
# =============================================================================

from dataclasses import dataclass, field
from typing import List

# ── CONSTANTES DE REWARD ──────────────────────────────────────────────────────
# Todas en un solo lugar para ajustar sin tocar la lógica.

# Daño base
R_DMG_DEALT         =  8.0   # por punto de HP quitado al rival
R_DMG_TAKEN         = -5.0   # por punto de HP recibido

# Remates (bonus sobre el daño base)
R_KO_BONUS          = 60.0   # rival llega a 0 HP
R_NEAR_KILL_20      = 35.0   # rival queda con < 20 HP bajando
R_NEAR_KILL_40      = 18.0   # rival queda con < 40 HP bajando
R_LOW_HP_PRESSURE   =  8.0   # rival < 70 HP bajando

# Presión proporcional al daño acumulado
R_P2_DANGER_MAX     =  3.0   # máximo cuando rival está en 0 % HP

# Rolling (15-17)
R_ROLL_DMG_BONUS    = 10.0   # daño extra cuando rolling conecta
R_ROLL_FK_SETUP     = 20.0   # rolling en ventana FK (post-salto enemigo)
R_ROLL_GOOD_RANGE   =  6.0   # rolling en rango correcto (180-650 px)
R_ROLL_BAD_RANGE    = -2.0   # rolling fuera de rango sin daño
R_ROLL_ENCOURAGE    =  0.8   # incentivo por intentar rolling en buen rango

# Electric (18)
R_ELEC_DMG_CLOSE    = 16.0   # electric conecta cerca (< 150 px)
R_ELEC_DMG_FAR      =  7.0   # electric conecta lejos
R_ELEC_MISS_CLOSE   =  0.5   # electric cerca sin daño (posición correcta)
R_ELEC_MISS_FAR     = -3.0   # electric lejos sin daño (desperdicio)

# Saltos con ataque (19-24)
R_JUMP_ATK_HIT      = 10.0   # salto con ataque conecta
R_JUMP_ATK_RANGE    =  5.0   # bonus por rango óptimo (140-520 px)
R_JUMP_HIT_TAKEN    = -5.0   # recibió daño en el aire (anti-air rival)

# Rolling Jump (25)
R_RJUMP_HIT_WINDOW  = 28.0   # rolling jump en ventana + conecta
R_RJUMP_WINDOW_MISS =  5.0   # rolling jump en ventana pero falla
R_RJUMP_NO_WINDOW   = -4.0   # rolling jump fuera de ventana

# Defensa / Esquiva
R_DODGE_PROJECTILE  =  2.5   # Blanka en el aire con boom activo (esquiva)
R_ANTI_AIR_HIT      =  8.0   # golpea al rival mientras está en el aire
R_STUN_BUILDING     =  2.0   # stun del rival supera 150 (cerca del KO mental)
R_CORNER_PRESSURE   =  0.4   # rival en esquina

# Posicionamiento
R_CHARGE_BACK       =  0.25  # mantener back (carga rolling)
R_IDLE_PENALTY      = -3.0   # 60+ steps sin daño en ninguna dirección
R_DISTANCE_PENALTY  = -0.05  # distancia > 700 px sin daño
R_ARCADE_CLEAR      = 200.0  # bonus de arcade clear

# Anti-spam
R_SPAM_PENALTY      = -1.5   # misma acción ≥ 5 de 8 últimas sin daño

# ── Límites de juego (deben coincidir con los de blanka_env.py) ───────────────
MAX_HP              = 144.0
ELECTRIC_MAX_DIST   = 150
LANDING_WINDOW      = 8

ROLLING_ACTIONS     = {15, 16, 17}
ACTION_ELECTRIC     = 18
JUMP_ACTIONS        = {19, 20, 21, 22, 23, 24}


# ── CONTEXTO DEL ENTORNO ──────────────────────────────────────────────────────

@dataclass
class BlankaContext:
    """
    Snapshot de los atributos internos de BlankaEnv necesarios para
    calcular el reward. Se construye en blanka_env._calc_reward() y
    se pasa a compute_reward() para mantener reward.py sin imports
    circulares ni referencias a la clase del entorno.
    """
    ep_step:             int
    fk_land_steps:       int
    macro_action_id:     int
    p2_was_air:          bool
    p1_land_steps:       int
    last_p1_dir:         int   # 1 = mirando derecha, 0 = izquierda
    arcade_just_cleared: bool
    last_actions_hist:   List[int] = field(default_factory=list)


# ── FUNCIÓN PRINCIPAL ─────────────────────────────────────────────────────────

def compute_reward(
    p1hp:       float,
    p2hp:       float,
    prev_p1hp:  float,
    prev_p2hp:  float,
    st:         dict,
    action:     int,
    ctx:        BlankaContext,
    extra_bonus: float = 0.0,
) -> float:
    """
    Calcula el reward del step actual.

    Parámetros
    ----------
    p1hp / p2hp       : HPs actuales de Blanka y el rival (swap v5.12 aplicado)
    prev_p1hp / prev_p2hp : HPs del step anterior
    st                : state dict completo del bridge MAME
    action            : acción real ejecutada (0-25, sin mapeo cl1)
    ctx               : BlankaContext con el snapshot del estado interno del env
    extra_bonus       : reward adicional por eventos de ronda/match

    Retorna
    -------
    float : reward total del step
    """
    # ── Deltas de HP ──────────────────────────────────────────────────────────
    dp1 = max(0.0, prev_p1hp - p1hp)   # daño recibido por Blanka
    dp2 = max(0.0, prev_p2hp - p2hp)   # daño infligido al rival

    # ── Contexto del bridge ───────────────────────────────────────────────────
    p1x   = float(st.get("p1_x",        700.0))
    p2x   = float(st.get("p2_x",        700.0))
    p1a   = bool (st.get("p1_airborne", False))
    p2a   = bool (st.get("p2_airborne", False))
    p2stn = float(st.get("p2_stun",     0))
    boom  = bool (st.get("boom_slot_active", False))
    dist  = abs(p1x - p2x)

    r = extra_bonus

    # ── Daño base ─────────────────────────────────────────────────────────────
    r += dp2 * R_DMG_DEALT
    r -= dp1 * R_DMG_TAKEN

    # ── Remates ───────────────────────────────────────────────────────────────
    if p2hp <= 0 and dp2 > 0:
        r += R_KO_BONUS
    elif p2hp < 20 and dp2 > 0:
        r += R_NEAR_KILL_20
    elif p2hp < 40 and dp2 > 0:
        r += R_NEAR_KILL_40
    elif p2hp < 70 and dp2 > 0:
        r += R_LOW_HP_PRESSURE

    # ── Presión proporcional ──────────────────────────────────────────────────
    p2hp_frac = p2hp / MAX_HP
    if p2hp_frac < 0.65:
        r += (0.65 - p2hp_frac) / 0.65 * R_P2_DANGER_MAX

    # ── ESPECIALES ────────────────────────────────────────────────────────────

    # Rolling (acción directa o macro activa)
    rolling_active = (action in ROLLING_ACTIONS or
                      ctx.macro_action_id in ROLLING_ACTIONS)
    if rolling_active:
        in_fk_window = 0 < ctx.fk_land_steps <= 20
        good_range   = 180 <= dist <= 650
        if dp2 > 0:
            r += R_ROLL_DMG_BONUS
            if in_fk_window:
                r += R_ROLL_FK_SETUP
            elif good_range:
                r += R_ROLL_GOOD_RANGE
        elif action in ROLLING_ACTIONS:
            # Solo penalizar/premiar cuando es la decisión del agente, no macro
            r += R_ROLL_ENCOURAGE if good_range else R_ROLL_BAD_RANGE

    # Electric
    electric_active = (action == ACTION_ELECTRIC or
                       ctx.macro_action_id == ACTION_ELECTRIC)
    if electric_active:
        close = dist < ELECTRIC_MAX_DIST
        if dp2 > 0:
            r += R_ELEC_DMG_CLOSE if close else R_ELEC_DMG_FAR
        elif action == ACTION_ELECTRIC:
            r += R_ELEC_MISS_CLOSE if close else R_ELEC_MISS_FAR

    # ── SALTOS CON ATAQUE (19-24) ──────────────────────────────────────────────
    if action in JUMP_ACTIONS:
        if dp2 > 0:
            r += R_JUMP_ATK_HIT
            if 140 <= dist <= 520:
                r += R_JUMP_ATK_RANGE
        elif p1a and dp1 > 0:
            r += R_JUMP_HIT_TAKEN

    # ── ROLLING JUMP (25) ─────────────────────────────────────────────────────
    if action == 25:
        in_window = (ctx.p1_land_steps > 0 and
                     ctx.p1_land_steps <= LANDING_WINDOW)
        if in_window and dp2 > 0:
            r += R_RJUMP_HIT_WINDOW
        elif in_window:
            r += R_RJUMP_WINDOW_MISS
        else:
            r += R_RJUMP_NO_WINDOW

    # ── DEFENSA Y ESQUIVA ──────────────────────────────────────────────────────
    if p1a and boom:
        r += R_DODGE_PROJECTILE

    if ctx.p2_was_air and not p2a and dp2 > 0:
        r += R_ANTI_AIR_HIT

    if p2stn > 150:
        r += R_STUN_BUILDING

    # ── POSICIONAMIENTO ───────────────────────────────────────────────────────
    back = 3 if ctx.last_p1_dir == 1 else 4
    if action == back or action in (23, 24):
        r += R_CHARGE_BACK

    if p2x < 120 or p2x > 1280:
        r += R_CORNER_PRESSURE

    # ── PENALIZACIONES ────────────────────────────────────────────────────────
    if ctx.ep_step > 60 and dp2 == 0 and dp1 == 0:
        r += R_IDLE_PENALTY

    if dist > 700 and dp2 == 0:
        r += R_DISTANCE_PENALTY

    # Anti-spam: misma acción ≥ 5 de las 8 últimas sin causar daño
    if len(ctx.last_actions_hist) >= 6:
        spam = sum(1 for a in ctx.last_actions_hist if a == action)
        if spam >= 5 and dp2 == 0:
            r += R_SPAM_PENALTY

    # ── ARCADE CLEAR ──────────────────────────────────────────────────────────
    if ctx.arcade_just_cleared:
        r += R_ARCADE_CLEAR

    return float(r)


# =============================================================================
# CAMBIOS EN blanka_env.py
# =============================================================================
# Son 4 cambios pequeños. No tocar nada más.
#
# 1. IMPORT (al inicio del archivo, junto al resto de imports):
# ─────────────────────────────────────────────────────────────
#    from env.reward import compute_reward, BlankaContext
#
#
# 2. __init__: añadir UN atributo nuevo al final de los existentes:
# ─────────────────────────────────────────────────────────────────
#    self._last_actions_hist: deque = deque(maxlen=8)
#
#
# 3. reset(): añadir UN reset junto al resto:
# ────────────────────────────────────────────
#    self._last_actions_hist.clear()
#
#
# 4. _calc_reward(): REEMPLAZAR el método completo por este wrapper:
# ──────────────────────────────────────────────────────────────────
#    def _calc_reward(self, p1hp: float, p2hp: float, st: dict,
#                     action: int, extra_bonus: float = 0.0) -> float:
#        ctx = BlankaContext(
#            ep_step             = self._ep_step,
#            fk_land_steps       = self._fk_land_steps,
#            macro_action_id     = self._macro_action_id,
#            p2_was_air          = self._p2_was_air,
#            p1_land_steps       = self._p1_land_steps,
#            last_p1_dir         = self._last_p1_dir,
#            arcade_just_cleared = self._arcade_just_cleared,
#            last_actions_hist   = list(self._last_actions_hist),
#        )
#        reward = compute_reward(
#            p1hp, p2hp,
#            self._prev_p1_hp, self._prev_p2_hp,
#            st, action, ctx, extra_bonus,
#        )
#        # Consumir el flag arcade_just_cleared después de usarlo
#        if self._arcade_just_cleared:
#            self._arcade_just_cleared = False
#        return reward
#
#
# 5. step() — CASO B (EN COMBATE): añadir UNA línea antes de _calc_reward:
# ──────────────────────────────────────────────────────────────────────────
#    self._last_actions_hist.append(_action_real)   # historial anti-spam
#    reward = self._calc_reward(p1hp, p2hp, st, action, extra_bonus=extra_reward)
#
# =============================================================================
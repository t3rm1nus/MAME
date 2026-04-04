# =============================================================================
# CONFIGURACIÓN GLOBAL — DIRECCIONES RAM Y LÓGICA DE DETECCIÓN (02/04/2026)
# =============================================================================
# ESTADO:
#   FASE 1 COMPLETADA ✅  — Control de Blanka (Rolling, Electricidad)
#   FASE 2 COMPLETADA ✅  — P1/P2 char, modo ARCADE/VS, cronómetro
#   FASE 3.1 COMPLETADA ✅ — Estado dinámico P2 (crouch, stun, X, Y-vel)
#   FASE 3.2 COMPLETADA ✅ — FK anatomía definitiva. Sonic Boom: workaround activo
#                            (PROJ_X no existe en RAM como entidad separada)
# =============================================================================

from pathlib import Path
import json
# ==================== DIRECCIONES RAM — ESTADO MAESTRO =======================
# Encontrado vía scanner_fsm.py el 31/03/2026.
# Mapeo de estados (FSM) confirmado Abril 2026.
GAME_STATE_ADDR = 0xFF8005

# MAPA DE GAME_STATE — SF2CE CONFIRMADO
GAME_STATE_BOOT_TITLE        = 0x00  # Boot / Título / Atraer (HP=0)
GAME_STATE_INSERT_COIN       = 0x02  # Insert Coin / Press Start (HP=0)
GAME_STATE_COMBAT_SELECT     = 0x04  # Char Select + VS Screen + Combate (HP>0 si en combate)
GAME_STATE_ROUND_TRANSITION  = 0x06  # Transición / Round Over inter-rondas (HP=0)
GAME_STATE_GAME_OVER         = 0x08  # Game Over / Continue (HP=0)
GAME_STATE_CONTINUE_EXPIRED  = 0x0A  # Continue expirado → vuelta a título

GAME_STATE_NAMES = {
    GAME_STATE_BOOT_TITLE: "BOOT/TITLE",
    GAME_STATE_INSERT_COIN: "INSERT_COIN/PRESS_START",
    GAME_STATE_COMBAT_SELECT: "CHAR_SELECT/VS_SCREEN/COMBAT",
    GAME_STATE_ROUND_TRANSITION: "ROUND_TRANSITION",
    GAME_STATE_GAME_OVER: "GAME_OVER/CONTINUE",
    GAME_STATE_CONTINUE_EXPIRED: "CONTINUE_EXPIRED"
}

def get_game_state(ram_reader) -> int:
    return ram_reader.read_u8(GAME_STATE_ADDR)

def get_game_state_name(state_value: int) -> str:
    return GAME_STATE_NAMES.get(state_value, f"UNKNOWN_STATE_{hex(state_value)}")


# Lista de nombres para las 26 acciones de Blanka
ACTION_NAMES = [
    "NOOP", "UP", "DOWN", "LEFT", "RIGHT",
    "LP", "MP", "HP", "LK", "MK", "HK",
    "D+LP", "D+MP", "D+HP", "D+LK", "D+MK", "D+HK",  # 11-16
    "ROLLING_F", "ROLLING_S", "ROLLING_J",             # 17-19
    "ELECTRIC",                                        # 20
    "JUMP_F", "JUMP_N", "JUMP_B",                      # 21-23
    "JUMP_F_ATTACK", "JUMP_N_ATTACK"                   # 24-25
]

# --- VIDA ---
P1_HP_ADDR     = 0xFF83E9
P2_HP_ADDR     = 0xFF86E9
P2_HP_DISPLAY2 = 0xFF86EB  # display secundario; lag 1-2f vs E9

# --- LADO ---
P1_SIDE_ADDR = 0xFF83D0   # 9 flips reales
P2_SIDE_ADDR = 0xFF86D0   # 18 flips reales

# Rounds ganados — confirmado por escaneo diagnóstico Abril 2026
P1_ROUND_WIN_SIG = 0xFF8A3F   # >0 cuando P1 acaba de ganar una ronda
P2_ROUND_WIN_SIG = 0xFF8A41   # >0 cuando P2 acaba de ganar una ronda

P2_CHAR_ADDR = 0xFF8660   # ✅ char ID del rival activo, se actualiza entre combates

# Alias legacy para no romper código que todavía referencie P1_CHAR_ADDR
# (antes apuntaba erróneamente a 0xFF864F con el nombre "P1"). Eliminar en v3.0.
P1_CHAR_ADDR         = 0xFF834F   # Siempre = 0 en SF2CE (Blanka no se auto-registra aquí)
P2_CHAR_ADDR_LEGACY  = 0xFF894F   # ⚠️  LEGACY — NO usar. Devuelve siempre 3 en headless.
P2_CHAR_SCAN_BASE    = 0xFF894F   # ⚠️  LEGACY — método scan flags, ya no necesario.

# ==================== MAPA DE PERSONAJES (SF2CE) =============================
#
# IDs confirmados empíricamente 02/04/2026:
#   2 → Guile   (visible, Terminal 1)
#   3 → Guile   (headless, legacy — era el valor residual de 0xFF894F)
#   4 → Ken     (headless, Terminal 2, combate 1)
#   8 → M.Bison (headless, Terminal 2, combate 2)
#  11 → Vega    (headless, Terminal 2, combate 3)
#
# El orden completo del select screen de SF2CE (fila superior izquierda→derecha,
# fila inferior izquierda→derecha) mapea directamente al ID de entidad:

CHAR_MAP = {
    0:"Ryu", 1:"E.Honda", 2:"Blanka", 3:"Guile",
    4:"Ken", 5:"Chun-Li", 6:"Zangief", 7:"Dhalsim",
    8:"M.Bison", 9:"Sagat", 10:"Balrog", 11:"Vega",
}

# ID de Blanka (P1) — constante durante todo el arcade
BLANKA_CHAR_ID = 3

CHAR_SELECT_FILE = "char_select.txt"

# --- MODO ARCADE vs VS (confirmado 27/03/2026) ---
MODE_BLOCK_START = 0xFF87E0
MODE_BLOCK_END   = 0xFF87FF
MODE_BLOCK_SIZE  = 32

# --- CRONÓMETRO ---
TIMER_ADDR = 0xFF8ACE

# ==================== POSICIÓN X — CONFIRMADO ORO (28/03/2026) ===============

# P1 (Blanka) posición X — 16-bit big-endian
P1_X_H_ADDR = 0xFF917C   # ✅ byte alto
P1_X_L_ADDR = 0xFF917D   # ✅ byte bajo

# P2 posición X — 16-bit big-endian
P2_X_H_ADDR = 0xFF927C   # ✅ byte alto
P2_X_L_ADDR = 0xFF927D   # ✅ byte bajo

def read_p1_x(ram_reader) -> int:
    h = ram_reader.read_u8(P1_X_H_ADDR)
    l = ram_reader.read_u8(P1_X_L_ADDR)
    return (h << 8) | l

def read_p2_x(ram_reader) -> int:
    h = ram_reader.read_u8(P2_X_H_ADDR)
    l = ram_reader.read_u8(P2_X_L_ADDR)
    return (h << 8) | l

# ==================== ESTADO DINÁMICO P2 (Fase 3.1) =========================

# --- STUN ---
P2_STUN_ADDR        = 0xFF865A   # ✅ acumula +5 por hit recibido
P2_STUN_SPRITE_ADDR = 0xFF8951   # ✅ 0x24 = en stun (pajaritos)
P1_STUN_ADDR        = 0xFF895A   # ✅ acumula +5 por hit recibido (simétrico)

# --- POSE ---
P2_CROUCH_FLAG_ADDR = 0xFF86C4   # ✅ 0x03=agachado | 0x02=de pie

# --- ANIMACIÓN / ATAQUE ---
P2_ANIM_FRAME_ADDR = 0xFF86C1    # ✅ contador frame animación

# --- VELOCIDAD VERTICAL (airborne) ---
P2_Y_VEL_H_ADDR = 0xFF86FC      # ✅ signed 16-bit; abs > 256 = en el aire
P2_Y_VEL_L_ADDR = 0xFF86FD

# ==================== FLASH KICK (FK) — ANATOMÍA DEFINITIVA (28/03/2026) ====

FK_ANIM_STARTUP  = 0x0C
FK_ANIM_ASCENT   = 0x02
FK_ANIM_APEX     = 0x00
FK_ANIM_DESCENT  = 0x04
FK_ANIM_LANDING  = 0x0C

FK_YVEL_STARTUP  = -288
FK_YVEL_ASCENT   = -2304
FK_YVEL_DESCENT  = +1760
FK_YVEL_GROUND   = 0

FK_FRAME_STARTUP        = 0
FK_FRAME_ASCENT         = 26
FK_FRAME_APEX           = 123
FK_FRAME_DESCENT        = 126
FK_FRAME_LANDING        = 150
FK_TOTAL_FRAMES         = 150
FK_ABORTED_FRAMES       = 24

def is_p2_fk_airborne(ram_reader) -> bool:
    h = ram_reader.read_u8(P2_Y_VEL_H_ADDR)
    l = ram_reader.read_u8(P2_Y_VEL_L_ADDR)
    raw = (h << 8) | l
    signed = raw if raw < 0x8000 else raw - 0x10000
    return abs(signed) > 256

def fk_phase(anim_frame: int, y_vel_signed: int) -> str:
    airborne = abs(y_vel_signed) > 256
    if anim_frame == FK_ANIM_ASCENT and airborne:
        return "FK_ASCENT"
    if anim_frame == FK_ANIM_APEX and airborne:
        return "FK_APEX"
    if anim_frame == FK_ANIM_DESCENT and airborne:
        return "FK_DESCENT"
    if anim_frame == 0x0C:
        if airborne:
            return "FK_STARTUP"
        else:
            return "BOOM_THROW_OR_FK_LANDING"
    if anim_frame == 0x00 and not airborne:
        return "GROUND_IDLE"
    return "UNKNOWN"

# ==================== PROYECTIL SONIC BOOM (Fase 3.2 — CONCLUSIÓN) ===========

PROJ_SLOT_FLAG_ADDR = 0xFF8E30   # ✅ 0x00 → 0xA4 al primer lanzamiento
PROJ_SLOT_FLAG_VAL  = 0xA4

PROJ_IMPACT_ADDR = 0xFF8E00      # ✅ 0x98 ≈ 0.5s antes del impacto real
PROJ_IMPACT_VAL  = 0x98

PROJ_X_ADDR     = None
BOOM_VEL_APPROX = 25

def estimate_boom_x(p2_x: int, frames_since_throw: int) -> int:
    if frames_since_throw <= 0:
        return -1
    return max(0, p2_x - frames_since_throw * BOOM_VEL_APPROX)

def is_boom_incoming(ram_reader) -> bool:
    return ram_reader.read_u8(PROJ_IMPACT_ADDR) == PROJ_IMPACT_VAL

def is_boom_slot_active(ram_reader) -> bool:
    return ram_reader.read_u8(PROJ_SLOT_FLAG_ADDR) == PROJ_SLOT_FLAG_VAL

def is_p2_throwing(ram_reader) -> bool:
    anim = ram_reader.read_u8(P2_ANIM_FRAME_ADDR)
    if anim != 0x0C:
        return False
    h = ram_reader.read_u8(P2_Y_VEL_H_ADDR)
    l = ram_reader.read_u8(P2_Y_VEL_L_ADDR)
    raw = (h << 8) | l
    signed = raw if raw < 0x8000 else raw - 0x10000
    return abs(signed) <= 256

# ==================== FUNCIONES DE DETECCIÓN =================================

def char_name(char_id: int) -> str:
    return CHAR_MAP.get(char_id, f"ID_{char_id}")

def detect_mode(ram_reader) -> str:
    for i in range(MODE_BLOCK_SIZE):
        if ram_reader.read_u8(MODE_BLOCK_START + i) != 0:
            return "ARCADE"
    return "VS"

def is_p2_stunned(ram_reader) -> bool:
    return ram_reader.read_u8(P2_STUN_SPRITE_ADDR) == 0x24

def is_p2_crouching(ram_reader) -> bool:
    return ram_reader.read_u8(P2_CROUCH_FLAG_ADDR) == 0x03

def is_p2_airborne(ram_reader) -> bool:
    h = ram_reader.read_u8(P2_Y_VEL_H_ADDR)
    l = ram_reader.read_u8(P2_Y_VEL_L_ADDR)
    raw = (h << 8) | l
    signed = raw if raw < 0x8000 else raw - 0x10000
    return abs(signed) > 256

def read_char_select() -> dict:
    path = Path(CHAR_SELECT_FILE)
    default = {"p1":"Unknown","p1_id":0xFF,"p2":"Unknown","p2_id":0xFF,"mode":"UNKNOWN"}
    if not path.exists():
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default

# =============================================================================
# HISTORIAL
# =============================================================================
# 25/03/2026 — Fase 1 completada (Rolling, Electricidad).
# 26/03/2026 — P1_CHAR/P2_CHAR confirmados (FF864F/FF894F).
# 27/03/2026 — MODO ARCADE/VS (FF87E0). TIMER=FF8ACE.
#              P2_STUN=FF865A, P1_STUN=FF895A confirmados.
#              P2_CROUCH_FLAG=FF86C4 (valor 0x01 — corregido abajo).
#              P2_Y_VEL=FF86FC-FD candidato. P2_STUN_SPRITE=FF8951 (0x24).
# 28/03/2026 — P2_CROUCH_FLAG valor canónico corregido a 0x03.
#              P2_X CONFIRMADO: FF927C(high)+FF927D(low) 16-bit.
#              P1_X CONFIRMADO: FF917C(high)+FF917D(low) por simetría CPS1.
#              PROJ_SLOT_FLAG (FF8E30=0xA4) confirmado: boom slot activo.
#              PROJ_IMPACT (FF8E00=0x98) confirmado: aparece ~0.5s pre-daño.
#              PROJ_X en vuelo: DESCARTADA hipótesis FF937C.
#              FK ANATOMÍA DEFINITIVA (mapeo_guile_v4):
#                Secuencia: 0x0C(-288)→0x02(-2304)→0x00(-2304)→0x04(+1760)→0x0C(~0)
# =============================================================================
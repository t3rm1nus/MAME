# =============================================================================
# CONFIGURACIÓN GLOBAL — DIRECCIONES RAM Y LÓGICA DE DETECCIÓN (28/03/2026)
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

# ==================== DIRECCIONES RAM — ORO PURO (NO MODIFICAR) ==============


# ==================== DIRECCIONES RAM — ESTADO MAESTRO =======================
# Encontrado vía scanner_fsm.py el 31/03/2026
GAME_STATE_ADDR = 0xFF8005
def get_game_state(ram_reader) -> int:
    return ram_reader.read_u8(GAME_STATE_ADDR)

# --- VIDA ---
P1_HP_ADDR     = 0xFF83E9
P2_HP_ADDR     = 0xFF86E9
P2_HP_DISPLAY2 = 0xFF86EB  # display secundario; lag 1-2f vs E9

# --- LADO ---
P1_SIDE_ADDR = 0xFF83D0   # 9 flips reales
P2_SIDE_ADDR = 0xFF86D0   # 18 flips reales

# --- PERSONAJES (confirmados 26/03/2026) ---
P1_CHAR_ADDR = 0xFF864F   # Byte directo, ID 0-11
P2_CHAR_ADDR = 0xFF894F   # Byte directo, ID 0-11

# --- MODO ARCADE vs VS (confirmado 27/03/2026) ---
MODE_BLOCK_START = 0xFF87E0
MODE_BLOCK_END   = 0xFF87FF
MODE_BLOCK_SIZE  = 32

# --- CRONÓMETRO ---
TIMER_ADDR = 0xFF8ACE

# ==================== POSICIÓN X — CONFIRMADO ORO (28/03/2026) ===============

# P1 (Blanka) posición X — 16-bit big-endian (confirmado por simetría con P2)
P1_X_H_ADDR = 0xFF917C   # ✅ byte alto
P1_X_L_ADDR = 0xFF917D   # ✅ byte bajo

# P2 (Guile) posición X — 16-bit big-endian
# Evidencia: carry byte alto 02→03 durante throw. Rango: 728–855.
# Coordenadas mundo CPS1: valores MAYORES = más a la DERECHA.
P2_X_H_ADDR = 0xFF927C   # ✅ byte alto (16-bit big-endian)
P2_X_L_ADDR = 0xFF927D   # ✅ byte bajo

def read_p1_x(ram_reader) -> int:
    """Devuelve la posición X de P1 como entero 16-bit sin signo."""
    h = ram_reader.read_u8(P1_X_H_ADDR)
    l = ram_reader.read_u8(P1_X_L_ADDR)
    return (h << 8) | l

def read_p2_x(ram_reader) -> int:
    """Devuelve la posición X de P2 como entero 16-bit sin signo."""
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
# NOTA 28/03: valor canónico agachado = 0x03 (sesión anterior marcó 0x01 por error)

# --- ANIMACIÓN / ATAQUE ---
P2_ANIM_FRAME_ADDR = 0xFF86C1    # ✅ contador frame animación
# Valores clave: 0x00=idle | 0x02=ascenso FK | 0x04=descenso FK | 0x0A=normales pie
#                0x0C=Sonic Boom throw Y FK startup/landing (desambiguar con Y_VEL)

# --- VELOCIDAD VERTICAL (airborne) ---
P2_Y_VEL_H_ADDR = 0xFF86FC      # ✅ signed 16-bit; abs > 256 = en el aire
P2_Y_VEL_L_ADDR = 0xFF86FD

# ==================== FLASH KICK (FK) — ANATOMÍA DEFINITIVA (28/03/2026) ====
# Fuente: mapeo_guile_v4.txt — oro puro, NO modificar sin nueva evidencia
#
# Duración total airborne REAL: ~150 frames (FK completo con arco).
# FK "abortado": ~24 frames (solo startup + landing sin arco completo).
#
# SECUENCIA CANÓNICA DE ANIM_FRAME durante el arco completo:
#   f+0    ANIM=0x0C  Y_VEL=-288   → Startup / despegue
#   f+26   ANIM=0x02  Y_VEL=-2304  → Ascenso activo (hitbox activa)
#   f+123  ANIM=0x00  Y_VEL=-2304  → Cima del arco
#   f+126  ANIM=0x04  Y_VEL=+1760  → Descenso / caída
#   f+150  ANIM=0x0C  Y_VEL~0      → Landing / Recovery
#
# ADVERTENCIA: ANIM=0x0C es AMBIGUO:
#   - Aparece en Sonic Boom throw (tierra, Y_VEL~0)
#   - Aparece en FK startup (despegue, Y_VEL=-288)
#   - Aparece en FK landing (recovery, Y_VEL~0)
#   DESAMBIGUAR: abs(Y_VEL) > 256 → airborne → FK. Y_VEL~0 → tierra → Boom throw o Recovery.
#
# NOTA ADICIONAL: algunos FK arrancan con ANIM=0x04 (descenso) como primer frame
#   observado (FK#1, FK#3 del log), probablemente por timing de detección.
#   La forma más fiable de detectar inicio es el flanco airborne (Y_VEL abs > 256).

FK_ANIM_STARTUP  = 0x0C   # startup del despegue (también Boom throw y FK landing)
FK_ANIM_ASCENT   = 0x02   # ascenso activo (hitbox)
FK_ANIM_APEX     = 0x00   # cima del arco
FK_ANIM_DESCENT  = 0x04   # descenso / caída
FK_ANIM_LANDING  = 0x0C   # recovery (mismo código que startup — desambiguar por Y_VEL)

FK_YVEL_STARTUP  = -288    # Y_VEL signed al despegue
FK_YVEL_ASCENT   = -2304   # Y_VEL signed en ascenso
FK_YVEL_DESCENT  = +1760   # Y_VEL signed en descenso
FK_YVEL_GROUND   = 0       # Y_VEL en tierra

FK_FRAME_STARTUP        = 0    # frame relativo inicio
FK_FRAME_ASCENT         = 26   # frame relativo inicio ascenso activo
FK_FRAME_APEX           = 123  # frame relativo cima
FK_FRAME_DESCENT        = 126  # frame relativo inicio descenso
FK_FRAME_LANDING        = 150  # frame relativo landing/recovery (FK completo)
FK_TOTAL_FRAMES         = 150  # duración total del arco airborne (FK real)
FK_ABORTED_FRAMES       = 24   # FK abortado: solo startup sin arco completo

def is_p2_fk_airborne(ram_reader) -> bool:
    """True si P2 está actualmente airborne (FK en vuelo). No distingue fase."""
    h = ram_reader.read_u8(P2_Y_VEL_H_ADDR)
    l = ram_reader.read_u8(P2_Y_VEL_L_ADDR)
    raw = (h << 8) | l
    signed = raw if raw < 0x8000 else raw - 0x10000
    return abs(signed) > 256

def fk_phase(anim_frame: int, y_vel_signed: int) -> str:
    """Clasifica la fase del FK dado ANIM_FRAME y Y_VEL signed.
    Devuelve: 'FK_STARTUP', 'FK_ASCENT', 'FK_APEX', 'FK_DESCENT',
              'FK_LANDING_RECOVERY', 'BOOM_THROW', 'GROUND_IDLE', 'UNKNOWN'
    """
    airborne = abs(y_vel_signed) > 256
    if anim_frame == FK_ANIM_ASCENT and airborne:
        return "FK_ASCENT"
    if anim_frame == FK_ANIM_APEX and airborne:
        return "FK_APEX"
    if anim_frame == FK_ANIM_DESCENT and airborne:
        return "FK_DESCENT"
    if anim_frame == 0x0C:
        if airborne:
            return "FK_STARTUP"     # despegue con Y_VEL=-288
        else:
            # En tierra: puede ser Boom throw O FK landing/recovery
            # Distinguir con contexto (frame previo airborne → landing)
            return "BOOM_THROW_OR_FK_LANDING"
    if anim_frame == 0x00 and not airborne:
        return "GROUND_IDLE"
    return "UNKNOWN"

# ==================== PROYECTIL SONIC BOOM (Fase 3.2 — CONCLUSIÓN) ===========
# Fuente: mapeo_guile_v4.txt + mapeo_boom_v1.txt (sesión 28/03/2026)
#
# CONCLUSIÓN DEFINITIVA: El Sonic Boom NO tiene entidad de proyectil en RAM
#   con X propia en los bloques 0xFF9300-0xFF9500.
#   Los slots ENT_PROJ_A/B/C (0xFF9300-0xFF95FF) permanecen a CERO durante
#   todo el vuelo del boom. Hipótesis 0xFF937C offset+0x7C = DESCARTADA.
#
#   El bloque 0xFF9200 (ENT_P2) en offset+0x7C contiene la X de Guile (P2),
#   NO la del proyectil. Confirmado en Boom#1-5: el valor en +0x7C siempre
#   coincide con P2_X en el momento del throw.
#
#   La X del boom en vuelo probablemente reside en un bloque de sprites/objetos
#   fuera del rango 0xFF9300-0xFF9500 escaneado. Requiere barrido más amplio
#   o análisis de la tabla de objetos de CPS1.
#
# WORKAROUND ACTIVO para el agente PPO:
#   1. Detectar lanzamiento: P2_ANIM_FRAME == 0x0C con Y_VEL~0 (tierra)
#   2. Estimar X del boom: P2_X - (frames_desde_throw × BOOM_VEL_APPROX)
#   3. Detectar impacto inminente: PROJ_IMPACT_ADDR == 0x98 (~0.5s pre-daño)

PROJ_SLOT_FLAG_ADDR = 0xFF8E30   # ✅ 0x00 → 0xA4 al primer lanzamiento
PROJ_SLOT_FLAG_VAL  = 0xA4

PROJ_IMPACT_ADDR = 0xFF8E00      # ✅ 0x98 ≈ 0.5s antes del impacto real
PROJ_IMPACT_VAL  = 0x98

# X del proyectil: NO EXISTE como dirección de RAM independiente confirmada
# Los bloques ENT_PROJ_A/B/C (0xFF9300-0xFF9500) permanecen a cero durante vuelo.
PROJ_X_ADDR     = None    # DESCARTADA la hipótesis 0xFF937C
BOOM_VEL_APPROX = 25      # unidades coord mundo / frame (estimado)

def estimate_boom_x(p2_x: int, frames_since_throw: int) -> int:
    """
    Estima la X del Sonic Boom. Guile está a la derecha, el boom va a la izquierda.
    Devuelve -1 si no hay boom activo (frames_since_throw <= 0).
    """
    if frames_since_throw <= 0:
        return -1
    return max(0, p2_x - frames_since_throw * BOOM_VEL_APPROX)

def is_boom_incoming(ram_reader) -> bool:
    """True si hay impacto de boom inminente (~0.5s antes del daño)."""
    return ram_reader.read_u8(PROJ_IMPACT_ADDR) == PROJ_IMPACT_VAL

def is_boom_slot_active(ram_reader) -> bool:
    """True si Guile ha lanzado al menos un boom en este combate."""
    return ram_reader.read_u8(PROJ_SLOT_FLAG_ADDR) == PROJ_SLOT_FLAG_VAL

def is_p2_throwing(ram_reader) -> bool:
    """
    True si P2 está en animación de lanzamiento de Sonic Boom.
    IMPORTANTE: ANIM=0x0C también aparece en FK startup y FK landing.
    Esta función es correcta SOLO si se verifica previamente que P2 está en tierra.
    """
    anim = ram_reader.read_u8(P2_ANIM_FRAME_ADDR)
    if anim != 0x0C:
        return False
    # Verificar que está en tierra (Y_VEL ~ 0)
    h = ram_reader.read_u8(P2_Y_VEL_H_ADDR)
    l = ram_reader.read_u8(P2_Y_VEL_L_ADDR)
    raw = (h << 8) | l
    signed = raw if raw < 0x8000 else raw - 0x10000
    return abs(signed) <= 256

# ==================== MAPA DE PERSONAJES (SF2CE) =============================

CHAR_MAP = {
    0:"Ryu", 1:"E.Honda", 2:"Blanka", 3:"Guile",
    4:"Ken", 5:"Chun-Li", 6:"Zangief", 7:"Dhalsim",
    8:"M.Bison", 9:"Sagat", 10:"Balrog", 11:"Vega",
}

BLANKA_ID = 2
GUILE_ID  = 3

CHAR_SELECT_FILE = "char_select.txt"

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
    """Valor canónico agachado = 0x03."""
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
#                Evidencia definitiva: carry byte alto 02→03 durante throw anim.
#                Rango observado: 0x02D8(728)..0x0357(855).
#              P1_X CONFIRMADO: FF917C(high)+FF917D(low) por simetría CPS1.
#                Evidencia: aparece en logs de mapeo_guile_v4 (P1_X=552→437).
#              PROJ_SLOT_FLAG (FF8E30=0xA4) confirmado: boom slot activo.
#              PROJ_IMPACT (FF8E00=0x98) confirmado: aparece ~0.5s pre-daño.
#              PROJ_X en vuelo: DESCARTADA hipótesis FF937C.
#                ENT_PROJ_A/B/C (FF9300-FF9500) permanecen a cero durante vuelo.
#                Workaround activo: P2_X - frames_desde_throw × 25 ud/frame.
#              FK ANATOMÍA DEFINITIVA (mapeo_guile_v4):
#                Secuencia: 0x0C(-288) → 0x02(-2304) → 0x00(-2304) → 0x04(+1760) → 0x0C(~0)
#                Duración real ~150f. FK abortado ~24f.
#                ANIM=0x0C ambiguo: desambiguar con Y_VEL (abs>256=airborne=FK).
# =============================================================================
-- =============================================================================
-- lua_bridge_v2.lua — Bridge Python↔MAME para entrenamiento PPO Blanka
-- Versión: 2.0 (28/03/2026) — Direcciones RAM oro puro Fase 3.2
-- =============================================================================
-- ARQUITECTURA:
--   · Lua escribe state.txt cada frame con todo el estado del juego
--   · Python escribe mame_input.txt con los inputs del agente
--   · El bucle: Lua lee input → aplica → lee RAM → escribe state → siguiente frame
--
-- DIRECCIONES RAM CONFIRMADAS (NO MODIFICAR):
--   P1_HP   = 0xFF83E9 | P2_HP   = 0xFF86E9
--   P1_X    = 0xFF917C-7D (16-bit big-endian)
--   P2_X    = 0xFF927C-7D (16-bit big-endian)
--   P2_CROUCH    = 0xFF86C4  (0x03=agachado)
--   P2_ANIM      = 0xFF86C1  (FK fases: 0x0C startup, 0x02 asc, 0x00 cima, 0x04 desc)
--   P2_Y_VEL     = 0xFF86FC-FD (signed 16-bit; abs>256=airborne)
--   P1_STUN      = 0xFF895A | P2_STUN = 0xFF865A
--   P2_STUN_SPRITE = 0xFF8951 (0x24=pajaritos)
--   PROJ_SLOT    = 0xFF8E30  (0xA4=boom slot activo)
--   PROJ_IMPACT  = 0xFF8E00  (0x98=impacto inminente ~0.5s)
--   TIMER        = 0xFF8ACE
--   P1_SIDE      = 0xFF83D0 | P2_SIDE = 0xFF86D0
--   P1_CHAR      = 0xFF864F | P2_CHAR = 0xFF894F
-- =============================================================================

print("[LuaBridge v2.0] Iniciando...")

-- ── RUTAS DE ARCHIVOS ─────────────────────────────────────────────────────────
local BASE_DIR   = "C:\\proyectos\\MAME\\"
local INPUT_FILE = BASE_DIR .. "mame_input.txt"
local STATE_FILE = BASE_DIR .. "state.txt"

-- ── DIRECCIONES RAM ───────────────────────────────────────────────────────────
local ADDR = {
    -- Vida
    P1_HP          = 0xFF83E9,
    P2_HP          = 0xFF86E9,

    -- Lado (dirección hacia la que mira)
    P1_SIDE        = 0xFF83D0,
    P2_SIDE        = 0xFF86D0,

    -- Personajes
    P1_CHAR        = 0xFF864F,
    P2_CHAR        = 0xFF894F,

    -- Posición X (16-bit big-endian)
    P1_X_H         = 0xFF917C,
    P1_X_L         = 0xFF917D,
    P2_X_H         = 0xFF927C,
    P2_X_L         = 0xFF927D,

    -- Stun
    P1_STUN        = 0xFF895A,
    P2_STUN        = 0xFF865A,
    P2_STUN_SPRITE = 0xFF8951,  -- 0x24 = pajaritos activos

    -- Estado P2 (Guile)
    P2_CROUCH      = 0xFF86C4,  -- 0x03=agachado, 0x02=de pie
    P2_ANIM        = 0xFF86C1,  -- frame de animación (FK fases)
    P2_Y_VEL_H     = 0xFF86FC,  -- velocidad vertical signed 16-bit
    P2_Y_VEL_L     = 0xFF86FD,

    -- Proyectil Sonic Boom
    PROJ_SLOT      = 0xFF8E30,  -- 0xA4 = slot activo
    PROJ_IMPACT    = 0xFF8E00,  -- 0x98 = impacto inminente

    -- Cronómetro
    TIMER          = 0xFF8ACE,
}

-- ── ESTADO INTERNO ────────────────────────────────────────────────────────────
local frame_count        = 0
local last_input         = {0,0,0,0,0,0,0,0,0,0,0,0}
local boom_active        = false
local boom_throw_frame   = -1
local prev_p2_anim       = 0
local prev_p2_airborne   = false
local BOOM_VEL_APPROX    = 25   -- unidades coord/frame

-- ── HELPERS ───────────────────────────────────────────────────────────────────

local function read_u8(addr)
    return mainmemory.read_u8(addr)
end

local function read_u16_be(addr_h, addr_l)
    local h = mainmemory.read_u8(addr_h)
    local l = mainmemory.read_u8(addr_l)
    return (h * 256) + l
end

local function read_s16_be(addr_h, addr_l)
    local raw = read_u16_be(addr_h, addr_l)
    if raw >= 0x8000 then return raw - 0x10000 end
    return raw
end

local function escape_string(s)
    s = s:gsub('\\', '\\\\')
    s = s:gsub('"',  '\\"')
    s = s:gsub('\n', '\\n')
    s = s:gsub('\r', '\\r')
    return s
end

-- ── LEER INPUTS PYTHON ────────────────────────────────────────────────────────

local function read_input()
    local f = io.open(INPUT_FILE, "r")
    if not f then return nil end
    local line = f:read("*l")
    f:close()
    if not line or line == "" then return nil end

    local buttons = {}
    for val in line:gmatch("([^,]+)") do
        table.insert(buttons, tonumber(val) or 0)
    end
    if #buttons < 12 then return nil end
    return buttons
end

-- ── APLICAR INPUTS A MAME ─────────────────────────────────────────────────────

local function apply_input(buttons)
    -- Mapeo: [UP, DOWN, LEFT, RIGHT, JAB, STRONG, FIERCE, SHORT, FORWARD, RH, ?, ?]
    -- P1 joystick
    manager.machine.input:set_value("P1 Up",         buttons[1]  or 0)
    manager.machine.input:set_value("P1 Down",       buttons[2]  or 0)
    manager.machine.input:set_value("P1 Left",       buttons[3]  or 0)
    manager.machine.input:set_value("P1 Right",      buttons[4]  or 0)
    -- Botones de ataque P1
    manager.machine.input:set_value("P1 Button 1",   buttons[5]  or 0)  -- JAB
    manager.machine.input:set_value("P1 Button 2",   buttons[6]  or 0)  -- STRONG
    manager.machine.input:set_value("P1 Button 3",   buttons[7]  or 0)  -- FIERCE
    manager.machine.input:set_value("P1 Button 4",   buttons[8]  or 0)  -- SHORT
    manager.machine.input:set_value("P1 Button 5",   buttons[9]  or 0)  -- FORWARD
    manager.machine.input:set_value("P1 Button 6",   buttons[10] or 0)  -- ROUNDHOUSE
end

-- ── LEER ESTADO COMPLETO ──────────────────────────────────────────────────────

local function read_game_state()
    -- Vida
    local p1_hp = read_u8(ADDR.P1_HP)
    local p2_hp = read_u8(ADDR.P2_HP)

    -- Posición X
    local p1_x = read_u16_be(ADDR.P1_X_H, ADDR.P1_X_L)
    local p2_x = read_u16_be(ADDR.P2_X_H, ADDR.P2_X_L)

    -- Lado (dirección hacia la que mira P1): 1=derecha, 0=izquierda
    local p1_side_raw = read_u8(ADDR.P1_SIDE)
    local p1_dir = (p1_side_raw == 0) and 1 or 0   -- 0 en RAM = mirando derecha en SF2CE

    -- Personajes
    local p1_char = read_u8(ADDR.P1_CHAR)
    local p2_char = read_u8(ADDR.P2_CHAR)

    -- Stun
    local p1_stun = read_u8(ADDR.P1_STUN)
    local p2_stun = read_u8(ADDR.P2_STUN)
    local p2_stun_sprite = read_u8(ADDR.P2_STUN_SPRITE)

    -- Estado P2 (Guile)
    local p2_crouch    = (read_u8(ADDR.P2_CROUCH) == 0x03)
    local p2_anim      = read_u8(ADDR.P2_ANIM)
    local p2_y_vel     = read_s16_be(ADDR.P2_Y_VEL_H, ADDR.P2_Y_VEL_L)
    local p2_airborne  = (math.abs(p2_y_vel) > 256)

    -- P1 airborne (heurística: usar Y_VEL de P1 si disponible, sino acción)
    -- Por ahora usamos una heurística simple desde acción de animación de P1
    -- (si tienes la dirección de P1_Y_VEL, añadir aquí)
    local p1_airborne  = false   -- TODO: añadir P1_Y_VEL cuando se confirme dirección

    -- Cronómetro
    local timer = read_u8(ADDR.TIMER)

    -- Proyectil Sonic Boom
    local proj_slot_val   = read_u8(ADDR.PROJ_SLOT)
    local proj_impact_val = read_u8(ADDR.PROJ_IMPACT)
    local boom_slot_active  = (proj_slot_val == 0xA4)
    local boom_incoming     = (proj_impact_val == 0x98)

    -- Detectar lanzamiento de boom este frame:
    -- ANIM=0x0C + en tierra + frame nuevo de lanzamiento
    local boom_throw_this_frame = false
    local on_ground = not p2_airborne
    if p2_anim == 0x0C and on_ground and prev_p2_anim ~= 0x0C then
        -- Puede ser boom throw O FK landing. Si venía del aire = FK landing; si no = boom
        if not prev_p2_airborne then
            boom_throw_this_frame = true
            boom_active   = true
            boom_throw_frame = frame_count
        end
    end

    -- Expirar boom si PROJ_SLOT ya no está activo y ha pasado suficiente tiempo
    if boom_active and not boom_slot_active then
        local frames_elapsed = frame_count - boom_throw_frame
        if frames_elapsed > 180 then   -- ~3s a 60fps → boom debe haber impactado
            boom_active = false
        end
    end

    -- Estimar X del boom (workaround: P2_X - frames_desde_throw × 25)
    local boom_x_est = 0.0
    if boom_active and boom_throw_frame >= 0 then
        local frames_elapsed = frame_count - boom_throw_frame
        boom_x_est = p2_x - frames_elapsed * BOOM_VEL_APPROX
        if boom_x_est < 0 then
            boom_x_est = 0.0
            boom_active = false
        end
    end

    -- FK landing detection (usado en Python, pero también lo exponemos)
    local fk_landing_this_frame = false
    if prev_p2_airborne and not p2_airborne then
        fk_landing_this_frame = true
        boom_active = false   -- si Guile acaba de aterrizar, el boom ya pasó o no era boom
    end

    -- Actualizar estado previo
    prev_p2_anim      = p2_anim
    prev_p2_airborne  = p2_airborne

    return {
        -- Vida y básicos
        p1_hp        = p1_hp,
        p2_hp        = p2_hp,
        p1_x         = p1_x,
        p2_x         = p2_x,
        p1_dir       = p1_dir,
        p1_char      = p1_char,
        p2_char      = p2_char,
        timer        = timer,

        -- Stun
        p1_stun      = p1_stun,
        p2_stun      = p2_stun,
        p2_stunned   = (p2_stun_sprite == 0x24),

        -- Estado P2
        p2_crouch    = p2_crouch,
        p2_anim      = p2_anim,
        p2_y_vel     = p2_y_vel,
        p2_airborne  = p2_airborne,
        p1_airborne  = p1_airborne,

        -- Boom
        boom_active         = boom_active,
        boom_x_est          = boom_x_est,
        boom_incoming       = boom_incoming,
        boom_slot_active    = boom_slot_active,
        boom_throw_this_frame = boom_throw_this_frame,
        fk_landing_this_frame = fk_landing_this_frame,

        -- Frame
        frame        = frame_count,
    }
end

-- ── SERIALIZAR A JSON (sin librerías externas) ────────────────────────────────

local function bool_to_str(b)
    return b and "true" or "false"
end

local function state_to_json(s)
    return string.format(
        '{"p1_hp":%d,"p2_hp":%d,"p1_x":%d,"p2_x":%d,"p1_dir":%d,' ..
        '"p1_char":%d,"p2_char":%d,"timer":%d,' ..
        '"p1_stun":%d,"p2_stun":%d,"p2_stunned":%s,' ..
        '"p2_crouch":%s,"p2_anim":%d,"p2_y_vel":%d,' ..
        '"p2_airborne":%s,"p1_airborne":%s,' ..
        '"boom_active":%s,"boom_x_est":%.1f,"boom_incoming":%s,' ..
        '"boom_slot_active":%s,"boom_throw_this_frame":%s,' ..
        '"fk_landing_this_frame":%s,"p2_hitstop":0,' ..
        '"frame":%d}',
        s.p1_hp, s.p2_hp, s.p1_x, s.p2_x, s.p1_dir,
        s.p1_char, s.p2_char, s.timer,
        s.p1_stun, s.p2_stun, bool_to_str(s.p2_stunned),
        bool_to_str(s.p2_crouch), s.p2_anim, s.p2_y_vel,
        bool_to_str(s.p2_airborne), bool_to_str(s.p1_airborne),
        bool_to_str(s.boom_active), s.boom_x_est, bool_to_str(s.boom_incoming),
        bool_to_str(s.boom_slot_active), bool_to_str(s.boom_throw_this_frame),
        bool_to_str(s.fk_landing_this_frame),
        s.frame
    )
end

-- ── BUCLE PRINCIPAL ───────────────────────────────────────────────────────────

local function on_frame()
    frame_count = frame_count + 1

    -- 1. Leer input de Python
    local buttons = read_input()
    if buttons then
        last_input = buttons
        apply_input(buttons)
    else
        apply_input(last_input)   -- mantener último input si no hay nuevo
    end

    -- 2. Leer estado del juego
    local state = read_game_state()

    -- 3. Escribir state.txt para Python
    local json_str = state_to_json(state)
    local f = io.open(STATE_FILE, "w")
    if f then
        f:write(json_str .. "\n")
        f:close()
    end

    -- 4. Log periódico en consola MAME (cada 300 frames ≈ 5s)
    if frame_count % 300 == 0 then
        local fk_phase_str = "tierra"
        local p2_air = state.p2_airborne
        local anim   = state.p2_anim
        if p2_air then
            if     anim == 0x02 then fk_phase_str = "FK_ASCENSO"
            elseif anim == 0x00 then fk_phase_str = "FK_CIMA"
            elseif anim == 0x04 then fk_phase_str = "FK_DESCENSO"
            elseif anim == 0x0C then fk_phase_str = "FK_STARTUP"
            end
        elseif anim == 0x0C then
            fk_phase_str = "BOOM/FK_LANDING"
        end
        print(string.format(
            "[F%d] P1:%d P2:%d | X: P1=%d P2=%d | P2_ANIM=0x%02X(%s) Y_VEL=%d | BOOM:%s IMPACT:%s",
            frame_count,
            state.p1_hp, state.p2_hp,
            state.p1_x,  state.p2_x,
            state.p2_anim, fk_phase_str, state.p2_y_vel,
            tostring(state.boom_active), tostring(state.boom_incoming)
        ))
    end
end

-- Registrar el callback para ejecutarse al final de cada frame
emu.register_frame_done(on_frame, "frame")

print("[LuaBridge v2.0] Bridge activo.")
print("  → Leyendo inputs de: " .. INPUT_FILE)
print("  → Escribiendo estado en: " .. STATE_FILE)
print("  → Log periódico cada 300 frames en consola MAME.")
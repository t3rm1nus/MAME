-- =============================================================================
-- autoplay_bridge.lua  |  v2.7  |  SF2CE / MAME 0.286
-- =============================================================================
-- DISEÑO: FSM event-driven basada en RAM. SIN frame-counts como lógica
-- principal. Los timeouts solo actúan como safety-net ante cuelgues.
--
--
-- FLUJO INICIAL (una sola vez al arrancar):
--   BOOTING
--     -> INSERT_COIN        (al detectar attract/title: p1_hp=0 o p2_hp=0)
--     -> PRESS_START        (coin insertada, esperar start screen)
--     -> CHAR_NAVIGATE      (cursor en Ryu, mover 2xRIGHT a Blanka)
--     -> CHAR_CONFIRM       (JAB para confirmar)
--     -> IN_COMBAT          (al detectar ambos HP >= MIN_HP estables)
--
-- BUCLE COMBATE:
--   IN_COMBAT
--     -> ROUND_OVER         (al detectar HP=0 en cualquier jugador)
--         Si HP resetea (nueva ronda o nuevo rival) -> IN_COMBAT
--         Si HP no resetea (game over) -> GAME_OVER
--     -> GAME_OVER
--         Insertar coin (event-driven: solo 1 coin)
--         JAB para confirmar Blanka (preseleccionado por SF2CE)
--         -> IN_COMBAT
-- =============================================================================

local BRIDGE_VERSION = "autoplay_bridge_v2.7"
local BASE_DIR = "C:\\proyectos\\MAME\\"
local DYN_DIR  = BASE_DIR .. "dinamicos\\"

-- ─── INSTANCE ID ─────────────────────────────────────────────────────────────
local CLAIM_FILE  = DYN_DIR .. "instance_id_claim.txt"
local INSTANCE_ID = nil
local _id_resolved = false

local function try_claim_instance_id()
    local f = io.open(CLAIM_FILE, "r")
    if not f then return false end
    local line = f:read("*l"); f:close()
    if not line or line == "" then return false end
    local id = tonumber(line); if not id then return false end
    os.rename(CLAIM_FILE, DYN_DIR .. "instance_id_claimed_" .. id .. ".txt")
    INSTANCE_ID = id
    return true
end

-- ─── PATHS ───────────────────────────────────────────────────────────────────
local P = {}
local _ver_written = false

local function refresh_paths()
    local sid = tostring(INSTANCE_ID or 0)
    P = {
        input   = DYN_DIR .. "mame_input_"     .. sid .. ".txt",
        state   = DYN_DIR .. "state_"          .. sid .. ".txt",
        state_t = DYN_DIR .. "state_"          .. sid .. ".tmp",
        ver     = DYN_DIR .. "bridge_version_" .. sid .. ".txt",
    }
end
refresh_paths()  -- paths iniciales con ID=0 (temporal)

local function write_ver_file()
    if _ver_written or not _id_resolved then return end
    local f = io.open(P.ver, "w")
    if f then f:write(BRIDGE_VERSION .. "\n"); f:close(); _ver_written = true end
end

local _id_attempts = 0
local function ensure_instance_id()
    if _id_resolved then return end
    _id_attempts = _id_attempts + 1
    if try_claim_instance_id() then
        _id_resolved = true
        refresh_paths()
        write_ver_file()
        print(string.format("[AB v2.7] Instance ID=%d resuelto en intento %d",
            INSTANCE_ID, _id_attempts))
        return
    end
    -- Fallback tras ~10s (600 frames a 60fps): asumir ID=0
    if _id_attempts >= 600 then
        INSTANCE_ID   = 0
        _id_resolved  = true
        refresh_paths()
        write_ver_file()
        print("[AB v2.7] WARN: claim no encontrado tras 600 intentos, usando ID=0")
    end
end

-- ─── DIRECCIONES RAM ─────────────────────────────────────────────────────────
local ADDR = {
    GAME_STATE   = 0xFF8005,
    P1_HP        = 0xFF83E9,
    P2_HP        = 0xFF86E9,
    P1_SIDE      = 0xFF83D0,
    -- P1_CHAR: dirección directa en bloque de entidad P1 (siempre = 2 = Blanka)
    P1_CHAR      = 0xFF864F,
    -- P2_CHAR: NO se usa directamente aquí. Se lee via ADDR_P2_CHAR = 0xFF894F
    -- en read_p2_char() definida más abajo.
    P1_X_H       = 0xFF917C, P1_X_L = 0xFF917D,
    P2_X_H       = 0xFF927C, P2_X_L = 0xFF927D,
    P1_STUN      = 0xFF895A, P2_STUN = 0xFF865A,
    P2_STUN_SPR  = 0xFF8951,
    P2_CROUCH    = 0xFF86C4,
    P2_ANIM      = 0xFF86C1,
    P2_Y_VEL_H   = 0xFF86FC, P2_Y_VEL_L = 0xFF86FD,
    P1_ANIM      = 0xFF83C1,
    P1_Y_VEL_H   = 0xFF83FC, P1_Y_VEL_L = 0xFF83FD,
    PROJ_SLOT    = 0xFF8E30,
    PROJ_IMPACT  = 0xFF8E00,
}

-- ─── HARDWARE INIT ───────────────────────────────────────────────────────────
local _mem       = nil
local _mem_ok    = false
local _fields    = {}
local _fields_ok = false

local BTN_JAB        = "P1 Jab Punch"
local BTN_STRONG     = "P1 Strong Punch"
local BTN_FIERCE     = "P1 Fierce Punch"
local BTN_SHORT      = "P1 Short Kick"
local BTN_FORWARD    = "P1 Forward Kick"
local BTN_ROUNDHOUSE = "P1 Roundhouse Kick"
local BTN_COIN       = "Coin 1"
local BTN_START      = "1 Player Start"

local ALL_BTNS = {
    "P1 Up","P1 Down","P1 Left","P1 Right",
    BTN_JAB, BTN_STRONG, BTN_FIERCE,
    BTN_SHORT, BTN_FORWARD, BTN_ROUNDHOUSE,
    BTN_COIN, BTN_START,
}

local function try_init()
    if _mem_ok and _fields_ok then return true end
    if not _mem_ok then
        local ok, sp = pcall(function()
            return manager.machine.devices[":maincpu"].spaces["program"]
        end)
        if ok and sp then _mem = sp; _mem_ok = true end
    end
    if not _fields_ok and _mem_ok then
        local ok, ports = pcall(function() return manager.machine.ioport.ports end)
        if ok and ports then
            local all = {}
            for _, port in pairs(ports) do
                for fname, field in pairs(port.fields) do all[fname] = field end
            end
            local found = 0
            for _, name in ipairs(ALL_BTNS) do
                if all[name] then _fields[name] = all[name]; found = found + 1 end
            end
            _fields_ok = (found >= 10)
        end
    end
    return _mem_ok and _fields_ok
end

-- ─── INPUT SYSTEM ────────────────────────────────────────────────────────────
local _held = {}
local function hold(n)    _held[n] = true  end
local function release(n) _held[n] = nil   end
local function clear_all() _held = {}      end

local function flush_inputs()
    if not _fields_ok then return end
    for name, field in pairs(_fields) do
        pcall(function() field:set_value(_held[name] and 1 or 0) end)
    end
end

-- ─── RAM HELPERS ─────────────────────────────────────────────────────────────
-- IMPORTANTE: Definir ANTES de read_p2_char() y read_game_state()
local function ru8(a)
    if not _mem then return 0 end
    return _mem:read_u8(a)
end
local function ru16(ah, al)
    if not _mem then return 0 end
    return (_mem:read_u8(ah) * 256) + _mem:read_u8(al)
end
local function rs16(ah, al)
    local r = ru16(ah, al)
    return r >= 0x8000 and r - 0x10000 or r
end
local function bts(b) return b and "true" or "false" end


local ADDR_P2_CHAR = 0xFF8660


-- Último char_id de P2 conocido (para mantener valor entre transiciones)
local _last_known_p2ch = 3  -- Guile como default inicial

local function read_p2_char()
    local v = ru8(ADDR_P2_CHAR)
    -- Durante transiciones puede leer 0xFF o valores inválidos (>11)
    -- En ese caso mantener el último valor conocido
    if v <= 11 then
        _last_known_p2ch = v
        return v
    end
    return _last_known_p2ch
end

local MAX_COMBAT_FRAMES = 6400  -- 99s × 60fps + margen
local MIN_HP            = 100
local HP_ALIVE          = 0

-- Estado del enfrentamiento (Mejor de 3)
local match_p1_wins = 0
local match_p2_wins = 0
local match_over = false
local round_result = "none"

-- ─── ESTADO INTERNO ──────────────────────────────────────────────────────────
local frame_count = 0

-- FSM
local sm_state   = "BOOTING"
local sm_frame   = 0

-- Lectura anterior (para detectar flancos)
local prev_p1_hp       = 144
local prev_p2_hp       = 144
local prev_p2_airborne = false
local prev_p1_airborne = false
local prev_p2_anim     = 0

-- Boom tracking
local boom_active      = false
local boom_throw_frame = -1
local BOOM_VEL         = 25

-- Diagnóstico combate
local diag_combat_frame  = 0
local diag_noop_count    = 0
local diag_action_count  = 0
local diag_write_ok      = 0
local diag_write_fail    = 0

-- Input Python
local last_input = {0,0,0,0,0,0,0,0,0,0,0,0}

-- ─── TRANSICIÓN FSM ──────────────────────────────────────────────────────────
local function transition(new_state)
    print(string.format("[AB v2.7 ID%d F%d] %s -> %s",
        INSTANCE_ID or 0, frame_count, sm_state, new_state))
    sm_state  = new_state
    sm_frame  = 0
    clear_all()
    if new_state == "IN_COMBAT" then
        diag_combat_frame = 0
        diag_noop_count   = 0
        diag_action_count = 0
    end
end

-- ─── LECTURA ESTADO JUEGO ────────────────────────────────────────────────────
local function read_game_state()
    -- Fallback si la memoria no está lista
    if not _mem_ok then
        return {
            p1_hp=144, p2_hp=144, p1_x=700, p2_x=700, p1_dir=1,
            p1_char=2, p2_char=_last_known_p2ch, timer=99,
            p1_stun=0, p2_stun=0, p2_stunned=false,
            p2_crouch=false, p2_anim=0, p2_y_vel=0, p2_airborne=false,
            p1_anim=0, p1_y_vel=0, p1_airborne=false,
            p1_landing_this_frame=false,
            boom_active=false, boom_x_est=0.0,
            boom_incoming=false, boom_slot_active=false,
            boom_throw_this_frame=false,
            fk_landing_this_frame=false,
            frame=frame_count,
            game_state=0
        }
    end

    -- Lecturas básicas
    local p1hp   = ru8(ADDR.P1_HP)
    local p2hp   = ru8(ADDR.P2_HP)
    local p1x    = ru16(ADDR.P1_X_H, ADDR.P1_X_L)
    local p2x    = ru16(ADDR.P2_X_H, ADDR.P2_X_L)
    local p1dir  = (ru8(ADDR.P1_SIDE) == 0) and 1 or 0
    local p1ch   = ru8(ADDR.P1_CHAR)

    local p2ch   = read_p2_char()

    local p1st   = ru8(ADDR.P1_STUN)
    local p2st   = ru8(ADDR.P2_STUN)
    local p2ss   = ru8(ADDR.P2_STUN_SPR)
    local p2cr   = (ru8(ADDR.P2_CROUCH) == 0x03)
    local p2an   = ru8(ADDR.P2_ANIM)
    local p2yv   = rs16(ADDR.P2_Y_VEL_H, ADDR.P2_Y_VEL_L)
    local p2air  = (math.abs(p2yv) > 256)

    local p1an   = ru8(ADDR.P1_ANIM)
    local p1yv   = rs16(ADDR.P1_Y_VEL_H, ADDR.P1_Y_VEL_L)
    local p1air  = (math.abs(p1yv) > 256)
    local p1land = (prev_p1_airborne and not p1air)
    local g_state = ru8(ADDR.GAME_STATE)

    local timer_est = 99
    local bxe = 0.0
    local bi = false
    local bsa = false
    local boom_thr = false
    local fkl = false

    return {
        p1_hp=p1hp, p2_hp=p2hp, p1_x=p1x, p2_x=p2x, p1_dir=p1dir,
        p1_char=p1ch, p2_char=p2ch,
        timer=timer_est, p1_stun=p1st, p2_stun=p2st,
        p2_stunned=(p2ss==0x24), p2_crouch=p2cr, p2_anim=p2an,
        p2_y_vel=p2yv, p2_airborne=p2air,
        p1_anim=p1an, p1_y_vel=p1yv, p1_airborne=p1air,
        p1_landing_this_frame=p1land,
        boom_active=boom_active, boom_x_est=bxe,
        boom_incoming=bi, boom_slot_active=bsa,
        boom_throw_this_frame=boom_thr,
        fk_landing_this_frame=fkl,
        frame=frame_count,
        game_state=g_state
    }
end

-- ─── SERIALIZACIÓN JSON ──────────────────────────────────────────────────────
local function fmtf(v)
    local i = math.floor(v)
    return string.format("%d.%d", i, math.floor((v-i)*10+0.5))
end

local function to_json(s)
    return string.format(
        '{"p1_hp":%d,"p2_hp":%d,"p1_x":%d,"p2_x":%d,"p1_dir":%d,'..
        '"p1_char":%d,"p2_char":%d,"timer":%d,'..
        '"p1_stun":%d,"p2_stun":%d,"p2_stunned":%s,'..
        '"p2_crouch":%s,"p2_anim":%d,"p2_y_vel":%d,"p2_airborne":%s,'..
        '"p1_anim":%d,"p1_y_vel":%d,"p1_airborne":%s,"p1_landing_this_frame":%s,'..
        '"boom_active":%s,"boom_x_est":%s,"boom_incoming":%s,'..
        '"boom_slot_active":%s,"boom_throw_this_frame":%s,'..
        '"fk_landing_this_frame":%s,"p2_hitstop":0,'..
        '"match_p1_wins":%d,"match_p2_wins":%d,"match_over":%s,"round_result":"%s",'..
        '"in_combat":%s,"frame":%d}',
        s.p1_hp,s.p2_hp,s.p1_x,s.p2_x,s.p1_dir,
        s.p1_char,s.p2_char,s.timer,
        s.p1_stun,s.p2_stun,bts(s.p2_stunned),
        bts(s.p2_crouch),s.p2_anim,s.p2_y_vel,bts(s.p2_airborne),
        s.p1_anim,s.p1_y_vel,bts(s.p1_airborne),bts(s.p1_landing_this_frame),
        bts(s.boom_active),fmtf(s.boom_x_est),bts(s.boom_incoming),
        bts(s.boom_slot_active),bts(s.boom_throw_this_frame),
        bts(s.fk_landing_this_frame),
        match_p1_wins, match_p2_wins, bts(match_over), round_result,
        bts(sm_state=="IN_COMBAT"),
        s.frame)
end

local function write_state(s)
    -- v2.6 FIX: os.rename falla en Windows si el destino ya existe.
    -- Escribimos directamente a state_N.txt sin pasar por .tmp
    local json_str = to_json(s) .. "\n"
    local f = io.open(P.state, "w")
    if f then
        f:write(json_str); f:flush(); f:close()
        diag_write_ok = diag_write_ok + 1
    else
        diag_write_fail = diag_write_fail + 1
        -- Log del primer fallo y luego cada 300 frames
        if diag_write_fail == 1 or diag_write_fail % 300 == 0 then
            print(string.format("[AB v2.7 ID%d] ERROR write_state FALLO #%d path=%s",
                INSTANCE_ID or 0, diag_write_fail, tostring(P.state)))
        end
    end
end

-- ─── INPUT PYTHON ────────────────────────────────────────────────────────────
local function read_python_input()
    local f = io.open(P.input, "r"); if not f then return nil end
    local l = f:read("*l"); f:close()
    if not l or l == "" then return nil end
    local b = {}
    for v in l:gmatch("([^,]+)") do b[#b+1] = tonumber(v) or 0 end
    return #b >= 10 and b or nil
end

-- =============================================================================
-- FSM — MÁQUINA DE ESTADOS
-- =============================================================================

local hp_stable_count = 0
local HP_STABLE_NEED  = 8

local char_nav_step   = 0
local char_nav_frame  = 0
local is_continue     = false

local coin_inserted   = false
local start_pressed   = false

local ko_frames       = 0
local KO_GAME_OVER_N  = 360

local blanka_ko       = false

-- ─── TICK FSM ────────────────────────────────────────────────────────────────
local function tick_fsm(st)
    sm_frame = sm_frame + 1

    local p1_alive   = st.p1_hp > HP_ALIVE
    local p2_alive   = st.p2_hp > HP_ALIVE
    local both_alive = p1_alive and p2_alive

    -- =========================================================================
    if sm_state == "BOOTING" then
    -- =========================================================================
        if _mem_ok and _fields_ok then
            transition("INSERT_COIN")
        elseif sm_frame >= 600 then
            sm_frame = 0
        end

    -- =========================================================================
    elseif sm_state == "INSERT_COIN" then
    -- =========================================================================
        if sm_frame == 1 then
            hold(BTN_COIN)
            coin_inserted = true
        elseif sm_frame == 5 then
            release(BTN_COIN)
        end

        if sm_frame >= 60 then
            transition("PRESS_START")
        end

    -- =========================================================================
    elseif sm_state == "PRESS_START" then
    -- =========================================================================
        if sm_frame % 20 == 1 then
            hold(BTN_START)
        elseif sm_frame % 20 == 6 then
            release(BTN_START)
        end

        if sm_frame >= 120 then
            is_continue   = false
            char_nav_step = 0
            char_nav_frame = 0
            transition("CHAR_NAVIGATE")
        end

    -- =========================================================================
    elseif sm_state == "CHAR_NAVIGATE" then
    -- =========================================================================
        if is_continue then
            transition("CHAR_CONFIRM")
            return
        end

        char_nav_frame = char_nav_frame + 1

        if char_nav_step == 0 then
            if char_nav_frame >= 20 then
                char_nav_step  = 1
                char_nav_frame = 0
            end

        elseif char_nav_step == 1 then
            if char_nav_frame <= 8 then
                hold("P1 Right")
            else
                release("P1 Right")
                if char_nav_frame >= 20 then
                    char_nav_step  = 2
                    char_nav_frame = 0
                end
            end

        elseif char_nav_step == 2 then
            if char_nav_frame <= 8 then
                hold("P1 Right")
            else
                release("P1 Right")
                if char_nav_frame >= 20 then
                    char_nav_step  = 3
                    char_nav_frame = 0
                end
            end

        elseif char_nav_step == 3 then
            transition("CHAR_CONFIRM")
        end

        if sm_frame >= 500 then
            print("[AB v2.7] TIMEOUT CHAR_NAVIGATE -> reintento INSERT_COIN")
            char_nav_step = 0; char_nav_frame = 0
            transition("INSERT_COIN")
        end

    -- =========================================================================
    elseif sm_state == "CHAR_CONFIRM" then
    -- =========================================================================
        if sm_frame == 10 then
            hold(BTN_JAB)
        elseif sm_frame == 20 then
            release(BTN_JAB)
        end

        if sm_frame >= 40 then
            hp_stable_count = 0
            transition("WAIT_COMBAT")
        end

    -- =========================================================================
    elseif sm_state == "WAIT_COMBAT" then
    -- =========================================================================
        if both_alive then
            hp_stable_count = hp_stable_count + 1
            if hp_stable_count >= HP_STABLE_NEED then
                hp_stable_count = 0
                transition("IN_COMBAT")
            end
        else
            hp_stable_count = 0
        end

        if sm_frame >= 1200 then
            print(string.format("[AB v2.7] TIMEOUT WAIT_COMBAT (is_continue=%s)",
                bts(is_continue)))
            hp_stable_count = 0
            if is_continue then
                transition("CHAR_CONFIRM")
            else
                transition("INSERT_COIN")
            end
        end

    -- =========================================================================
    elseif sm_state == "IN_COMBAT" then
    -- =========================================================================
        diag_combat_frame = diag_combat_frame + 1

        local buttons = read_python_input()
        if buttons then
            last_input = buttons
            local any = false
            for i = 1,10 do if (buttons[i] or 0) ~= 0 then any = true end end
            if any then diag_action_count = diag_action_count + 1
            else diag_noop_count = diag_noop_count + 1 end
        end

        clear_all()
        local b = last_input
        if (b[1]  or 0)~=0 then hold("P1 Up")       end
        if (b[2]  or 0)~=0 then hold("P1 Down")      end
        if (b[3]  or 0)~=0 then hold("P1 Left")      end
        if (b[4]  or 0)~=0 then hold("P1 Right")     end
        if (b[5]  or 0)~=0 then hold(BTN_JAB)        end
        if (b[6]  or 0)~=0 then hold(BTN_STRONG)     end
        if (b[7]  or 0)~=0 then hold(BTN_FIERCE)     end
        if (b[8]  or 0)~=0 then hold(BTN_SHORT)      end
        if (b[9]  or 0)~=0 then hold(BTN_FORWARD)    end
        if (b[10] or 0)~=0 then hold(BTN_ROUNDHOUSE) end

        local p1_dead = (st.p1_hp <= HP_ALIVE)
        local p2_dead = (st.p2_hp <= HP_ALIVE)
        local timeout = (diag_combat_frame >= MAX_COMBAT_FRAMES)

        if p1_dead or p2_dead or timeout then
            clear_all()
            ko_frames = 0

            if timeout then
                if st.p1_hp > st.p2_hp then
                    round_result = "win"
                    match_p1_wins = match_p1_wins + 1
                    blanka_ko = false
                elseif st.p2_hp > st.p1_hp then
                    round_result = "loss"
                    match_p2_wins = match_p2_wins + 1
                    blanka_ko = true
                else
                    round_result = "draw"
                    match_p1_wins = match_p1_wins + 1
                    match_p2_wins = match_p2_wins + 1
                    blanka_ko = false
                end
            else
                if p1_dead and p2_dead then
                    round_result = "draw"
                    match_p1_wins = match_p1_wins + 1
                    match_p2_wins = match_p2_wins + 1
                    blanka_ko = true
                elseif p2_dead then
                    round_result = "win"
                    match_p1_wins = match_p1_wins + 1
                    blanka_ko = false
                elseif p1_dead then
                    round_result = "loss"
                    match_p2_wins = match_p2_wins + 1
                    blanka_ko = true
                end
            end

            if match_p1_wins >= 2 or match_p2_wins >= 2 then
                match_over = true
            end

            print(string.format("[AB v2.7] FIN DE RONDA | P1=%d P2=%d | P2char=%d | Marcador: %d-%d | DoubleKO: %s",
                st.p1_hp, st.p2_hp, st.p2_char, match_p1_wins, match_p2_wins,
                bts(p1_dead and p2_dead)))

            transition("ROUND_OVER")
        end

    -- =========================================================================
    elseif sm_state == "ROUND_OVER" then
    -- =========================================================================
        local p1_rising = (prev_p1_hp <= HP_ALIVE and st.p1_hp >= MIN_HP)
        local p2_rising = (prev_p2_hp <= HP_ALIVE and st.p2_hp >= MIN_HP)
        local hp_flank  = p1_rising and p2_rising

        if hp_flank then
            hp_stable_count = hp_stable_count + 1
            if hp_stable_count >= 3 then
                hp_stable_count = 0
                ko_frames = 0

                if match_over then
                    match_p1_wins = 0
                    match_p2_wins = 0
                    match_over = false
                end
                round_result = "none"

                print("[AB v2.7] HP RESET (flanco) -> nueva ronda / nuevo rival")
                transition("IN_COMBAT")
            end
        elseif both_alive then
            hp_stable_count = hp_stable_count + 1
            if hp_stable_count >= HP_STABLE_NEED then
                hp_stable_count = 0
                ko_frames = 0

                if match_over then
                    match_p1_wins = 0
                    match_p2_wins = 0
                    match_over = false
                end
                round_result = "none"

                print("[AB v2.7] HP RESET (estable) -> nueva ronda / nuevo rival")
                transition("IN_COMBAT")
            end
        else
            hp_stable_count = 0
        end

        if blanka_ko then
            local any_hp_rising = (st.p1_hp > prev_p1_hp or st.p2_hp > prev_p2_hp)
            if any_hp_rising then
                ko_frames = 0
            else
                ko_frames = ko_frames + 1
                if ko_frames >= KO_GAME_OVER_N then
                    print("[AB v2.7] GAME OVER detectado (Blanka KO, HP sin recuperar en " ..
                        KO_GAME_OVER_N .. " frames)")
                    ko_frames = 0; hp_stable_count = 0
                    transition("GAME_OVER")
                end
            end
        else
            ko_frames = 0
        end

        if sm_frame >= 5000 then
            print("[AB v2.7] TIMEOUT ROUND_OVER (5000f)")
            ko_frames = 0; hp_stable_count = 0
            if blanka_ko then
                transition("GAME_OVER")
            else
                transition("WAIT_COMBAT")
            end
        end

    -- =========================================================================
    elseif sm_state == "GAME_OVER" then
    -- =========================================================================
        if sm_frame == 1 then
            hold(BTN_COIN)
        elseif sm_frame == 5 then
            release(BTN_COIN)
        end

        if sm_frame >= 10 and sm_frame % 15 == 0 then
            hold(BTN_START)
        elseif sm_frame >= 10 and sm_frame % 15 == 5 then
            release(BTN_START)
        end

        if sm_frame >= 120 then
            is_continue     = true
            hp_stable_count = 0

            match_p1_wins = 0
            match_p2_wins = 0
            match_over    = false
            round_result  = "none"

            transition("CHAR_CONFIRM")
        end
    end
end

-- =============================================================================
-- BUCLE PRINCIPAL
-- =============================================================================
local function on_frame()
    frame_count = frame_count + 1

    if not _id_resolved then
        ensure_instance_id()
    end
    if not _ver_written then write_ver_file() end

    try_init()

    local st = read_game_state()

    if _id_resolved then
        write_state(st)
        -- Log diagnóstico: primeros 3 writes y luego cada 600 frames
        if diag_write_ok <= 3 or diag_write_ok % 600 == 0 then
            print(string.format("[AB v2.7 ID%d] write_state OK #%d | path=%s | frame=%d",
                INSTANCE_ID or 0, diag_write_ok, tostring(P.state), frame_count))
        end
    elseif frame_count % 120 == 0 then
        -- Aún no resuelto: log cada 2s para ver si el claim no llega
        print(string.format("[AB v2.7] frame=%d _id_resolved=false _id_attempts=%d",
            frame_count, _id_attempts))
    end

    tick_fsm(st)

    prev_p1_hp       = st.p1_hp
    prev_p2_hp       = st.p2_hp
    prev_p1_airborne = st.p1_airborne
    prev_p2_airborne = st.p2_airborne
    prev_p2_anim     = st.p2_anim

    flush_inputs()

    if frame_count % 600 == 0 then
        print(string.format("[AB v2.7 ID%d] estado=%s smf=%d P1HP=%d P2HP=%d P2char=%d(%s) wr=%d cont=%s",
            INSTANCE_ID or 0, sm_state, sm_frame, st.p1_hp, st.p2_hp,
            st.p2_char,
            ({"Ryu","Honda","Blanka","Guile","Ken","Chun-Li","Zangief","Dhalsim",
              "Bison","Sagat","Balrog","Vega"})[st.p2_char+1] or "???",
            diag_write_ok, bts(is_continue)))
    end
end

emu.register_frame_done(on_frame, "frame")

print("[AB v2.7] Iniciado - P2_CHAR via 0xFF8660 directo (fix deteccion)")
print("[AB v2.7] ru8/ru16/rs16 definidas ANTES de read_p2_char (fix orden Lua)")
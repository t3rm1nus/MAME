-- =============================================================================
-- autoplay_bridge.lua  |  v2.22  |  SF2CE / MAME 0.286
-- =============================================================================
--
--   BUG 1 FIX: both_full exige HP en [140,144], excluye HP=255 (overflow CPS1)
--   BUG 2 FIX: ROUND_OVER detecta match_over falso por GS=0x04+HP combate → revert
--              GAME_OVER requiere _saw_gs08=true para ejecutar continue
--   BUG 3 FIX: GS=0x0C registrado como VS_SCREEN (cosmético)
--   BUG 4 FIX: IN_COMBAT intercepta GS=0x08 directamente → ROUND_OVER
--
--   BUG 5 FIX (v2.22) — Selección de Vega en lugar de Blanka
--   ──────────────────────────────────────────────────────────────────────────
--   CAUSA: v2.20/v2.21 introducían un reset (LEFT×3/LEFT×9+UP×N) antes de
--   navegar al personaje. SF2CE siempre posiciona el cursor en Ryu (col=0,
--   fila=0) al entrar al character select, por lo que el reset desplazaba
--   el cursor desde Ryu a posiciones erróneas por el wrap circular del grid.
--   RIGHT×2 desde esas posiciones aterrizaba en Vega u otro personaje.
--
--   FIX: CHAR_NAVIGATE hace ÚNICAMENTE RIGHT×2. Sin reset.
--   Ruta garantizada: Ryu(col=0) → RIGHT → Honda(col=1) → RIGHT → Blanka(col=2).
--
--   BUG 6 FIX (v2.22) — p1_char unreliable desde RAM
--   ──────────────────────────────────────────────────────────────────────────
--   CAUSA: La dirección 0xFF864F usada para P1_CHAR devuelve valores
--   inconsistentes. En SF2CE, P1 (Blanka) no se auto-registra en esa
--   dirección (documentado en constants.py).
--
--   FIX: p1_char hardcodeado a 2 (ID de Blanka en SF2CE). No se lee de RAM.
--
-- =============================================================================

local BRIDGE_VERSION = "autoplay_bridge_v2.22"
local BASE_DIR = "C:\\proyectos\\MAME\\"
local DYN_DIR  = BASE_DIR .. "dinamicos\\"

-- ─── GAME_STATE RAM MAP ───────────────────────────────────────────────────────
local GS_TITLE       = 0x00
local GS_COIN        = 0x02
local GS_PLAY        = 0x04
local GS_ROUNDOVER   = 0x06
local GS_GAMEOVER    = 0x08
local GS_CONT_EXPIRE = 0x0A
local GS_VS_SCREEN   = 0x0C

-- ─── INSTANCE ID ─────────────────────────────────────────────────────────────
local CLAIM_FILE   = DYN_DIR .. "instance_id_claim.txt"
local INSTANCE_ID  = nil
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
refresh_paths()

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
        print(string.format("[AB v2.22] Instance ID=%d resuelto en intento %d",
            INSTANCE_ID, _id_attempts))
        return
    end
    if _id_attempts >= 600 then
        INSTANCE_ID  = 0
        _id_resolved = true
        refresh_paths()
        write_ver_file()
        print("[AB v2.22] WARN: claim no encontrado tras 600 intentos, usando ID=0")
    end
end

-- ─── DIRECCIONES RAM ─────────────────────────────────────────────────────────
local ADDR = {
    GAME_STATE  = 0xFF8005,
    P1_HP       = 0xFF83E9,
    P2_HP       = 0xFF86E9,
    P1_SIDE     = 0xFF83D0,
    -- P1_CHAR: NO se lee de RAM — hardcodeado a 2 (Blanka). Ver BUG 6 FIX.
    P1_X_H      = 0xFF917C,  P1_X_L     = 0xFF917D,
    P2_X_H      = 0xFF927C,  P2_X_L     = 0xFF927D,
    P1_STUN     = 0xFF895A,  P2_STUN    = 0xFF865A,
    P2_STUN_SPR = 0xFF8951,
    P2_CROUCH   = 0xFF86C4,
    P2_ANIM     = 0xFF86C1,
    P2_Y_VEL_H  = 0xFF86FC,  P2_Y_VEL_L = 0xFF86FD,
    P1_ANIM     = 0xFF83C1,
    P1_Y_VEL_H  = 0xFF83FC,  P1_Y_VEL_L = 0xFF83FD,
    PROJ_SLOT   = 0xFF8E30,
    PROJ_IMPACT = 0xFF8E00,
    P1_WINS     = 0xFF8A3F,
    P2_WINS     = 0xFF8A41,
}

local P1_CHAR_ID   = 2       -- Blanka (hardcodeado, BUG 6 FIX)
local ADDR_P2_CHAR = 0xFF8660

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
    "P1 Up", "P1 Down", "P1 Left", "P1 Right",
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
local function hold(n)     _held[n] = true end
local function release(n)  _held[n] = nil  end
local function clear_all() _held = {}      end

local function flush_inputs()
    if not _fields_ok then return end
    for name, field in pairs(_fields) do
        pcall(function() field:set_value(_held[name] and 1 or 0) end)
    end
end

-- ─── RAM HELPERS ─────────────────────────────────────────────────────────────
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

local _last_known_p2ch = 0xFF
local function read_p2_char()
    local v = ru8(ADDR_P2_CHAR)
    if v <= 11 then _last_known_p2ch = v; return v end
    return _last_known_p2ch
end

local MAX_COMBAT_FRAMES  = 6400
local HP_ALIVE           = 0

local match_p1_wins = 0
local match_p2_wins = 0
local match_over    = false
local round_result  = "none"
local blanka_ko     = false

local _ram_offset_blanka = nil
local _ram_offset_rival  = nil

-- ─── ESTADO INTERNO ──────────────────────────────────────────────────────────
local frame_count = 0
local sm_state    = "BOOTING"
local sm_frame    = 0

local prev_p1_airborne = false
local prev_p2_airborne = false

local diag_combat_frame = 0
local diag_write_ok     = 0
local diag_write_fail   = 0

local last_input = {0,0,0,0,0,0,0,0,0,0,0,0}

local hp_stable_count = 0
local HP_STABLE_NEED  = 8

local char_nav_step   = 0
local char_nav_frame  = 0
local is_continue     = false

local ko_frames          = 0
local KO_WATCHDOG_FRAMES = 1500

local _saw_gs08 = false

-- ─── TRANSICIÓN FSM ──────────────────────────────────────────────────────────
local function transition(new_state)
    print(string.format("[AB v2.22 ID%d F%d] %s -> %s",
        INSTANCE_ID or 0, frame_count, sm_state, new_state))
    sm_state  = new_state
    sm_frame  = 0
    clear_all()
    if new_state == "IN_COMBAT" then
        diag_combat_frame = 0
    end
    if new_state == "GAME_OVER" then
        _saw_gs08 = false
    end
end

-- ─── LECTURA ESTADO JUEGO ────────────────────────────────────────────────────
local function read_game_state()
    if not _mem_ok then
        return {
            p1_hp=144, p2_hp=144, p1_x=700, p2_x=700, p1_dir=1,
            p1_char=P1_CHAR_ID, p2_char=_last_known_p2ch, timer=99,
            p1_stun=0, p2_stun=0, p2_stunned=false,
            p2_crouch=false, p2_anim=0, p2_y_vel=0, p2_airborne=false,
            p1_anim=0, p1_y_vel=0, p1_airborne=false,
            p1_landing_this_frame=false,
            boom_active=false, boom_x_est=0.0,
            boom_incoming=false, boom_slot_active=false,
            boom_throw_this_frame=false, fk_landing_this_frame=false,
            frame=frame_count, game_state=0
        }
    end

    local gs    = ru8(ADDR.GAME_STATE)
    local p1hp  = ru8(ADDR.P1_HP)
    local p2hp  = ru8(ADDR.P2_HP)
    local p1x   = ru16(ADDR.P1_X_H, ADDR.P1_X_L)
    local p2x   = ru16(ADDR.P2_X_H, ADDR.P2_X_L)
    local p1dir = (ru8(ADDR.P1_SIDE) == 0) and 1 or 0
    local p2ch  = read_p2_char()
    local p1st  = ru8(ADDR.P1_STUN)
    local p2st  = ru8(ADDR.P2_STUN)
    local p2ss  = ru8(ADDR.P2_STUN_SPR)
    local p2cr  = (ru8(ADDR.P2_CROUCH) == 0x03)
    local p2an  = ru8(ADDR.P2_ANIM)
    local p2yv  = rs16(ADDR.P2_Y_VEL_H, ADDR.P2_Y_VEL_L)
    local p2air = (math.abs(p2yv) > 256)
    local p1an  = ru8(ADDR.P1_ANIM)
    local p1yv  = rs16(ADDR.P1_Y_VEL_H, ADDR.P1_Y_VEL_L)
    local p1air = (math.abs(p1yv) > 256)
    local p1land = (prev_p1_airborne and not p1air)

    local t_raw     = ru8(0xFF8ACE)
    local timer_est = math.floor(t_raw / 16) * 10 + (t_raw % 16)
    if timer_est > 99 then timer_est = 99 end

    return {
        p1_hp=p1hp, p2_hp=p2hp, p1_x=p1x, p2_x=p2x, p1_dir=p1dir,
        p1_char=P1_CHAR_ID,   -- BUG 6 FIX: hardcodeado, no leer de RAM
        p2_char=p2ch,
        timer=timer_est, p1_stun=p1st, p2_stun=p2st,
        p2_stunned=(p2ss==0x24), p2_crouch=p2cr, p2_anim=p2an,
        p2_y_vel=p2yv, p2_airborne=p2air,
        p1_anim=p1an, p1_y_vel=p1yv, p1_airborne=p1air,
        p1_landing_this_frame=p1land,
        boom_active=false, boom_x_est=0.0,
        boom_incoming=false, boom_slot_active=false,
        boom_throw_this_frame=false, fk_landing_this_frame=false,
        frame=frame_count, game_state=gs
    }
end

-- ─── SERIALIZACIÓN JSON ──────────────────────────────────────────────────────
local function fmtf(v)
    local i = math.floor(v)
    return string.format("%d.%d", i, math.floor((v - i) * 10 + 0.5))
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
        s.p1_hp, s.p2_hp, s.p1_x, s.p2_x, s.p1_dir,
        s.p1_char, s.p2_char, s.timer,
        s.p1_stun, s.p2_stun, bts(s.p2_stunned),
        bts(s.p2_crouch), s.p2_anim, s.p2_y_vel, bts(s.p2_airborne),
        s.p1_anim, s.p1_y_vel, bts(s.p1_airborne), bts(s.p1_landing_this_frame),
        bts(s.boom_active), fmtf(s.boom_x_est), bts(s.boom_incoming),
        bts(s.boom_slot_active), bts(s.boom_throw_this_frame),
        bts(s.fk_landing_this_frame),
        match_p1_wins, match_p2_wins, bts(match_over), round_result,
        bts(sm_state == "IN_COMBAT"),
        s.frame)
end

local function write_state(s)
    local json_str = to_json(s) .. "\n"
    local f = io.open(P.state, "w")
    if f then
        f:write(json_str); f:flush(); f:close()
        diag_write_ok = diag_write_ok + 1
    else
        diag_write_fail = diag_write_fail + 1
        if diag_write_fail == 1 or diag_write_fail % 300 == 0 then
            print(string.format("[AB v2.22 ID%d] ERROR write_state #%d path=%s",
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

local function tick_fsm(st)
    sm_frame = sm_frame + 1

    local gs         = st.game_state
    local p1_alive   = st.p1_hp > HP_ALIVE
    local p2_alive   = st.p2_hp > HP_ALIVE
    local both_alive = p1_alive and p2_alive

    -- ─── Intercepción global: GS=0x0A (continue expirado) ───────────────────
    if gs == GS_CONT_EXPIRE
       and sm_state ~= "INSERT_COIN"
       and sm_state ~= "PRESS_START"
       and sm_state ~= "BOOTING" then
        print(string.format("[AB v2.22 ID%d F%d] GS=0x0A en '%s' → INSERT_COIN",
            INSTANCE_ID or 0, frame_count, sm_state))
        match_p1_wins      = 0
        match_p2_wins      = 0
        match_over         = false
        round_result       = "none"
        blanka_ko          = false
        _ram_offset_blanka = nil
        _ram_offset_rival  = nil
        is_continue        = false
        transition("INSERT_COIN")
        return
    end

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
            is_continue    = false
            char_nav_step  = 0
            char_nav_frame = 0
            transition("CHAR_NAVIGATE")
        end

    -- =========================================================================
    elseif sm_state == "CHAR_NAVIGATE" then
    -- =========================================================================
        -- Continue: personaje ya confirmado en pantalla, saltar navegación.
        if is_continue then
            transition("CHAR_CONFIRM")
            return
        end

        char_nav_frame = char_nav_frame + 1

        -- BUG 5 FIX (v2.22): SF2CE posiciona el cursor en Ryu (col=0)
        -- siempre al entrar al character select. Sin reset.
        -- Step 0: RIGHT (Ryu → Honda)
        -- Step 1: RIGHT (Honda → Blanka) → CHAR_CONFIRM
        if char_nav_step == 0 then
            if char_nav_frame <= 8 then
                hold("P1 Right")
            else
                release("P1 Right")
                if char_nav_frame >= 20 then
                    char_nav_step  = 1
                    char_nav_frame = 0
                end
            end

        elseif char_nav_step == 1 then
            if char_nav_frame <= 8 then
                hold("P1 Right")
            else
                release("P1 Right")
                if char_nav_frame >= 20 then
                    char_nav_step  = 0
                    char_nav_frame = 0
                    transition("CHAR_CONFIRM")
                end
            end
        end

        if sm_frame >= 300 then
            print("[AB v2.22] TIMEOUT CHAR_NAVIGATE → INSERT_COIN")
            char_nav_step  = 0
            char_nav_frame = 0
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
        if gs == GS_PLAY and both_alive then
            hp_stable_count = hp_stable_count + 1
            local min_wait = is_continue and 90 or HP_STABLE_NEED
            if hp_stable_count >= HP_STABLE_NEED and sm_frame > min_wait then
                hp_stable_count = 0
                local raw_b = ru8(ADDR.P1_WINS)
                local raw_r = ru8(ADDR.P2_WINS)
                if _ram_offset_blanka == nil then
                    _ram_offset_blanka = raw_b
                    _ram_offset_rival  = raw_r
                    print(string.format("[AB v2.22 ID%d] OFFSET sesión: Blanka=%d Rival=%d",
                        INSTANCE_ID or 0, _ram_offset_blanka, _ram_offset_rival))
                end
                match_p1_wins = raw_b - _ram_offset_blanka
                match_p2_wins = raw_r - _ram_offset_rival
                print(string.format("[AB v2.22 ID%d] WAIT→IN_COMBAT B=%d R=%d (cont=%s)",
                    INSTANCE_ID or 0, match_p1_wins, match_p2_wins, bts(is_continue)))
                transition("IN_COMBAT")
            end
        else
            hp_stable_count = 0
        end

        if gs == GS_GAMEOVER then
            print("[AB v2.22] WAIT_COMBAT: GS=0x08 → GAME_OVER")
            transition("GAME_OVER")
            return
        end

        local wait_timeout = is_continue and 1800 or 1200
        if sm_frame >= wait_timeout then
            print(string.format("[AB v2.22] TIMEOUT WAIT_COMBAT (cont=%s, smf=%d) → INSERT_COIN",
                bts(is_continue), sm_frame))
            hp_stable_count = 0
            is_continue     = false
            transition("INSERT_COIN")
        end

    -- =========================================================================
    elseif sm_state == "IN_COMBAT" then
    -- =========================================================================
        diag_combat_frame = diag_combat_frame + 1

        local buttons = read_python_input()
        if buttons then last_input = buttons end

        clear_all()
        local b = last_input
        if (b[1]  or 0) ~= 0 then hold("P1 Up")       end
        if (b[2]  or 0) ~= 0 then hold("P1 Down")      end
        if (b[3]  or 0) ~= 0 then hold("P1 Left")      end
        if (b[4]  or 0) ~= 0 then hold("P1 Right")     end
        if (b[5]  or 0) ~= 0 then hold(BTN_JAB)        end
        if (b[6]  or 0) ~= 0 then hold(BTN_STRONG)     end
        if (b[7]  or 0) ~= 0 then hold(BTN_FIERCE)     end
        if (b[8]  or 0) ~= 0 then hold(BTN_SHORT)      end
        if (b[9]  or 0) ~= 0 then hold(BTN_FORWARD)    end
        if (b[10] or 0) ~= 0 then hold(BTN_ROUNDHOUSE) end

        -- BUG 4 FIX: interceptar GS=0x08 dentro de IN_COMBAT
        if gs == GS_GAMEOVER then
            clear_all()
            local p1h = ru8(ADDR.P1_HP)
            local p2h = ru8(ADDR.P2_HP)
            if p2h == 0 and p1h > 0 then
                round_result  = "win";  match_p1_wins = match_p1_wins + 1; blanka_ko = false
            elseif p1h == 0 and p2h > 0 then
                round_result  = "loss"; match_p2_wins = match_p2_wins + 1; blanka_ko = true
            else
                round_result  = "loss"; match_p2_wins = match_p2_wins + 1; blanka_ko = true
            end
            if match_p1_wins >= 2 or match_p2_wins >= 2 then match_over = true end
            print(string.format(
                "[AB v2.22] IN_COMBAT GS=0x08 | res=%s | B:%d R:%d | F:%d",
                round_result, match_p1_wins, match_p2_wins, frame_count))
            ko_frames = 0; diag_combat_frame = 0
            transition("ROUND_OVER")
            return
        end

        -- Guard HP=255 (overflow CPS1 post-KO)
        local p1r = ru8(ADDR.P1_HP)
        local p2r = ru8(ADDR.P2_HP)
        if p1r >= 0xF0 or p2r >= 0xF0 then
            if diag_combat_frame > 30 then
                clear_all()
                if p2r >= 0xF0 and p1r < 0xF0 then
                    round_result = "win";  match_p1_wins = match_p1_wins + 1; blanka_ko = false
                    print(string.format("[AB v2.22] KO HP255: Rival muerto → WIN | B:%d R:%d",
                        match_p1_wins, match_p2_wins))
                elseif p1r >= 0xF0 and p2r < 0xF0 then
                    round_result = "loss"; match_p2_wins = match_p2_wins + 1; blanka_ko = true
                    print(string.format("[AB v2.22] KO HP255: Blanka muerta → LOSS | B:%d R:%d",
                        match_p1_wins, match_p2_wins))
                else
                    round_result = "draw"
                    match_p1_wins = match_p1_wins + 1; match_p2_wins = match_p2_wins + 1
                    blanka_ko = true
                    print(string.format("[AB v2.22] KO HP255: Double KO → DRAW | B:%d R:%d",
                        match_p1_wins, match_p2_wins))
                end
                if match_p1_wins >= 2 or match_p2_wins >= 2 then match_over = true end
                ko_frames = 0; diag_combat_frame = 0
                transition("ROUND_OVER")
            end
            return
        end

        -- Timeout de combate
        if diag_combat_frame > MAX_COMBAT_FRAMES then
            clear_all()
            if st.p2_hp > st.p1_hp then
                round_result = "loss"; match_p2_wins = match_p2_wins + 1; blanka_ko = true
            elseif st.p1_hp > st.p2_hp then
                round_result = "win";  match_p1_wins = match_p1_wins + 1; blanka_ko = false                
            else
                round_result = "draw"
                match_p1_wins = match_p1_wins + 1; match_p2_wins = match_p2_wins + 1
                blanka_ko = true
            end
            if match_p1_wins >= 2 or match_p2_wins >= 2 then match_over = true end
            print(string.format("[AB v2.22] TIMEOUT COMBATE | Res:%s | B:%d R:%d",
                round_result, match_p1_wins, match_p2_wins))
            ko_frames = 0; diag_combat_frame = 0
            transition("ROUND_OVER")
        end

    -- =========================================================================
    elseif sm_state == "ROUND_OVER" then
    -- =========================================================================
        ko_frames = ko_frames + 1

        if match_over then
            if gs == GS_GAMEOVER then
                print(string.format("[AB v2.22] ROUND_OVER: GS=0x08 → GAME_OVER | ko_f=%d", ko_frames))
                ko_frames = 0
                transition("GAME_OVER")

            elseif gs == GS_PLAY then
                -- BUG 2 FIX: match_over falso si el juego sigue en combate
                local p1h = ru8(ADDR.P1_HP)
                local p2h = ru8(ADDR.P2_HP)
                local combat_hp = (p1h > 0 and p1h <= 144 and p2h > 0 and p2h <= 144)
                if combat_hp and ko_frames > 120 then
                    print(string.format(
                        "[AB v2.22] ROUND_OVER: match_over FALSO | HP=%d/%d ko_f=%d → IN_COMBAT",
                        p1h, p2h, ko_frames))
                    if round_result == "loss" and match_p2_wins > 0 then
                        match_p2_wins = match_p2_wins - 1
                    elseif round_result == "win" and match_p1_wins > 0 then
                        match_p1_wins = match_p1_wins - 1
                    end
                    match_over = false; round_result = "none"
                    ko_frames = 0; diag_combat_frame = 0
                    transition("IN_COMBAT")
                end

            elseif ko_frames > KO_WATCHDOG_FRAMES then
                print(string.format("[AB v2.22] WATCHDOG ROUND_OVER (GS=0x%02X, ko_f=%d) → GAME_OVER",
                    gs, ko_frames))
                ko_frames = 0
                transition("GAME_OVER")
            end

        else
            -- Ronda intermedia: esperar HP en [140,144] (BUG 1 FIX)
            local p1h = ru8(ADDR.P1_HP)
            local p2h = ru8(ADDR.P2_HP)
            local both_full = (p1h >= 140 and p1h <= 144 and p2h >= 140 and p2h <= 144)
            if both_full then
                hp_stable_count = hp_stable_count + 1
                if hp_stable_count >= 10 then
                    print(string.format("[AB v2.22] NUEVA RONDA | B=%d R=%d",
                        match_p1_wins, match_p2_wins))
                    hp_stable_count = 0; ko_frames = 0; diag_combat_frame = 0
                    transition("IN_COMBAT")
                end
            else
                hp_stable_count = 0
                if gs == GS_GAMEOVER then
                    print("[AB v2.22] ROUND_OVER inter-ronda: GS=0x08 → GAME_OVER")
                    ko_frames = 0; transition("GAME_OVER")
                elseif ko_frames > KO_WATCHDOG_FRAMES then
                    print(string.format("[AB v2.22] WATCHDOG ROUND_OVER inter-ronda (ko_f=%d) → GAME_OVER",
                        ko_frames))
                    ko_frames = 0; transition("GAME_OVER")
                end
            end
        end

    -- =========================================================================
    elseif sm_state == "GAME_OVER" then
    -- =========================================================================
        if gs == GS_GAMEOVER then _saw_gs08 = true end

        if gs == GS_GAMEOVER then
            if sm_frame == 1 then hold(BTN_COIN)
            elseif sm_frame == 5 then release(BTN_COIN) end
            if sm_frame % 15 == 0 then hold(BTN_START)
            elseif sm_frame % 15 == 5 then release(BTN_START) end
            if sm_frame % 300 == 0 then
                print(string.format("[AB v2.22 ID%d] GAME_OVER: GS=0x08, martillando Start (smf=%d)",
                    INSTANCE_ID or 0, sm_frame))
            end
        else
            if _saw_gs08 then
                print(string.format(
                    "[AB v2.22 ID%d] GAME_OVER: 0x08→0x%02X (smf=%d) → CHAR_CONFIRM",
                    INSTANCE_ID or 0, gs, sm_frame))
                is_continue        = true
                hp_stable_count    = 0
                match_p1_wins      = 0
                match_p2_wins      = 0
                match_over         = false
                round_result       = "none"
                blanka_ko          = false
                _ram_offset_blanka = nil
                _ram_offset_rival  = nil
                transition("CHAR_CONFIRM")
            else
                -- BUG 2 FIX: continue espurio sin GS=0x08
                print(string.format(
                    "[AB v2.22 ID%d] GAME_OVER: sin GS=0x08 (GS=0x%02X, smf=%d) → ROUND_OVER",
                    INSTANCE_ID or 0, gs, sm_frame))
                ko_frames = 0; transition("ROUND_OVER")
            end
        end

        if sm_frame > KO_WATCHDOG_FRAMES then
            print(string.format("[AB v2.22] WATCHDOG GAME_OVER (smf=%d) → INSERT_COIN", sm_frame))
            is_continue = false; transition("INSERT_COIN")
        end
    end
end

-- =============================================================================
-- BUCLE PRINCIPAL
-- =============================================================================
local function on_frame()
    frame_count = frame_count + 1

    if not _id_resolved then ensure_instance_id() end
    if not _ver_written  then write_ver_file()     end

    try_init()

    local st = read_game_state()

    if _id_resolved then
        write_state(st)
        if diag_write_ok <= 3 or diag_write_ok % 600 == 0 then
            print(string.format("[AB v2.22 ID%d] write_state OK #%d | path=%s | frame=%d",
                INSTANCE_ID or 0, diag_write_ok, tostring(P.state), frame_count))
        end
    elseif frame_count % 120 == 0 then
        print(string.format("[AB v2.22] frame=%d _id_resolved=false _id_attempts=%d",
            frame_count, _id_attempts))
    end

    tick_fsm(st)

    prev_p1_airborne = st.p1_airborne
    prev_p2_airborne = st.p2_airborne

    flush_inputs()

    if frame_count % 600 == 0 then
        local gs_names = {
            [0x00]="TITLE",    [0x02]="COIN",    [0x04]="PLAY",
            [0x06]="ROUNDOVER",[0x08]="GAMEOVER",[0x0A]="CONT_EXP",
            [0x0C]="VS_SCREEN"
        }
        local gs = st.game_state
        local CHAR_NAMES_LUA = {
            "Ryu","Honda","Blanka","Guile","Ken","Chun-Li",
            "Zangief","Dhalsim","Bison","Sagat","Balrog","Vega"
        }
        print(string.format(
            "[AB v2.22 ID%d] estado=%s smf=%d GS=0x%02X(%s) BlankaHP=%d RivalHP=%d rival=%d(%s)"..
            " wr=%d cont=%s bko=%s | B_wins=%d R_wins=%d match_over=%s",
            INSTANCE_ID or 0, sm_state, sm_frame, gs, (gs_names[gs] or "?"),
            st.p1_hp, st.p2_hp, st.p2_char,
            (CHAR_NAMES_LUA[st.p2_char + 1] or "???"),
            diag_write_ok, bts(is_continue), bts(blanka_ko),
            match_p1_wins, match_p2_wins, bts(match_over)))
    end
end

emu.register_frame_done(on_frame, "frame")

print("[AB v2.22] BUG1 FIX: both_full exige HP en [140,144], excluye HP=255 (overflow CPS1)")
print("[AB v2.22] BUG2 FIX: ROUND_OVER detecta match_over falso por GS=0x04+HP combate → revert")
print("[AB v2.22] BUG2 FIX: GAME_OVER requiere _saw_gs08=true para ejecutar continue")
print("[AB v2.22] BUG3 FIX: GS=0x0C registrado como VS_SCREEN (cosmético)")
print("[AB v2.22] BUG4 FIX: IN_COMBAT intercepta GS=0x08 directamente → ROUND_OVER")
print("[AB v2.22] BUG5 FIX: CHAR_NAVIGATE solo RIGHT×2 — sin reset, cursor siempre en Ryu al entrar")
print("[AB v2.22] BUG6 FIX: p1_char hardcodeado a 2 (Blanka) — 0xFF864F no fiable en SF2CE")
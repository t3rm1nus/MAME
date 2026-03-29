-- =============================================================================
-- autoplay_bridge.lua  |  v1.14 |  SF2CE / MAME 0.286  |  29/03/2026
-- =============================================================================
-- CAMBIOS v1.14:
--   · [FIX] PRESS_CONTINUE ahora pulsa "1 Player Start" en lugar de BTN_JAB.
--     En SF2CE la pantalla de CONTINUE no acepta punches — requiere Start P1.
--     Se añaden 3 pulsos espaciados (f=20, f=60, f=100) para mayor fiabilidad.
-- CAMBIOS v1.13 (mantenidos):
--   · Timeout por frames internos (MAX_COMBAT_FRAMES=6400). Sin lectura RAM timer.
--   · timer en state.txt = estimado por frames. timer_raw = RAM diagnóstico.
-- CAMBIOS v1.11 (mantenidos):
--   · ROUND_OVER_WAIT distingue ronda vs game over real por HP.
--   · Double KO → ROUND_OVER_WAIT.
--   · hp_stable >= 10 frames consecutivos anti-falso-positivo.
-- CAMBIOS v1.10 (mantenidos):
--   · WAITING_COMBAT rechaza HP=255 — tope MAX_HP_VALID=144.
-- =============================================================================

local BRIDGE_VERSION = "autoplay_bridge_v1.14"
local BASE_DIR       = "C:\\proyectos\\MAME\\"
local INPUT_FILE     = BASE_DIR .. "mame_input.txt"
local STATE_FILE     = BASE_DIR .. "state.txt"
local STATE_TMP      = BASE_DIR .. "state.tmp"
local VER_FILE       = BASE_DIR .. "bridge_version_0.txt"

do
    local f = io.open(VER_FILE, "w")
    if f then f:write(BRIDGE_VERSION .. "\n"); f:close()
        print("[ABridge] OK -> " .. VER_FILE)
    end
end
print("[ABridge] Iniciando " .. BRIDGE_VERSION)

-- ── DIRECCIONES RAM ───────────────────────────────────────────────────────────
local ADDR = {
    P1_HP=0xFF83E9, P2_HP=0xFF86E9,
    P1_SIDE=0xFF83D0,
    P1_CHAR=0xFF864F, P2_CHAR=0xFF894F,
    P1_X_H=0xFF917C, P1_X_L=0xFF917D,
    P2_X_H=0xFF927C, P2_X_L=0xFF927D,
    P1_STUN=0xFF895A, P2_STUN=0xFF865A, P2_STUN_SPRITE=0xFF8951,
    P2_CROUCH=0xFF86C4, P2_ANIM=0xFF86C1,
    P2_Y_VEL_H=0xFF86FC, P2_Y_VEL_L=0xFF86FD,
    P1_ANIM=0xFF83C1,
    P1_Y_VEL_H=0xFF83FC,
    P1_Y_VEL_L=0xFF83FD,
    PROJ_SLOT=0xFF8E30, PROJ_IMPACT=0xFF8E00,
    TIMER=0xFF8ACE,  -- NO FIABLE (siempre 0). Solo diagnostico.
}

local MAX_COMBAT_FRAMES = 6400

-- ── NOMBRES DE BOTONES ────────────────────────────────────────────────────────
local BTN_JAB        = "P1 Jab Punch"
local BTN_STRONG     = "P1 Strong Punch"
local BTN_FIERCE     = "P1 Fierce Punch"
local BTN_SHORT      = "P1 Short Kick"
local BTN_FORWARD    = "P1 Forward Kick"
local BTN_ROUNDHOUSE = "P1 Roundhouse Kick"

local BUTTON_NAMES = {
    "P1 Up", "P1 Down", "P1 Left", "P1 Right",
    BTN_JAB, BTN_STRONG, BTN_FIERCE,
    BTN_SHORT, BTN_FORWARD, BTN_ROUNDHOUSE,
    "Coin 1", "1 Player Start",
}

local BTN_LABEL = {
    [1]="UP",[2]="DOWN",[3]="LEFT",[4]="RIGHT",
    [5]="JAB",[6]="STRONG",[7]="FIERCE",
    [8]="SHORT",[9]="FORWARD",[10]="RH",
}

-- ── LAZY INIT ─────────────────────────────────────────────────────────────────
local _mem       = nil
local _mem_ok    = false
local _fields    = {}
local _fields_ok = false

local function try_init()
    if _mem_ok and _fields_ok then return true end
    if not _mem_ok then
        local ok, sp = pcall(function()
            return manager.machine.devices[":maincpu"].spaces["program"]
        end)
        if ok and sp then _mem = sp; _mem_ok = true
            print("[ABridge] Memoria CPU lista.")
        end
    end
    if not _fields_ok then
        local ok, ports = pcall(function() return manager.machine.ioport.ports end)
        if ok and ports then
            local all_fields = {}
            local total = 0
            for tag, port in pairs(ports) do
                for fname, field in pairs(port.fields) do
                    all_fields[fname] = field
                    total = total + 1
                end
            end
            if total > 0 then
                local found = 0
                for _, name in ipairs(BUTTON_NAMES) do
                    if all_fields[name] then
                        _fields[name] = all_fields[name]
                        found = found + 1
                        print("  [OK ] " .. name)
                    else
                        print("  [???] " .. name .. " <- NO ENCONTRADO")
                    end
                end
                print(string.format("[ABridge] Campos: %d/%d (de %d totales)",
                    found, #BUTTON_NAMES, total))
                _fields_ok = (found >= 10)
            end
        end
    end
    return _mem_ok and _fields_ok
end

-- ── SISTEMA DE INPUTS ─────────────────────────────────────────────────────────
local _held = {}
local function hold(name)    _held[name] = true end
local function release(name) _held[name] = nil  end
local function clear_held()  _held = {}          end

local function flush_inputs()
    if not _fields_ok then return end
    for name, field in pairs(_fields) do
        local val = _held[name] and 1 or 0
        pcall(function() field:set_value(val) end)
    end
end

-- ── HELPERS RAM ───────────────────────────────────────────────────────────────
local function ru8(a)
    if not _mem then return 0 end; return _mem:read_u8(a)
end
local function ru16(ah, al)
    if not _mem then return 0 end
    return (_mem:read_u8(ah) * 256) + _mem:read_u8(al)
end
local function rs16(ah, al)
    local r = ru16(ah, al); return r >= 0x8000 and r-0x10000 or r
end
local function bts(b) return b and "true" or "false" end

-- ── ESTADO INTERNO ────────────────────────────────────────────────────────────
local frame_count      = 0
local sm_state         = "BOOTING"
local state_frame      = 0
local last_input       = {0,0,0,0,0,0,0,0,0,0,0,0}
local boom_active      = false
local boom_throw_frame = -1
local prev_p2_anim     = 0
local prev_p2_airborne = false
local prev_p1_airborne = false
local BOOM_VEL         = 25
local MIN_HP           = 100
local MAX_HP_VALID     = 144
local hp_stable        = 0
local HP_STABLE_N      = 10

local diag_noop_count    = 0
local diag_action_count  = 0
local diag_combat_frame  = 0
local diag_write_ok      = 0
local diag_write_fail    = 0

-- ── TRANSICIÓN ───────────────────────────────────────────────────────────────
local function transition(s)
    print(string.format("[F%d] %s -> %s", frame_count, sm_state, s))
    sm_state    = s
    state_frame = 0
    hp_stable   = 0
    clear_held()
    if s == "IN_COMBAT" then
        diag_noop_count   = 0
        diag_action_count = 0
        diag_combat_frame = 0
    end
end

-- ── LECTURA INPUT PYTHON ──────────────────────────────────────────────────────
local function read_python_input()
    local f = io.open(INPUT_FILE, "r"); if not f then return nil end
    local l = f:read("*l"); f:close()
    if not l or l == "" then return nil end
    local b = {}
    for v in l:gmatch("([^,]+)") do b[#b+1] = tonumber(v) or 0 end
    return #b >= 10 and b or nil
end

-- ── LECTURA ESTADO JUEGO ──────────────────────────────────────────────────────
local function read_game_state()
    if not _mem_ok then
        return {p1_hp=0,p2_hp=0,p1_x=700,p2_x=700,p1_dir=1,
                p1_char=0,p2_char=0,timer=99,timer_raw=0,
                p1_stun=0,p2_stun=0,p2_stunned=false,
                p2_crouch=false,p2_anim=0,p2_y_vel=0,p2_airborne=false,
                p1_anim=0,p1_y_vel=0,p1_airborne=false,p1_landing_this_frame=false,
                boom_active=false,boom_x_est=0.0,boom_incoming=false,
                boom_slot_active=false,boom_throw_this_frame=false,
                fk_landing_this_frame=false,frame=frame_count}
    end

    local p1hp  = ru8(ADDR.P1_HP);    local p2hp  = ru8(ADDR.P2_HP)
    local p1x   = ru16(ADDR.P1_X_H,   ADDR.P1_X_L)
    local p2x   = ru16(ADDR.P2_X_H,   ADDR.P2_X_L)
    local p1dir = (ru8(ADDR.P1_SIDE) == 0) and 1 or 0
    local p1char= ru8(ADDR.P1_CHAR);  local p2char= ru8(ADDR.P2_CHAR)
    local p1st  = ru8(ADDR.P1_STUN);  local p2st  = ru8(ADDR.P2_STUN)
    local p2ss  = ru8(ADDR.P2_STUN_SPRITE)
    local p2cr  = (ru8(ADDR.P2_CROUCH) == 0x03)
    local p2an  = ru8(ADDR.P2_ANIM)
    local p2yv  = rs16(ADDR.P2_Y_VEL_H, ADDR.P2_Y_VEL_L)
    local p2air = (math.abs(p2yv) > 256)
    local p1an  = ru8(ADDR.P1_ANIM)
    local p1yv  = rs16(ADDR.P1_Y_VEL_H, ADDR.P1_Y_VEL_L)
    local p1air = (math.abs(p1yv) > 256)
    local p1land= prev_p1_airborne and not p1air

    local timer_raw = ru8(ADDR.TIMER)
    local frames_remaining = math.max(0, MAX_COMBAT_FRAMES - diag_combat_frame)
    local timer_est = math.min(99, math.ceil(frames_remaining / 60))

    local bsa   = (ru8(ADDR.PROJ_SLOT) == 0xA4)
    local bi    = (ru8(ADDR.PROJ_IMPACT) == 0x98)

    local boom_thr = false
    if p2an == 0x0C and not p2air and prev_p2_anim ~= 0x0C then
        if not prev_p2_airborne then
            boom_thr = true; boom_active = true; boom_throw_frame = frame_count
        end
    end
    if boom_active and not bsa then
        if frame_count - boom_throw_frame > 180 then boom_active = false end
    end

    local bxe = 0.0
    if boom_active and boom_throw_frame >= 0 then
        local fe = frame_count - boom_throw_frame
        bxe = p2x - fe * BOOM_VEL
        if bxe < 0 then bxe = 0.0; boom_active = false end
    end

    local fkl = false
    if prev_p2_airborne and not p2air then fkl = true; boom_active = false end
    prev_p2_anim = p2an; prev_p2_airborne = p2air; prev_p1_airborne = p1air

    return {p1_hp=p1hp, p2_hp=p2hp, p1_x=p1x, p2_x=p2x, p1_dir=p1dir,
            p1_char=p1char, p2_char=p2char,
            timer=timer_est, timer_raw=timer_raw,
            p1_stun=p1st, p2_stun=p2st, p2_stunned=(p2ss==0x24),
            p2_crouch=p2cr, p2_anim=p2an, p2_y_vel=p2yv, p2_airborne=p2air,
            p1_anim=p1an, p1_y_vel=p1yv, p1_airborne=p1air,
            p1_landing_this_frame=p1land,
            boom_active=boom_active, boom_x_est=bxe,
            boom_incoming=bi, boom_slot_active=bsa,
            boom_throw_this_frame=boom_thr,
            fk_landing_this_frame=fkl, frame=frame_count}
end

-- ── SERIALIZACIÓN JSON ────────────────────────────────────────────────────────
local function float_to_json(v)
    local i = math.floor(v)
    local d = math.floor((v - i) * 10 + 0.5)
    return string.format("%d.%d", i, d)
end

local function to_json(s)
    return string.format(
        '{"p1_hp":%d,"p2_hp":%d,"p1_x":%d,"p2_x":%d,"p1_dir":%d,'..
        '"p1_char":%d,"p2_char":%d,"timer":%d,"timer_raw":%d,'..
        '"p1_stun":%d,"p2_stun":%d,"p2_stunned":%s,'..
        '"p2_crouch":%s,"p2_anim":%d,"p2_y_vel":%d,'..
        '"p2_airborne":%s,'..
        '"p1_anim":%d,"p1_y_vel":%d,"p1_airborne":%s,"p1_landing_this_frame":%s,'..
        '"boom_active":%s,"boom_x_est":%s,"boom_incoming":%s,'..
        '"boom_slot_active":%s,"boom_throw_this_frame":%s,'..
        '"fk_landing_this_frame":%s,"p2_hitstop":0,'..
        '"in_combat":%s,"frame":%d}',
        s.p1_hp, s.p2_hp, s.p1_x, s.p2_x, s.p1_dir,
        s.p1_char, s.p2_char, s.timer, s.timer_raw,
        s.p1_stun, s.p2_stun, bts(s.p2_stunned),
        bts(s.p2_crouch), s.p2_anim, s.p2_y_vel,
        bts(s.p2_airborne),
        s.p1_anim, s.p1_y_vel, bts(s.p1_airborne), bts(s.p1_landing_this_frame),
        bts(s.boom_active), float_to_json(s.boom_x_est), bts(s.boom_incoming),
        bts(s.boom_slot_active), bts(s.boom_throw_this_frame),
        bts(s.fk_landing_this_frame),
        bts(sm_state == "IN_COMBAT"),
        s.frame)
end

local function write_state(s)
    local json_str = to_json(s) .. "\n"
    local f = io.open(STATE_TMP, "w")
    if f then
        f:write(json_str); f:flush(); f:close()
        local ok = os.rename(STATE_TMP, STATE_FILE)
        if ok then diag_write_ok = diag_write_ok + 1; return end
    end
    local f2 = io.open(STATE_FILE, "w")
    if f2 then
        f2:write(json_str); f2:flush(); f2:close()
        diag_write_ok = diag_write_ok + 1
    else
        diag_write_fail = diag_write_fail + 1
        if diag_write_fail % 60 == 1 then
            print(string.format("[ABridge] ERROR write state.txt (fail=%d)", diag_write_fail))
        end
    end
end

-- ── MÁQUINA DE ESTADOS ────────────────────────────────────────────────────────
local function tick_menu(p1hp, p2hp, timer_raw)
    state_frame = state_frame + 1
    local f = state_frame

    if sm_state == "BOOTING" then
        if f >= 360 then transition("DISMISS_WARNING") end

    elseif sm_state == "DISMISS_WARNING" then
        clear_held()
        if f % 30 >= 1  and f % 30 <= 5  then hold(BTN_JAB)           end
        if f % 30 >= 15 and f % 30 <= 19 then hold("1 Player Start")  end
        if f >= 180 then clear_held(); transition("INSERT_COIN") end

    elseif sm_state == "INSERT_COIN" then
        if f == 20 then hold("Coin 1");    print("[ABridge] Pulsando Coin 1")   end
        if f == 26 then release("Coin 1"); print("[ABridge] Soltando Coin 1")   end
        if f >= 100 then transition("PRESS_START") end

    elseif sm_state == "PRESS_START" then
        if f == 20 then hold("1 Player Start");    print("[ABridge] Pulsando 1 Player Start") end
        if f == 26 then release("1 Player Start"); print("[ABridge] Soltando 1 Player Start") end
        if f >= 220 then transition("CHAR_NAVIGATE") end

    elseif sm_state == "CHAR_NAVIGATE" then
        if f == 10 then hold("P1 Right");   print("[ABridge] RIGHT 1") end
        if f == 16 then release("P1 Right") end
        if f == 40 then hold("P1 Right");   print("[ABridge] RIGHT 2") end
        if f == 46 then release("P1 Right") end
        if f >= 90 then transition("CHAR_CONFIRM") end

    elseif sm_state == "CHAR_CONFIRM" then
        if f == 10 then hold(BTN_JAB);    print("[ABridge] JAB confirm (Blanka)") end
        if f == 16 then release(BTN_JAB) end
        if f >= 450 then transition("WAITING_COMBAT") end

    elseif sm_state == "WAITING_COMBAT" then
        local p1_valid = (p1hp >= MIN_HP and p1hp <= MAX_HP_VALID)
        local p2_valid = (p2hp >= MIN_HP and p2hp <= MAX_HP_VALID)
        if p1_valid and p2_valid then
            hp_stable = hp_stable + 1
            if hp_stable >= HP_STABLE_N then transition("IN_COMBAT") end
        else
            hp_stable = 0
        end
        if f >= 2400 then
            print("[ABridge] TIMEOUT WAITING_COMBAT - reiniciando flujo")
            transition("DISMISS_WARNING")
        end

    elseif sm_state == "ROUND_OVER_WAIT" then
        local p1_valid = (p1hp >= MIN_HP and p1hp <= MAX_HP_VALID)
        local p2_valid = (p2hp >= MIN_HP and p2hp <= MAX_HP_VALID)
        if p1_valid and p2_valid then
            hp_stable = hp_stable + 1
            if hp_stable >= HP_STABLE_N then
                print(string.format(
                    "[F%d] ROUND_OVER_WAIT: HP restaurados (P1=%d P2=%d) → siguiente ronda",
                    frame_count, p1hp, p2hp))
                transition("WAITING_COMBAT")
            end
        else
            hp_stable = 0
        end
        if f >= 360 then
            print(string.format(
                "[F%d] ROUND_OVER_WAIT timeout: HP no restaurados → GAME OVER real",
                frame_count))
            transition("GAME_OVER_WAIT")
        end

    elseif sm_state == "GAME_OVER_WAIT" then
        if f == 60  then hold("Coin 1");   print("[ABridge] Coin antes de CONTINUE") end
        if f == 66  then release("Coin 1") end
        if f >= 180 then transition("PRESS_CONTINUE") end

    -- ── [v1.14] FIX CRÍTICO: PRESS_CONTINUE ──────────────────────────────────
    -- SF2CE requiere "1 Player Start" en la pantalla de continue, no BTN_JAB.
    -- 3 pulsos espaciados: cubre variaciones en el timing de aparición
    -- de la pantalla de continue vs el momento de transición del estado.
    elseif sm_state == "PRESS_CONTINUE" then
        if f == 20  then hold("1 Player Start");   print("[ABridge] CONTINUE (Start) — pulso 1") end
        if f == 26  then release("1 Player Start") end
        if f == 60  then hold("1 Player Start");   print("[ABridge] CONTINUE (Start) — pulso 2") end
        if f == 66  then release("1 Player Start") end
        if f == 100 then hold("1 Player Start");   print("[ABridge] CONTINUE (Start) — pulso 3") end
        if f == 106 then release("1 Player Start") end
        if f >= 310 then transition("WAITING_COMBAT") end
    end
end

-- ── BUCLE PRINCIPAL ───────────────────────────────────────────────────────────
local function on_frame()
    frame_count = frame_count + 1

    if not try_init() then
        if frame_count % 60 == 0 then
            print(string.format("[F%d] Esperando máquina MAME... mem=%s fields=%s",
                frame_count, tostring(_mem_ok), tostring(_fields_ok)))
        end
        write_state(read_game_state())
        flush_inputs()
        return
    end

    local st = read_game_state()
    write_state(st)

    if sm_state == "IN_COMBAT" then
        diag_combat_frame = diag_combat_frame + 1

        local buttons = read_python_input()
        if buttons then
            last_input = buttons
            local any_active = false
            local active_names = {}
            for i = 1, 10 do
                if (buttons[i] or 0) ~= 0 then
                    any_active = true
                    active_names[#active_names+1] = (BTN_LABEL[i] or "b"..i)
                end
            end
            if any_active then
                diag_action_count = diag_action_count + 1
                if diag_action_count <= 20 then
                    print(string.format("[INPUT-DIAG] F%d acción#%d: [%s]",
                        frame_count, diag_action_count,
                        table.concat(active_names, "+")))
                end
            else
                diag_noop_count = diag_noop_count + 1
            end
        end

        if diag_combat_frame % 600 == 0 then
            local total = diag_noop_count + diag_action_count
            local pct   = total > 0 and (diag_action_count * 100 / total) or 0
            print(string.format(
                "[DIAG] F%d | cf=%d/%d | NOOPs=%d Acc=%d (%.1f%%) | W_OK=%d W_FAIL=%d",
                frame_count, diag_combat_frame, MAX_COMBAT_FRAMES,
                diag_noop_count, diag_action_count, pct,
                diag_write_ok, diag_write_fail))
        end

        clear_held()
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

        -- ── DETECCIÓN FIN DE RONDA ───────────────────────────────────────────
        local p1_dead = (st.p1_hp <= 0)
        local p2_dead = (st.p2_hp <= 0)
        local timeout = (diag_combat_frame >= MAX_COMBAT_FRAMES)

        if p2_dead and not p1_dead then
            clear_held()
            print(string.format(
                "[DIAG] FIN-RONDA VICTORIA | P1=%d P2=%d | cf=%d NOOPs=%d Acc=%d",
                st.p1_hp, st.p2_hp, diag_combat_frame,
                diag_noop_count, diag_action_count))
            transition("ROUND_OVER_WAIT")

        elseif p1_dead then
            clear_held()
            local tag = p2_dead and "DOUBLE KO" or "DERROTA"
            print(string.format(
                "[DIAG] FIN-RONDA %s | P1=%d P2=%d | cf=%d NOOPs=%d Acc=%d",
                tag, st.p1_hp, st.p2_hp, diag_combat_frame,
                diag_noop_count, diag_action_count))
            transition("ROUND_OVER_WAIT")

        elseif timeout then
            clear_held()
            local tag = (st.p1_hp > st.p2_hp) and "TIMEOUT VICTORIA" or "TIMEOUT DERROTA"
            print(string.format(
                "[DIAG] %s | P1=%d P2=%d | cf=%d/%d | timer_raw=0x%02X",
                tag, st.p1_hp, st.p2_hp,
                diag_combat_frame, MAX_COMBAT_FRAMES, st.timer_raw))
            transition("ROUND_OVER_WAIT")
        end

        if diag_combat_frame % 300 == 0 then
            local fk = "tierra"
            if st.p2_airborne then
                local a = st.p2_anim
                if     a == 0x02 then fk = "FK_ASCENSO"
                elseif a == 0x00 then fk = "FK_CIMA"
                elseif a == 0x04 then fk = "FK_DESCENSO"
                elseif a == 0x0C then fk = "FK_STARTUP" end
            elseif st.p2_anim == 0x0C then fk = "BOOM/FK_LAND" end
            print(string.format(
                "[F%d] P1=%d P2=%d X:%d-%d ANIM=0x%02X(%s) Y=%d | "..
                "TMR_EST=%ds TMR_RAW=0x%02X | cf=%d/%d | BOOM:%s",
                frame_count, st.p1_hp, st.p2_hp, st.p1_x, st.p2_x,
                st.p2_anim, fk, st.p2_y_vel,
                st.timer, st.timer_raw,
                diag_combat_frame, MAX_COMBAT_FRAMES,
                tostring(st.boom_active)))
        end

    else
        tick_menu(st.p1_hp, st.p2_hp, st.timer_raw)
        if frame_count % 120 == 0 then
            local held_str = ""
            for k, _ in pairs(_held) do held_str = held_str .. k .. " " end
            if held_str == "" then held_str = "(ninguno)" end
            print(string.format(
                "[F%d] STATE=%-22s f=%d HP=%d/%d TMR_EST=%d TMR_RAW=0x%02X HELD:[%s]",
                frame_count, sm_state, state_frame,
                st.p1_hp, st.p2_hp, st.timer, st.timer_raw, held_str))
        end
    end

    flush_inputs()
end

emu.register_frame_done(on_frame, "frame")

print("[ABridge] Bridge activo v1.14")
print("  [v1.14] FIX: PRESS_CONTINUE usa '1 Player Start' (3 pulsos f=20/60/100)")
print("  [v1.13] Timeout por frames internos — MAX_COMBAT_FRAMES=" .. MAX_COMBAT_FRAMES)
print("  [v1.13] timer en state.txt = estimado por frames (timer_raw = RAM diagnostico)")
print("  [v1.11] ROUND_OVER_WAIT distingue ronda vs game over real")
print("  -> " .. INPUT_FILE)
print("  -> " .. STATE_FILE)
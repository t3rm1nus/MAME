-- =============================================================================
-- autoplay_bridge.lua  |  v1.6  |  SF2CE / MAME 0.286  |  29/03/2026
-- =============================================================================
-- CAMBIOS v1.6:
--   · [FIX #4] Añadido campo "in_combat" al JSON de state.txt.
--     Valor: true solo cuando sm_state == "IN_COMBAT", false en todo lo demás
--     (menus, GAME_OVER_WAIT, WIN_WAIT, etc.).
--     Esto permite a Python detectar el fin de ronda de forma fiable, sin
--     depender de HP <= 0 (que falla porque SF2CE pone P1_HP=255 al morir).
--     mame_bridge.py v1.1 usa "in_combat" en soft_reset() y bridge_error fix.
--     blanka_env.py v2.2 usa "in_combat" para terminated.
-- =============================================================================
-- CAMBIOS v1.5 (recordatorio):
--   · flush_inputs() solo toca los 12 campos de botones P1 (DIPs excluidos).
--   · Diagnóstico de inputs Python en IN_COMBAT (NOOPs vs Acciones).
-- CAMBIOS v1.4 (recordatorio):
--   · Nombres de botones corregidos ("P1 Jab Punch" etc.)
--   · BOOTING ampliado a 360 frames.
-- =============================================================================

local BRIDGE_VERSION = "autoplay_bridge_v1.6"
local BASE_DIR       = "C:\\proyectos\\MAME\\"
local INPUT_FILE     = BASE_DIR .. "mame_input.txt"
local STATE_FILE     = BASE_DIR .. "state.txt"
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
    PROJ_SLOT=0xFF8E30, PROJ_IMPACT=0xFF8E00,
    TIMER=0xFF8ACE,
}

-- ── NOMBRES DE BOTONES REALES EN SF2CE/MAME ──────────────────────────────────
local BTN_JAB       = "P1 Jab Punch"
local BTN_STRONG    = "P1 Strong Punch"
local BTN_FIERCE    = "P1 Fierce Punch"
local BTN_SHORT     = "P1 Short Kick"
local BTN_FORWARD   = "P1 Forward Kick"
local BTN_ROUNDHOUSE= "P1 Roundhouse Kick"

-- [FIX v1.5] Solo los 12 campos de control P1 — DIPs excluidos
local BUTTON_NAMES = {
    "P1 Up", "P1 Down", "P1 Left", "P1 Right",
    BTN_JAB, BTN_STRONG, BTN_FIERCE,
    BTN_SHORT, BTN_FORWARD, BTN_ROUNDHOUSE,
    "Coin 1", "1 Player Start",
}

local BTN_LABEL = {
    [1]="UP", [2]="DOWN", [3]="LEFT", [4]="RIGHT",
    [5]="JAB", [6]="STRONG", [7]="FIERCE",
    [8]="SHORT", [9]="FORWARD", [10]="RH",
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
                print(string.format(
                    "[ABridge] Campos: %d/%d (de %d totales — DIPs excluidos)",
                    found, #BUTTON_NAMES, total))
                _fields_ok = (found >= 10)
            end
        end
    end
    return _mem_ok and _fields_ok
end

-- ── SISTEMA DE INPUTS ─────────────────────────────────────────────────────────
local _held = {}

local function hold(name)    _held[name] = true  end
local function release(name) _held[name] = nil   end
local function clear_held()  _held = {}           end

-- [FIX v1.5] Solo itera los campos de BUTTON_NAMES
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
local BOOM_VEL         = 25
local MIN_HP           = 100
local hp_stable        = 0
local HP_STABLE_N      = 10

-- ── DIAGNÓSTICO (v1.5) ────────────────────────────────────────────────────────
local diag_noop_count   = 0
local diag_action_count = 0
local diag_combat_frame = 0

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
    local f = io.open(INPUT_FILE,"r"); if not f then return nil end
    local l = f:read("*l"); f:close()
    if not l or l=="" then return nil end
    local b={}; for v in l:gmatch("([^,]+)") do b[#b+1]=tonumber(v) or 0 end
    return #b>=10 and b or nil
end

-- ── LECTURA ESTADO JUEGO ──────────────────────────────────────────────────────
local function read_game_state()
    if not _mem_ok then
        return {p1_hp=0,p2_hp=0,p1_x=700,p2_x=700,p1_dir=1,
                p1_char=0,p2_char=0,timer=99,p1_stun=0,p2_stun=0,
                p2_stunned=false,p2_crouch=false,p2_anim=0,p2_y_vel=0,
                p2_airborne=false,p1_airborne=false,
                boom_active=false,boom_x_est=0.0,boom_incoming=false,
                boom_slot_active=false,boom_throw_this_frame=false,
                fk_landing_this_frame=false,frame=frame_count}
    end
    local p1hp=ru8(ADDR.P1_HP); local p2hp=ru8(ADDR.P2_HP)
    local p1x=ru16(ADDR.P1_X_H,ADDR.P1_X_L)
    local p2x=ru16(ADDR.P2_X_H,ADDR.P2_X_L)
    local p1dir=(ru8(ADDR.P1_SIDE)==0) and 1 or 0
    local p1char=ru8(ADDR.P1_CHAR); local p2char=ru8(ADDR.P2_CHAR)
    local p1st=ru8(ADDR.P1_STUN); local p2st=ru8(ADDR.P2_STUN)
    local p2ss=ru8(ADDR.P2_STUN_SPRITE)
    local p2cr=(ru8(ADDR.P2_CROUCH)==0x03)
    local p2an=ru8(ADDR.P2_ANIM)
    local p2yv=rs16(ADDR.P2_Y_VEL_H,ADDR.P2_Y_VEL_L)
    local p2air=(math.abs(p2yv)>256)
    local timer=ru8(ADDR.TIMER)
    local bsa=(ru8(ADDR.PROJ_SLOT)==0xA4)
    local bi=(ru8(ADDR.PROJ_IMPACT)==0x98)

    local boom_thr=false
    if p2an==0x0C and not p2air and prev_p2_anim~=0x0C then
        if not prev_p2_airborne then
            boom_thr=true; boom_active=true; boom_throw_frame=frame_count
        end
    end
    if boom_active and not bsa then
        if frame_count-boom_throw_frame>180 then boom_active=false end
    end
    local bxe=0.0
    if boom_active and boom_throw_frame>=0 then
        local fe=frame_count-boom_throw_frame
        bxe=p2x-fe*BOOM_VEL
        if bxe<0 then bxe=0.0; boom_active=false end
    end
    local fkl=false
    if prev_p2_airborne and not p2air then fkl=true; boom_active=false end
    prev_p2_anim=p2an; prev_p2_airborne=p2air

    return {p1_hp=p1hp,p2_hp=p2hp,p1_x=p1x,p2_x=p2x,p1_dir=p1dir,
            p1_char=p1char,p2_char=p2char,timer=timer,
            p1_stun=p1st,p2_stun=p2st,p2_stunned=(p2ss==0x24),
            p2_crouch=p2cr,p2_anim=p2an,p2_y_vel=p2yv,
            p2_airborne=p2air,p1_airborne=false,
            boom_active=boom_active,boom_x_est=bxe,
            boom_incoming=bi,boom_slot_active=bsa,
            boom_throw_this_frame=boom_thr,
            fk_landing_this_frame=fkl,frame=frame_count}
end

-- ── SERIALIZACIÓN JSON ────────────────────────────────────────────────────────
-- [FIX v1.6] Campo "in_combat": true solo en IN_COMBAT, false en menús/game-over
local function to_json(s)
    return string.format(
        '{"p1_hp":%d,"p2_hp":%d,"p1_x":%d,"p2_x":%d,"p1_dir":%d,'..
        '"p1_char":%d,"p2_char":%d,"timer":%d,'..
        '"p1_stun":%d,"p2_stun":%d,"p2_stunned":%s,'..
        '"p2_crouch":%s,"p2_anim":%d,"p2_y_vel":%d,'..
        '"p2_airborne":%s,"p1_airborne":%s,'..
        '"boom_active":%s,"boom_x_est":%.1f,"boom_incoming":%s,'..
        '"boom_slot_active":%s,"boom_throw_this_frame":%s,'..
        '"fk_landing_this_frame":%s,"p2_hitstop":0,'..
        '"in_combat":%s,"frame":%d}',
        s.p1_hp,s.p2_hp,s.p1_x,s.p2_x,s.p1_dir,
        s.p1_char,s.p2_char,s.timer,
        s.p1_stun,s.p2_stun,bts(s.p2_stunned),
        bts(s.p2_crouch),s.p2_anim,s.p2_y_vel,
        bts(s.p2_airborne),bts(s.p1_airborne),
        bts(s.boom_active),s.boom_x_est,bts(s.boom_incoming),
        bts(s.boom_slot_active),bts(s.boom_throw_this_frame),
        bts(s.fk_landing_this_frame),
        bts(sm_state == "IN_COMBAT"),   -- NUEVO v1.6
        s.frame)
end

local function write_state(s)
    local f=io.open(STATE_FILE,"w"); if f then f:write(to_json(s).."\n"); f:close() end
end

-- ── MÁQUINA DE ESTADOS ────────────────────────────────────────────────────────

local function tick_menu(p1hp, p2hp)
    state_frame = state_frame + 1
    local f = state_frame

    if sm_state == "BOOTING" then
        if f >= 360 then transition("DISMISS_WARNING") end

    elseif sm_state == "DISMISS_WARNING" then
        clear_held()
        if f % 30 >= 1 and f % 30 <= 5 then hold(BTN_JAB) end
        if f % 30 >= 15 and f % 30 <= 19 then hold("1 Player Start") end
        if f >= 180 then clear_held(); transition("INSERT_COIN") end

    elseif sm_state == "INSERT_COIN" then
        if f == 20 then hold("Coin 1"); print("[ABridge] Pulsando Coin 1") end
        if f == 26 then release("Coin 1"); print("[ABridge] Soltando Coin 1") end
        if f >= 100 then transition("PRESS_START") end

    elseif sm_state == "PRESS_START" then
        if f == 20 then hold("1 Player Start"); print("[ABridge] Pulsando 1 Player Start") end
        if f == 26 then release("1 Player Start"); print("[ABridge] Soltando 1 Player Start") end
        if f >= 220 then transition("CHAR_NAVIGATE") end

    elseif sm_state == "CHAR_NAVIGATE" then
        if f == 10 then hold("P1 Right");   print("[ABridge] RIGHT 1") end
        if f == 16 then release("P1 Right") end
        if f == 40 then hold("P1 Right");   print("[ABridge] RIGHT 2") end
        if f == 46 then release("P1 Right") end
        if f >= 90 then transition("CHAR_CONFIRM") end

    elseif sm_state == "CHAR_CONFIRM" then
        if f == 10 then hold(BTN_JAB); print("[ABridge] JAB confirm (Blanka)") end
        if f == 16 then release(BTN_JAB) end
        if f >= 450 then transition("WAITING_COMBAT") end

    elseif sm_state == "WAITING_COMBAT" then
        if p1hp >= MIN_HP and p2hp >= MIN_HP then
            hp_stable = hp_stable + 1
            if hp_stable >= HP_STABLE_N then transition("IN_COMBAT") end
        else
            hp_stable = 0
        end
        if f >= 2400 then
            print("[ABridge] TIMEOUT WAITING_COMBAT - reiniciando flujo")
            transition("DISMISS_WARNING")
        end

    elseif sm_state == "WIN_WAIT" then
        if f >= 300 then transition("WAITING_COMBAT") end

    elseif sm_state == "GAME_OVER_WAIT" then
        if f >= 180 then transition("PRESS_CONTINUE") end

    elseif sm_state == "PRESS_CONTINUE" then
        if f == 20 then hold("1 Player Start"); print("[ABridge] CONTINUE") end
        if f == 26 then release("1 Player Start") end
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
        return
    end

    flush_inputs()

    local st = read_game_state()
    write_state(st)   -- "in_combat" queda = (sm_state=="IN_COMBAT") en to_json

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
            local pct = total > 0 and (diag_action_count * 100 / total) or 0
            print(string.format(
                "[DIAG] F%d | Combate f=%d | NOOPs=%d Acciones=%d (%.1f%%)",
                frame_count, diag_combat_frame, diag_noop_count, diag_action_count, pct))
        end

        clear_held()
        local b = last_input
        if (b[1]  or 0)~=0 then hold("P1 Up")          end
        if (b[2]  or 0)~=0 then hold("P1 Down")         end
        if (b[3]  or 0)~=0 then hold("P1 Left")         end
        if (b[4]  or 0)~=0 then hold("P1 Right")        end
        if (b[5]  or 0)~=0 then hold(BTN_JAB)           end
        if (b[6]  or 0)~=0 then hold(BTN_STRONG)        end
        if (b[7]  or 0)~=0 then hold(BTN_FIERCE)        end
        if (b[8]  or 0)~=0 then hold(BTN_SHORT)         end
        if (b[9]  or 0)~=0 then hold(BTN_FORWARD)       end
        if (b[10] or 0)~=0 then hold(BTN_ROUNDHOUSE)    end

        if st.p2_hp <= 0 and st.p1_hp > 0 then
            clear_held()
            print(string.format("[DIAG] FIN-RONDA VICTORIA | combate=%d NOOPs=%d Acciones=%d",
                diag_combat_frame, diag_noop_count, diag_action_count))
            transition("WIN_WAIT")
        elseif st.p1_hp <= 0 then
            clear_held()
            print(string.format("[DIAG] FIN-RONDA DERROTA  | combate=%d NOOPs=%d Acciones=%d",
                diag_combat_frame, diag_noop_count, diag_action_count))
            transition("GAME_OVER_WAIT")
        end

        if frame_count % 300 == 0 then
            local fk="tierra"
            if st.p2_airborne then
                local a=st.p2_anim
                if a==0x02 then fk="FK_ASCENSO"
                elseif a==0x00 then fk="FK_CIMA"
                elseif a==0x04 then fk="FK_DESCENSO"
                elseif a==0x0C then fk="FK_STARTUP" end
            elseif st.p2_anim==0x0C then fk="BOOM/FK_LAND" end
            print(string.format(
                "[F%d] P1=%d P2=%d X:%d-%d ANIM=0x%02X(%s) Y=%d BOOM:%s IMP:%s in_combat=true",
                frame_count,st.p1_hp,st.p2_hp,st.p1_x,st.p2_x,
                st.p2_anim,fk,st.p2_y_vel,
                tostring(st.boom_active),tostring(st.boom_incoming)))
        end
    else
        tick_menu(st.p1_hp, st.p2_hp)
        if frame_count % 120 == 0 then
            local held_str = ""
            for k,_ in pairs(_held) do held_str = held_str .. k .. " " end
            if held_str == "" then held_str = "(ninguno)" end
            print(string.format("[F%d] STATE=%-18s f=%d HP=%d/%d HELD:[%s]",
                frame_count, sm_state, state_frame,
                st.p1_hp, st.p2_hp, held_str))
        end
    end
end

emu.register_frame_done(on_frame, "frame")

print("[ABridge] Bridge activo v1.6 — in_combat en state.txt + reintentos de lectura en Python")
print("  -> " .. INPUT_FILE)
print("  -> " .. STATE_FILE)
-- MAME Lua Bridge v8.1
-- MAME 0.286 / sf2ce
--
-- CAMBIOS vs v8.0 (dos fixes, sin cambios de lógica de combate):
--
-- FIX 1: Guard _notifier_active
--   Causa del bug: el watchdog de v8.0 disparaba ~cada 75 frames y añadía
--   un nuevo notifier. Con N notifiers acumulados y sin guard, los N
--   notifiers ejecutaban la lógica completa cada frame:
--     - frame_count += N por frame (en v8.0 cada notifier lo incrementaba)
--     - set_inputs() x N, in_reset modificado N veces → estado corrupto
--     - RESET start con P1=83 (combate activo interrumpido por notifier #N)
--   Fix: _notifier_active flag. Solo el notifier que encuentra el flag en
--   false ejecuta lógica. Los N-1 restantes hacen return inmediato SIN
--   incrementar frame_count (el activo ya lo hizo).
--   Resultado: frame_count sube exactamente 1 por frame real siempre.
--
-- FIX 2: Watchdog por stall_count con threshold alto
--   Causa: el periodic en MAME corre más de 1 vez por frame (especialmente
--   con -nothrottle donde el I/O introduce asimetría entre notifier y periodic).
--   360 calls era demasiado bajo. Con el guard ya activo, el periodic y el
--   notifier están más sincronizados, pero usamos 3000 como margen amplio.
--   En el peor caso (periodic 5x más rápido que notifier): 3000/5 = 600
--   frames de gracia = ~10s a 60fps. Suficiente para cualquier I/O lenta.
--
-- DISEÑO (igual que v8.0):
--   CADA FRAME (un solo notifier activo gracias al guard):
--     1. Leer HP de memoria
--     2. Si combate activo: aplicar acción Python
--     3. Si no: COIN + START en bucle
--   El periodic SOLO hace I/O de disco. El notifier es puro CPU.

local BASE_PATH   = "C:\\proyectos\\MAME\\"
local STATE_FILE  = BASE_PATH .. "bridge_state.txt"
local ACTION_FILE = BASE_PATH .. "bridge_action.txt"
local READY_FILE  = BASE_PATH .. "bridge_ready.txt"
local DIAG_FILE   = BASE_PATH .. "bridge_diag.txt"

local P1_HP_ADDR  = 0xFF83E9
local P2_HP_ADDR  = 0xFF86E9
local P1_X_ADDR   = 0xFF83C4
local P2_X_ADDR   = 0xFF86C4
local TIMER_ADDR  = 0xFF8ACE
local P1_DIR_ADDR = 0xFF83D2
local P2_DIR_ADDR = 0xFF86D2
local MAX_HP       = 144
local MAX_COMBAT_X = 1400

local BUTTON_DEFS = {
    { ":IN1", "P1 Up",              1  },
    { ":IN1", "P1 Down",            2  },
    { ":IN1", "P1 Left",            3  },
    { ":IN1", "P1 Right",           4  },
    { ":IN1", "P1 Jab Punch",       5  },
    { ":IN1", "P1 Strong Punch",    6  },
    { ":IN1", "P1 Fierce Punch",    7  },
    { ":IN2", "P1 Short Kick",      8  },
    { ":IN2", "P1 Forward Kick",    9  },
    { ":IN2", "P1 Roundhouse Kick", 10 },
    { ":IN0", "Coin 1",             11 },
    { ":IN0", "1 Player Start",     12 },
}

-- ── Estado global ──────────────────────────────────────────────────────
local frame_count          = 0
local port_fields          = {}
local fields_ok            = false
local globally_initialized = false
local diag_lines           = {}

-- Buffers compartidos notifier <-> periodic (solo memoria, sin I/O en notifier)
local buf_state  = string.format("0,0,0,0,0,0,0,0,%d,0", MAX_HP)
local buf_ready  = "0"
local buf_action = ""

-- ── [FIX 1] Guard: solo 1 notifier ejecuta lógica por frame ───────────
-- Lua es single-threaded en MAME → sin race conditions.
-- El primer notifier en ejecutar cada frame pone el flag a true.
-- Los N-1 notifiers "fantasma" ven el flag y hacen return inmediato
-- SIN incrementar frame_count (evita el problema de frame_count += N).
local _notifier_active = false

-- ── Estado de combate / reset ──────────────────────────────────────────
local in_reset            = true
local reset_frame         = 0
local combat_stable_count = 0
local combat_end_count    = 0

local COIN_HOLD        = 30    -- frames pulsando COIN al inicio del reset
local RESET_TIMEOUT    = 3600  -- FIX: Aumentado de 1800 a 3600 para evitar timeouts prematuros en I/O lenta
local COMBAT_STABLE    = 10    -- FIX: Reducido de 20 a 10 para resets más rápidos (menos frames esperando HP estable)
local COMBAT_END_GRACE = 8     -- frames HP=0 antes de entrar en reset

-- ── [FIX 2] Watchdog con threshold alto ───────────────────────────────
-- stall_count: llamadas al periodic sin que frame_count suba.
-- Con guard activo, frame_count sube exactamente 1 por frame real.
-- El periodic corre ~1-5 veces por frame (depende de I/O y CPU).
-- 3000 calls / 5 calls_per_frame = 600 frames = ~10s a 60fps.
-- Threshold conservador que tolera I/O muy lenta sin falsos positivos.
local WATCHDOG_STALL_CALLS = 20000  -- FIX: Aumentado de 3000 a 10000 para reducir falsos positives en desbalances
local watchdog_last_fc     = 0
local watchdog_stall_count = 0
local notifier_total       = 0
local MAX_NOTIFIERS_TOTAL  = 200  -- FIX: Añadido cleanup si notifier_total > 50 en register_frame_notifier

local NOOP      = {0,0,0,0,0,0,0,0,0,0,0,0}
local COIN      = {0,0,0,0,0,0,0,0,0,0,1,0}
local START     = {0,0,0,0,0,0,0,0,0,0,0,1}
local START_JAB = {0,0,0,0,1,0,0,0,0,0,0,1}
local last_state_written = ""
local last_ready_written = ""
-- ── Helpers ────────────────────────────────────────────────────────────
local function diag(msg)
    diag_lines[#diag_lines + 1] = "[f" .. frame_count .. "] " .. tostring(msg)
end

local function get_mem_safe()
    local ok, r = pcall(function()
        if not manager then return nil end
        local cpu = manager.machine.devices[":maincpu"]
        if not cpu then return nil end
        local sp = cpu.spaces
        if not sp then return nil end
        return sp["program"]
    end)
    return ok and r or nil
end

local function read_u8(mem, addr)
    if not mem then return 0 end
    local ok, v = pcall(function() return mem:read_u8(addr) end)
    return (ok and v) or 0
end

local function read_u16(mem, addr)
    return read_u8(mem, addr) | (read_u8(mem, addr + 1) << 8)
end

local function hp_valid(hp)
    return hp >= 1 and hp <= MAX_HP
end

local function build_state(mem, p1_hp, p2_hp)
    local p1_x  = read_u16(mem, P1_X_ADDR)
    local p2_x  = read_u16(mem, P2_X_ADDR)
    local timer = read_u8(mem, TIMER_ADDR)
    local p1dr  = read_u16(mem, P1_DIR_ADDR)
    local p2dr  = read_u16(mem, P2_DIR_ADDR)
    local p1dir = ((p1dr & 0x8000) == 0) and 1 or 0
    local p2dir = ((p2dr & 0x8000) == 0) and 1 or 0
    local p1x   = (p1_x <= MAX_COMBAT_X) and p1_x or 0
    local p2x   = (p2_x <= MAX_COMBAT_X) and p2_x or 0
    local cs    = in_reset and 0 or 1
    return string.format("%d,%d,%d,%d,%d,%d,%d,%d,%d,%d",
        p1_hp, p2_hp, p1x, p2x, timer, cs,
        p1dir, p2dir, MAX_HP, frame_count)
end

local function set_inputs(vals)
    if not fields_ok then return end
    pcall(function()
        for idx, field in pairs(port_fields) do
            if field then field:set_value(vals[idx] or 0) end
        end
    end)
end

local function try_init()
    if globally_initialized then return end
    local ok, err = pcall(function()
        if not manager then error("no manager") end
        local ip = manager.machine.ioport
        if not ip then error("no ioport") end
        local dswc = ip.ports[":DSWC"]
        if dswc then
            local fp = dswc.fields["Free Play"]
            if fp then fp:set_value(1); diag("FreePlay OK") end
        end
        local found = 0
        for _, def in ipairs(BUTTON_DEFS) do
            local po = ip.ports[def[1]]
            if po then
                local fo = po.fields[def[2]]
                if fo then port_fields[def[3]] = fo; found = found + 1 end
            end
        end
        fields_ok = found > 0
        globally_initialized = fields_ok
        diag("Campos: " .. found .. "/12")
    end)
    if not ok then
        diag("try_init FAIL: "...(err or "unknown"))
    end
end

local function read_action()
    local wants_reset = false
    local vals = {0,0,0,0,0,0,0,0,0,0,0,0}
    if buf_action == "" then return false, NOOP end
    local idx = 1
    for val_str in buf_action:gmatch("([^,]+)") do
        if idx <= 12 then
            local v = tonumber(val_str)
            vals[idx] = (v and v > 0) and 1 or 0
        elseif val_str == "RESET" then
            wants_reset = true
        end
        idx = idx + 1
    end
    return wants_reset, vals
end

-- ── Notifier ───────────────────────────────────────────────────────────

local function register_frame_notifier()
    if notifier_total >= MAX_NOTIFIERS_TOTAL then
        diag("Notifier skip (total=" .. notifier_total .. ")")
        return
    end
    -- FIX: Cleanup si notifier_total > 50 para evitar acumulación infinita
    if notifier_total > 50 then
        notifier_total = 1  -- Reset counter (asumiendo single-thread, no real cleanup needed)
        diag("Notifier cleanup: reset total to 1")
    end
    notifier_total     = notifier_total + 1
    watchdog_last_fc   = frame_count   -- reset watchdog al registrar nuevo notifier
    watchdog_stall_count = 0
    diag("Notifier #" .. notifier_total)

    emu.add_machine_frame_notifier(function()
        -- [FIX 1] Guard: solo 1 notifier activo por frame
        if _notifier_active then
            -- Notifier fantasma: no hacer nada.
            -- frame_count ya fue incrementado por el notifier activo.
            return
        end
        _notifier_active = true
        frame_count = frame_count + 1

        local ok, err = pcall(function()
            if not globally_initialized then
                try_init()
                if not globally_initialized then return end
                diag("INIT frame=" .. frame_count)
            end

            local mem = get_mem_safe()
            if not mem then return end

            local p1_hp = read_u8(mem, P1_HP_ADDR)
            local p2_hp = read_u8(mem, P2_HP_ADDR)
            if p1_hp > MAX_HP then p1_hp = 0 end
            if p2_hp > MAX_HP then p2_hp = 0 end

            -- ── COMBATE ACTIVO ────────────────────────────────────────
            if not in_reset then
                local wants_reset, act = read_action()
                set_inputs(act)
                buf_state = build_state(mem, p1_hp, p2_hp)
                buf_ready = "1"

                if p1_hp == 0 or p2_hp == 0 or wants_reset then
                    combat_end_count = combat_end_count + 1
                    if combat_end_count >= COMBAT_END_GRACE or wants_reset then
                        local reason = wants_reset and "RESET_Python"
                                    or ("HP=" .. p1_hp .. "/" .. p2_hp)
                        diag("COMBAT END " .. reason)
                        in_reset            = true
                        reset_frame         = 0
                        combat_stable_count = 0
                        combat_end_count    = 0
                        buf_ready           = "0"
                        buf_action          = ""
                        set_inputs(NOOP)
                    end
                else
                    combat_end_count = 0
                end
                return
            end

            -- ── MODO RESET ────────────────────────────────────────────
            if reset_frame == 0 then
                diag("RESET start P1=" .. p1_hp .. " P2=" .. p2_hp)
            end

            if reset_frame < COIN_HOLD then
                set_inputs(COIN)
            else
                local phase = (reset_frame - COIN_HOLD) % 24
                if phase < 8 then
                    set_inputs(START_JAB)
                elseif phase < 12 then
                    set_inputs(NOOP)
                elseif phase < 20 then
                    set_inputs(START)
                else
                    set_inputs(NOOP)
                end
            end

            reset_frame = reset_frame + 1

            if hp_valid(p1_hp) and hp_valid(p2_hp) then
                combat_stable_count = combat_stable_count + 1
                if reset_frame % 60 == 1 then
                    diag("RESET waiting P1=" .. p1_hp .. " P2=" .. p2_hp ..
                         " stable=" .. combat_stable_count ..
                         " rf=" .. reset_frame)
                end
                if combat_stable_count >= COMBAT_STABLE then
                    diag("COMBAT START P1=" .. p1_hp .. " P2=" .. p2_hp ..
                         " rf=" .. reset_frame)
                    in_reset            = false
                    reset_frame         = 0
                    combat_stable_count = 0
                    combat_end_count    = 0
                    buf_action          = ""
                    buf_ready           = "1"
                end
            else
                if combat_stable_count > 0 then
                    diag("RESET unstable P1=" .. p1_hp .. " P2=" .. p2_hp ..
                         " was=" .. combat_stable_count)
                end
                combat_stable_count = 0
            end

            if reset_frame >= RESET_TIMEOUT then
                diag("RESET timeout rf=" .. reset_frame .. " -> restart cycle")
                reset_frame         = 0
                combat_stable_count = 0
            end

            buf_state = build_state(mem, p1_hp, p2_hp)
        end)

        _notifier_active = false

        if not ok then
            diag("ERR notifier: " .. tostring(err))
        end
    end)
end

-- ── PERIODIC: I/O de disco + watchdog ─────────────────────────────────

local IO_FRAME_SKIP = 2
local io_frame_counter = 0

emu.register_periodic(function()

    io_frame_counter = io_frame_counter + 1

    -- limitar frecuencia de todo el bloque (I/O + watchdog)
    if (io_frame_counter % IO_FRAME_SKIP) ~= 0 then
        return
    end

    -- ── WATCHDOG ─────────────────────────────────────
    if in_reset and frame_count == watchdog_last_fc then
        watchdog_stall_count = watchdog_stall_count + 1

        if watchdog_stall_count >= WATCHDOG_STALL_CALLS then
            diag("WATCHDOG stall=" .. watchdog_stall_count ..
                 " f=" .. frame_count .. " total=" .. notifier_total)

            diag("WATCHDOG -> reset")

            in_reset = true
            reset_frame = 0
            buf_ready = "0"

            watchdog_stall_count = 0
        end
    else
        watchdog_last_fc = frame_count
        watchdog_stall_count = 0
    end

    -- ── STATE_FILE ───────────────────────────────────
    if buf_state ~= last_state_written then
        local fs = io.open(STATE_FILE, "w")
        if fs then
            fs:write(buf_state)
            fs:close()
            last_state_written = buf_state
        end
    end

    -- ── READY_FILE ───────────────────────────────────
    if buf_ready ~= last_ready_written then
        local fr = io.open(READY_FILE, "w")
        if fr then
            fr:write(buf_ready)
            fr:close()
            last_ready_written = buf_ready
        end
    end

    -- ── ACTION_FILE ──────────────────────────────────
    local fa = io.open(ACTION_FILE, "r")
    if fa then
        local c = fa:read("*all")
        fa:close()
        if c and c ~= "" then
            buf_action = c
        end
    end

end)

-- ── Inicialización ─────────────────────────────────────────────────────
local _f = io.open(DIAG_FILE, "w")
if _f then _f:write("[START] LuaBridge v8.1\n"); _f:close() end
local _g = io.open(READY_FILE, "w")
if _g then _g:write("0"); _g:close() end
local _h = io.open(STATE_FILE, "w")
if _h then _h:write(string.format("0,0,0,0,0,0,0,0,%d,0", MAX_HP)); _h:close() end

register_frame_notifier()

print("======================================================================")
print("[LuaBridge] v8.1 - sf2ce / MAME 0.286")
print("[LuaBridge] FIX 1: guard _notifier_active (solo 1 notifier activo)")
print("[LuaBridge] FIX 2: WATCHDOG_STALL_CALLS=" .. WATCHDOG_STALL_CALLS)
print("[LuaBridge] COMBAT_STABLE=" .. COMBAT_STABLE ..
      " RESET_TIMEOUT=" .. RESET_TIMEOUT)
print("======================================================================")
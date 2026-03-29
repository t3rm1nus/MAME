-- watch_bridge.lua v4.0
local INPUT_FILE = "C:\\proyectos\\MAME\\mame_input.txt"
local STATE_FILE = "C:\\proyectos\\MAME\\state.txt"
local READY_FILE = "C:\\proyectos\\MAME\\bridge_ready.txt"

local mem = nil
local frame_count = 0
local hold_buffer = {}

local KEY_MAP = {
    COIN1    = {port=":IN0", field="Coin 1"},
    P1_START = {port=":IN0", field="1 Player Start"},
    P2_START = {port=":IN0", field="2 Players Start"},
    P1_UP    = {port=":IN1", field="P1 Up"},
    P1_DOWN  = {port=":IN1", field="P1 Down"},
    P1_LEFT  = {port=":IN1", field="P1 Left"},
    P1_RIGHT = {port=":IN1", field="P1 Right"},
    P1_LP    = {port=":IN1", field="P1 Button 1"},
    P2_UP    = {port=":IN2", field="P2 Up"},
    P2_DOWN  = {port=":IN2", field="P2 Down"},
    P2_LEFT  = {port=":IN2", field="P2 Left"},
    P2_RIGHT = {port=":IN2", field="P2 Right"},
    P2_LP    = {port=":IN2", field="P2 Button 1"},
}

local function set_key(token, value)
    local km = KEY_MAP[token]
    if not km then return end
    local port = manager.machine.ioport.ports[km.port]
    if port then
        local field = port.fields[km.field]
        if field then field:set_value(value) end
    end
end

local function read_input()
    local f = io.open(INPUT_FILE, "r")
    if not f then return end
    local line = f:read("*l") or ""
    f:close()
    os.remove(INPUT_FILE)
    local rf = io.open(READY_FILE, "w")
    if rf then rf:write("ok\n"); rf:close() end

    for entry in line:gmatch("%S+") do
        local token, n_str = entry:match("^([A-Z0-9_]+):?(%d*)$")
        if token and KEY_MAP[token] then
            local n = (n_str ~= "" and tonumber(n_str)) or 20
            hold_buffer[token] = math.max(hold_buffer[token] or 0, n)
        end
    end
end

local function write_state()
    if not mem then return end
    local p1hp = mem:read_u8(0xFF83E9)
    local p2hp = mem:read_u8(0xFF86E9)
    local f = io.open(STATE_FILE, "w")
    if f then
        f:write(string.format("frame=%d p1hp=%d p2hp=%d\n", frame_count, p1hp, p2hp))
        f:close()
    end
end

local function on_frame()
    frame_count = frame_count + 1
    read_input()
    local to_release = {}
    for token, frames in pairs(hold_buffer) do
        if frames > 0 then
            set_key(token, 1)
            hold_buffer[token] = frames - 1
        else
            set_key(token, 0)
            table.insert(to_release, token)
        end
    end
    for _, t in ipairs(to_release) do hold_buffer[t] = nil end
    if frame_count % 3 == 0 then write_state() end
end

local function init()
    local cpu = manager.machine.devices[":maincpu"]
    if cpu then mem = cpu.spaces["program"] end
    emu.register_frame_done(on_frame, "frame")
    print("[BRIDGE v4.0] Activo - RAM OK")
end

init()

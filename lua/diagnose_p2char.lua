-- diagnose_p2char.lua — Escanea RAM para encontrar char ID de P2 en SF2CE
-- Uso: lanzar MAME con -autoboot_script diagnose_p2char.lua
-- Escribe un CSV en C:\proyectos\MAME\p2char_scan.txt cada 60 frames

local BASE_DIR = "C:\\proyectos\\MAME\\"
local OUT_FILE = BASE_DIR .. "p2char_scan.txt"

local _mem = nil
local frame_count = 0
local file_written = false

-- Direcciones candidatas para P2 char ID
local CANDIDATES = {
    {addr=0xFF894F, name="0xFF894F"},
    {addr=0xFF864F, name="0xFF864F_P1"},
    {addr=0xFF834F, name="0xFF834F"},
    -- Bloque entidad P2 base 0xFF8600, offset char en P1 es 0x4F desde base 0xFF8300
    -- P1 base=0xFF8300, P1_CHAR=0xFF864F → offset=0x34F? No, offset=0x14F
    -- Probar offsets del bloque P2
    {addr=0xFF8600, name="P2_base+0x00"},
    {addr=0xFF8601, name="P2_base+0x01"},
    {addr=0xFF8602, name="P2_base+0x02"},
    {addr=0xFF8650, name="P2_base+0x50"},
    {addr=0xFF8651, name="P2_base+0x51"},
    {addr=0xFF8652, name="P2_base+0x52"},
    -- Offset 0x4F en bloque P2
    {addr=0xFF864F, name="P2_blk+0x4F"},
    -- Direcciones cercanas a 0xFF894F
    {addr=0xFF8948, name="0xFF8948"},
    {addr=0xFF8949, name="0xFF8949"},
    {addr=0xFF894A, name="0xFF894A"},
    {addr=0xFF894B, name="0xFF894B"},
    {addr=0xFF894C, name="0xFF894C"},
    {addr=0xFF894D, name="0xFF894D"},
    {addr=0xFF894E, name="0xFF894E"},
    {addr=0xFF894F, name="0xFF894F"},
    {addr=0xFF8950, name="0xFF8950"},
    {addr=0xFF8951, name="0xFF8951"},
    {addr=0xFF8952, name="0xFF8952"},
    -- Bloque diferente
    {addr=0xFF8700, name="0xFF8700"},
    {addr=0xFF8701, name="0xFF8701"},
    {addr=0xFF8723, name="0xFF8723"},
    -- P2 char en otro offset conocido
    {addr=0xFF86C4, name="P2_CROUCH"},  -- referencia conocida
    {addr=0xFF86E9, name="P2_HP"},       -- referencia conocida
}

local P1_HP_ADDR = 0xFF83E9
local P2_HP_ADDR = 0xFF86E9

local function try_init()
    if _mem then return true end
    local ok, sp = pcall(function()
        return manager.machine.devices[":maincpu"].spaces["program"]
    end)
    if ok and sp then _mem = sp; return true end
    return false
end

local function ru8(a)
    if not _mem then return 0 end
    return _mem:read_u8(a)
end

local out_lines = {}
local header_written = false

local function on_frame()
    frame_count = frame_count + 1
    if not try_init() then return end

    -- Solo escanear cada 60 frames cuando ambos HP son válidos (en combate)
    if frame_count % 60 ~= 0 then return end

    local p1hp = ru8(P1_HP_ADDR)
    local p2hp = ru8(P2_HP_ADDR)

    -- Solo en combate activo
    if p1hp < 1 or p1hp > 144 or p2hp < 1 or p2hp > 144 then return end

    -- Escribir header una vez
    if not header_written then
        local h = "frame\tp1hp\tp2hp"
        for _, c in ipairs(CANDIDATES) do
            h = h .. "\t" .. c.name
        end
        out_lines[#out_lines+1] = h
        header_written = true
    end

    -- Leer todas las candidatas
    local line = string.format("%d\t%d\t%d", frame_count, p1hp, p2hp)
    for _, c in ipairs(CANDIDATES) do
        line = line .. "\t" .. tostring(ru8(c.addr))
    end
    out_lines[#out_lines+1] = line

    -- Imprimir en consola también
    print(string.format("[DIAG F%d] P1HP=%d P2HP=%d | 0xFF894F=%d | P2base+0x4F=%d",
        frame_count, p1hp, p2hp, ru8(0xFF894F), ru8(0xFF864F)))

    -- Escribir al archivo cada 10 líneas de datos
    if #out_lines >= 12 then
        local f = io.open(OUT_FILE, "a")
        if f then
            for _, l in ipairs(out_lines) do
                f:write(l .. "\n")
            end
            f:close()
        end
        out_lines = {}
    end
end

emu.register_frame_done(on_frame, "frame")
print("[DIAG] diagnose_p2char.lua cargado - escaneando RAM cada 60 frames")
print("[DIAG] Output: " .. OUT_FILE)
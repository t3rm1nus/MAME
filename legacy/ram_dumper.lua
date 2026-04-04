-- =============================================================================
-- ram_dumper.lua | Escáner de memoria bajo demanda
-- =============================================================================
local BASE_DIR = "C:\\proyectos\\MAME\\"
local DYN_DIR  = BASE_DIR .. "dinamicos\\"
local SIGNAL_FILE = DYN_DIR .. "do_dump.txt"
local OUTPUT_FILE = DYN_DIR .. "dump_out.txt"

local mem = nil

emu.register_frame_done(function()
    local f = io.open(SIGNAL_FILE, "r")
    if f then
        f:close()
        os.remove(SIGNAL_FILE)
        
        if not mem then
            mem = manager.machine.devices[":maincpu"].spaces["program"]
        end
        
        if mem then
            local out = io.open(OUTPUT_FILE, "w")
            -- Rango de RAM de trabajo de la placa CPS1 (8KB)
            for addr = 0xFF8000, 0xFF9FFF do
                local val = mem:read_u8(addr)
                out:write(string.format("%02X\n", val))
            end
            out:close()
            print("[DUMPER] Dump completado: 0xFF8000 - 0xFF9FFF")
        end
    end
end, "frame")

print("[DUMPER] Listo. Esperando señal de Python...")
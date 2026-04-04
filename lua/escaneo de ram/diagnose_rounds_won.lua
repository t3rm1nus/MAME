-- diagnose_rounds_won_v2.lua — Búsqueda estricta de victorias
-- Estrategia: Snapshot al inicio del round vs Snapshot al inicio del SIGUIENTE round.
-- Solo busca incrementos exactos de +1.

local OUT_FILE = "C:\\proyectos\\MAME\\rounds_scan_v2.txt"

-- Direcciones de HP basadas en tu log anterior
local ADDR_P1_HP = 0xFF83EB
local ADDR_P2_HP = 0xFF86EB

-- Rango de RAM a escanear (cubre variables de jugador y de sistema global)
local SCAN_START = 0xFF8000
local SCAN_SIZE  = 0x1000

local function ru8(addr)
    return manager.machine.devices[":maincpu"].spaces["program"]:read_u8(addr)
end

local state = "WAIT_FIGHT_START"
local snap_before = {}
local frame_count = 0

-- Función para capturar el bloque de RAM
local function take_snapshot()
    local snap = {}
    for addr = SCAN_START, SCAN_START + SCAN_SIZE - 1 do
        snap[addr] = ru8(addr)
    end
    return snap
end

-- Función para comparar y buscar solo sumas de +1
local function compare_snapshots(snapA, snapB)
    local diffs = {}
    for addr, valA in pairs(snapA) do
        local valB = snapB[addr] or 0
        -- Un contador de victorias real solo sumará 1 punto (ej. 0->1 o 1->2)
        if valB == valA + 1 and valB <= 3 then
            table.insert(diffs, {addr = addr, old = valA, new = valB})
        end
    end
    return diffs
end

-- Limpiar archivo al inicio
local f = io.open(OUT_FILE, "w")
if f then
    f:write("-- Inicia diagnóstico estricto de rondas --\n")
    f:close()
end

print("================================================================")
print("[ROUNDS_SCAN v2] Script cargado. Esperando inicio de round...")
print("================================================================")

local function on_frame()
    frame_count = frame_count + 1
    local p1hp = ru8(ADDR_P1_HP)
    local p2hp = ru8(ADDR_P2_HP)

    if state == "WAIT_FIGHT_START" then
        -- Esperamos a que ambos tengan la vida llena (inicio de round real)
        if p1hp == 144 and p2hp == 144 then
            snap_before = take_snapshot()
            print(string.format("[F%d] ROUND INICIADO (HP a tope). Snapshot inicial guardado. ¡Pelea!", frame_count))
            state = "FIGHTING"
        end

    elseif state == "FIGHTING" then
        -- Alguien ha perdido
        if (p1hp == 0 or p1hp == 255) or (p2hp == 0 or p2hp == 255) then
            print(string.format("[F%d] K.O. DETECTADO (P1:%d P2:%d). Esperando al siguiente round...", frame_count, p1hp, p2hp))
            state = "WAIT_NEXT_ROUND"
        end

    elseif state == "WAIT_NEXT_ROUND" then
        -- El juego ha reseteado la vida para empezar la siguiente ronda
        if p1hp == 144 and p2hp == 144 then
            print(string.format("[F%d] SIGUIENTE ROUND DETECTADO. Comparando memoria...", frame_count))
            local snap_after = take_snapshot()
            local diffs = compare_snapshots(snap_before, snap_after)

            local log_f = io.open(OUT_FILE, "a")
            log_f:write(string.format("\n--- DIFF ROUND FINALIZADO (Frame %d) ---\n", frame_count))
            print("--- RESULTADOS DEL ESCANEO ---")
            
            if #diffs == 0 then
                print("  >> No se encontraron variables que sumaran +1. El rango podría ser incorrecto.")
            else
                for _, d in ipairs(diffs) do
                    local msg = string.format("  >> CANDIDATO IDEAL: 0x%06X cambió de %d a %d", d.addr, d.old, d.new)
                    print(msg)
                    log_f:write(msg .. "\n")
                end
            end
            log_f:close()

            -- El "después" de este round es el "antes" del siguiente
            snap_before = snap_after
            state = "FIGHTING"
        end
    end
end

emu.register_frame_done(on_frame, "frame")
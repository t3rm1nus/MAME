-- =============================================================================
-- AUTOSTART V32: DETECCIÓN DEFINITIVA P1 / P2 / MODO
-- 
-- CONFIRMADO (ORO PURO):
--   P1 personaje → FF864F
--   P2 personaje → FF894F
--   MODO         → FF87E0-FF87FF: algún byte ≠0 = ARCADE, todo cero = VS
--                  (válido a partir del dump2, ~3 segundos de combate estable)
--
-- INSTRUCCIONES:
--   1. Carga este script ANTES de entrar al combate
--   2. Entra al combate y espera que la consola diga "VEREDICTO FINAL"
--   3. El log ya contiene P1, P2 y MODO correctos
-- =============================================================================

local PATH_LOG = "C:\\proyectos\\MAME\\modo_v32.txt"

-- =================== DIRECCIONES CONFIRMADAS (ORO PURO - NO TOCAR) ==========
local P1_HP_ADDR = 0xFF83E9
local P2_HP_ADDR = 0xFF86E9
local P1_CHAR    = 0xFF864F   -- P1 personaje ✅ CONFIRMADO
local P2_CHAR    = 0xFF894F   -- P2 personaje ✅ CONFIRMADO
-- MODO: FF87E0-FF87FF → algún byte ≠ 0 = ARCADE, todo cero = VS ✅ CONFIRMADO

local CHARS = {
    [0]="Ryu",[1]="E.Honda",[2]="Blanka",[3]="Guile",
    [4]="Ken",[5]="Chun-Li",[6]="Zangief",[7]="Dhalsim",
    [8]="M.Bison",[9]="Sagat",[10]="Balrog",[11]="Vega"
}
local function cname(id) return CHARS[id] or ("ID_"..tostring(id)) end

-- =================== PARÁMETROS ===================
local MAX_HP              = 144
local COMBAT_START_MIN_HP = 140
local COMBAT_STABLE_FRAMES = 5
local DUMP_INTERVAL        = 180   -- 3 segundos entre dumps
local MAX_DUMPS            = 3

-- =================== ESTADO ===================
local frames           = 0
local combat_stable    = 0
local in_combat        = false
local dump_count       = 0
local last_dump_frame  = 0
local veredicto_dado   = false

-- =================== FUNCIÓN: leer modo ===================
-- Cuenta bytes no-cero en FF87E0-FF87FF (32 bytes)
-- Si alguno ≠ 0 → ARCADE (IA CPU activa)
-- Si todos = 0  → VS    (no hay CPU)
local function leer_modo(cpu)
    local nozero = 0
    for i = 0, 31 do
        if cpu:read_u8(0xFF87E0 + i) ~= 0 then
            nozero = nozero + 1
        end
    end
    if nozero > 0 then
        return "ARCADE", nozero
    else
        return "VS", 0
    end
end

-- =================== FUNCIÓN: dump completo ===================
local function volcar(cpu, es_veredicto)
    dump_count = dump_count + 1
    last_dump_frame = frames

    local p1_hp   = cpu:read_u8(P1_HP_ADDR)
    local p2_hp   = cpu:read_u8(P2_HP_ADDR)
    local p1_char = cpu:read_u8(P1_CHAR)
    local p2_char = cpu:read_u8(P2_CHAR)
    local modo, ai_bytes = leer_modo(cpu)

    local file = io.open(PATH_LOG, "a")
    if not file then
        print("V32 ERROR: no puedo escribir en " .. PATH_LOG)
        return
    end

    file:write("\n" .. string.rep("=", 72) .. "\n")

    if es_veredicto then
        file:write(string.format("*** VEREDICTO FINAL *** dump=%d | frame=%d | T=%.2f\n",
            dump_count, frames, emu.time()))
        file:write(string.rep("-", 72) .. "\n")
        file:write(string.format("  P1        → %s (id=%d)\n", cname(p1_char), p1_char))
        file:write(string.format("  P2        → %s (id=%d)\n", cname(p2_char), p2_char))
        file:write(string.format("  MODO      → %s  (bytes IA activos en FF87E0-FF87FF: %d)\n",
            modo, ai_bytes))
        file:write(string.format("  HP P1=%d  HP P2=%d\n", p1_hp, p2_hp))
        file:write(string.rep("=", 72) .. "\n")
    else
        file:write(string.format("DUMP #%d | frame=%d | T=%.2f\n",
            dump_count, frames, emu.time()))
        file:write(string.format("  P1: %s (id=%d) HP=%d\n", cname(p1_char), p1_char, p1_hp))
        file:write(string.format("  P2: %s (id=%d) HP=%d\n", cname(p2_char), p2_char, p2_hp))
        file:write(string.format("  MODO (provisional): %s  (FF87E0-FF87FF bytes activos: %d)\n",
            modo, ai_bytes))
        -- Nota: en dump1 ARCADE puede dar VS porque la IA aún no arrancó
        if dump_count == 1 then
            file:write("  [AVISO] dump1 puede mostrar VS aunque sea ARCADE — esperar dump2\n")
        end
        file:write(string.rep("=", 72) .. "\n")

        -- Detalle del bloque de modo (FF87E0-FF87FF)
        file:write("  Bloque modo FF87E0-FF87FF: ")
        for i = 0, 31 do
            local v = cpu:read_u8(0xFF87E0 + i)
            if v == 0 then file:write(".. ")
            else file:write(string.format("%02X ", v)) end
        end
        file:write("\n")
    end

    file:close()

    if es_veredicto then
        print(string.format("V32 *** VEREDICTO *** P1=%s | P2=%s | MODO=%s (AI_bytes=%d)",
            cname(p1_char), cname(p2_char), modo, ai_bytes))
    else
        print(string.format("V32 DUMP #%d | P1=%s | P2=%s | MODO=%s(provisional,AI=%d)",
            dump_count, cname(p1_char), cname(p2_char), modo, ai_bytes))
    end
end

-- =================== CABECERA DEL LOG ===================
local f = io.open(PATH_LOG, "w")
if f then
    f:write("=============================================================\n")
    f:write("  AUTOSTART V32 — DETECCIÓN DEFINITIVA P1 / P2 / MODO\n")
    f:write("  P1   → FF864F (confirmado)\n")
    f:write("  P2   → FF894F (confirmado)\n")
    f:write("  MODO → FF87E0-FF87FF: ≠0=ARCADE, =0=VS (válido desde dump2)\n")
    f:write("=============================================================\n\n")
    f:close()
    print("V32 LISTO → entra al combate, espera 'VEREDICTO FINAL' en consola")
end

-- =================== LOOP PRINCIPAL ===================
emu.register_frame_done(function()
    frames = frames + 1

    local cpu = manager.machine.devices[":maincpu"].spaces["program"]
    if not cpu then return end

    local p1_hp = cpu:read_u8(P1_HP_ADDR)
    local p2_hp = cpu:read_u8(P2_HP_ADDR)

    local function hp_valid(hp) return hp >= COMBAT_START_MIN_HP and hp <= MAX_HP end

    -- Detección de combate
    if not in_combat then
        if hp_valid(p1_hp) and hp_valid(p2_hp) then
            combat_stable = combat_stable + 1
            if combat_stable >= COMBAT_STABLE_FRAMES then
                in_combat = true
                combat_stable = 0
                print(string.format("V32 COMBATE DETECTADO | P1_HP=%d P2_HP=%d | frame=%d",
                    p1_hp, p2_hp, frames))
                volcar(cpu, false)  -- dump1: modo provisional (ARCADE puede dar VS aquí)
            end
        else
            combat_stable = 0
        end
        return
    end

    -- Dumps periódicos
    if not veredicto_dado and dump_count < MAX_DUMPS and
       (frames - last_dump_frame) >= DUMP_INTERVAL then
        if hp_valid(p1_hp) and hp_valid(p2_hp) then
            local es_veredicto = (dump_count >= 1)  -- dump2 en adelante es veredicto
            volcar(cpu, es_veredicto)
            if es_veredicto then
                veredicto_dado = true
            end
        end
    end

    -- Fin de combate
    if in_combat and (p1_hp == 0 or p2_hp == 0) then
        print(string.format("V32 FIN COMBATE | dumps=%d | veredicto=%s",
            dump_count, veredicto_dado and "SI" or "NO (combate muy corto)"))
        in_combat = false
        combat_stable = 0
        veredicto_dado = false
        dump_count = 0
    end
end, "frame")
-- =============================================================================
-- mapeo_guile_v4.lua — Mapper especifico para Guile (P2)
-- SF2CE | MAME 0.286 | 28/03/2026
-- LOG: C:\proyectos\MAME\mapeo_guile_v4.txt
--
-- CAMBIOS vs v3:
--   [FIX] Vuelven ENT_PROJ_A/B/C al barrido (0xFF9300-0xFF95FF)
--         pero ahora con diff byte-a-byte frame-a-frame (no 16-bit)
--   [FIX] Dump ampliado: vuelca ENT_PROJ_A/B/C en throw e impacto
--   [FIX] Veredicto cada 10 frames (antes solo a f+30)
--   [NEW] Dump de ENT_P2 completo en throw para ver estructura de entidad
--         y comparar offset X (0x7C/7D) con entidades de proyectil
--   [DEL] PROJ_engine2 (0xFF8F00) eliminada — confirmado vacía
--   [!!]  PROTOCOLO NUEVO: Blanka en esquina izquierda, Guile en derecha
--         El boom debe volar >40 frames para que el tracker funcione
-- =============================================================================

-- ── LOG A FICHERO ─────────────────────────────────────────────────────────────
local LOG_PATH = "C:\\proyectos\\MAME\\mapeo_guile_v4.txt"
local log_file = io.open(LOG_PATH, "w")
if not log_file then
    print("[GUILE] ERROR: no se pudo abrir "..LOG_PATH)
end

local frame_count = 0

local function log_raw(line)
    print(line)
    if log_file then
        log_file:write(line.."\n")
        log_file:flush()
    end
end

local function log(tag, msg)
    log_raw(string.format("[GUILE|f%06d|%-16s] %s", frame_count, tag, msg))
end

local function print_diff(diffs, label, max)
    max = max or 24
    if #diffs == 0 then log(label, "(sin cambios)"); return end
    log(label, string.format("=== %d bytes cambiaron ===", #diffs))
    for k=1, math.min(#diffs, max) do
        local d = diffs[k]
        log_raw(string.format("    0x%06X [%-14s +0x%02X]: 0x%02X -> 0x%02X",
            d.addr, d.zone, d.off, d.old, d.new))
    end
    if #diffs > max then log_raw(string.format("    ... +%d mas", #diffs-max)) end
end

-- ── DIRECCIONES — ORO PURO ────────────────────────────────────────────────────
local P1_HP          = 0xFF83E9
local P2_HP          = 0xFF86E9
local P2_ANIM        = 0xFF86C1
local P2_X_H         = 0xFF927C   -- ✅ confirmado
local P2_X_L         = 0xFF927D
local P1_X_H         = 0xFF917C   -- ✅ confirmado
local P1_X_L         = 0xFF917D
local PROJ_SLOT_FLAG = 0xFF8E30
local PROJ_IMPACT    = 0xFF8E00
local P2_YVEL_H      = 0xFF86FC
local P2_YVEL_L      = 0xFF86FD

-- Offset X dentro de bloque de entidad (confirmado para P1 y P2)
-- ENT_P1: 0xFF917C = base 0xFF9100 + 0x7C
-- ENT_P2: 0xFF927C = base 0xFF9200 + 0x7C
-- Si el proyectil sigue el mismo esquema:
--   ENT_PROJ_A X_H = 0xFF9300 + 0x7C = 0xFF937C
--   ENT_PROJ_B X_H = 0xFF9400 + 0x7C = 0xFF947C
--   ENT_PROJ_C X_H = 0xFF9500 + 0x7C = 0xFF957C
local PROJ_A_X_H = 0xFF937C
local PROJ_B_X_H = 0xFF947C
local PROJ_C_X_H = 0xFF957C

-- ── ZONAS A BARRER ────────────────────────────────────────────────────────────
local ZONES = {
    { start=0xFF9100, size=0x100, name="ENT_P1"      },  -- [1]
    { start=0xFF9200, size=0x100, name="ENT_P2"      },  -- [2]
    { start=0xFF9300, size=0x100, name="ENT_PROJ_A"  },  -- [3] ← foco
    { start=0xFF9400, size=0x100, name="ENT_PROJ_B"  },  -- [4] ← foco
    { start=0xFF9500, size=0x100, name="ENT_PROJ_C"  },  -- [5] ← foco
    { start=0xFF8600, size=0x100, name="STATE_P2"    },  -- [6]
    { start=0xFF8E00, size=0x100, name="PROJ_engine" },  -- [7] (referencia)
}

-- Zonas donde buscar X del boom byte-a-byte
local BOOM_SEARCH_ZONES = { 3, 4, 5 }

local IGNORE = {
    [0xFF8ACE]=true, [0xFF83D0]=true, [0xFF86D0]=true,
    [0xFF83E9]=true, [0xFF86E9]=true,
}

-- ── ESTADO GLOBAL ─────────────────────────────────────────────────────────────
local mem
local initialized      = false
local waiting_baseline = true
local snap_baseline    = {}
local snap_prev        = {}

local last_p2_anim     = 0xFF
local last_proj_slot   = 0x00
local last_proj_impact = 0x00
local last_p2_hp       = 0
local last_p1_hp       = 0
local last_p2_throwing = false

local boom_in_flight   = false
local boom_frame_start = -1
local boom_count       = 0
local boom_x_history   = {}
local proj_frame_snaps = {}

local fk_phase         = "NONE"
local fk_start_frame   = -1
local fk_seen_anims    = {}
local fk_count         = 0
local fk_recovery_start= -1
local was_airborne     = false

-- ── HELPERS ───────────────────────────────────────────────────────────────────
local function rb(addr)  return mem:read_u8(addr) end
local function r16(addr) return rb(addr)*256 + rb(addr+1) end

local function take_snap()
    local s = {}
    for i,z in ipairs(ZONES) do
        s[i] = {}
        for j=0,z.size-1 do s[i][j] = rb(z.start+j) end
    end
    return s
end

local function take_boom_snap()
    local s = {}
    for _,zi in ipairs(BOOM_SEARCH_ZONES) do
        local z = ZONES[zi]
        s[zi] = {}
        for j=0,z.size-1 do s[zi][j] = rb(z.start+j) end
    end
    return s
end

local function make_diff(old, new_s)
    local out = {}
    for i,z in ipairs(ZONES) do
        if old[i] then
            for j=0,z.size-1 do
                local addr = z.start+j
                if not IGNORE[addr] and old[i][j] ~= new_s[i][j] then
                    table.insert(out,{
                        addr=addr, old=old[i][j], new=new_s[i][j],
                        zone=z.name, off=j
                    })
                end
            end
        end
    end
    table.sort(out,function(a,b) return a.addr<b.addr end)
    return out
end

local function read_p2_yvel()
    local raw = r16(P2_YVEL_H)
    if raw >= 0x8000 then raw = raw - 0x10000 end
    return raw
end
local function is_p2_airborne() return math.abs(read_p2_yvel()) > 256 end

-- ── DUMPS ─────────────────────────────────────────────────────────────────────
local function dump_zone(base, size, label, rows)
    rows = rows or (size // 16)
    log(label, string.format("=== DUMP 0x%06X (+0x%02X bytes) ===", base, size))
    for row=0, rows-1 do
        local s = string.format("    +0x%02X: ", row*16)
        for col=0,15 do
            if row*16+col < size then
                s = s..string.format("%02X ", rb(base + row*16 + col))
            end
        end
        log_raw(s)
    end
end

local function dump_on_throw()
    -- ENT_P2 completa para ver la estructura y comparar con PROJ
    dump_zone(0xFF9200, 0x100, "THROW_ENT_P2")
    -- Las tres zonas de proyectil — solo primeras 32 bytes cada una
    -- (si tienen datos serán al principio, igual que ENT_P1/P2)
    dump_zone(0xFF9300, 0x80, "THROW_ENT_PROJ_A")
    dump_zone(0xFF9400, 0x80, "THROW_ENT_PROJ_B")
    dump_zone(0xFF9500, 0x80, "THROW_ENT_PROJ_C")
end

local function dump_on_impact()
    dump_zone(0xFF9300, 0x80, "IMPACT_ENT_PROJ_A")
    dump_zone(0xFF9400, 0x80, "IMPACT_ENT_PROJ_B")
    dump_zone(0xFF9500, 0x80, "IMPACT_ENT_PROJ_C")
end

-- Log directo de los offsets X candidatos en las entidades PROJ
local function log_proj_x_candidates()
    local addrs = {
        {addr=PROJ_A_X_H, name="PROJ_A"},
        {addr=PROJ_B_X_H, name="PROJ_B"},
        {addr=PROJ_C_X_H, name="PROJ_C"},
    }
    for _,e in ipairs(addrs) do
        local x = r16(e.addr)
        if x > 0 then
            log("PROJ_X_CAND", string.format(
                "%s offset+0x7C: 0x%04X (%d)", e.name, x, x))
        end
    end
end

-- ── [FIX1] BASELINE CONDICIONAL ───────────────────────────────────────────────
local function try_init_baseline()
    local p2hp = rb(P2_HP)
    local p1hp = rb(P1_HP)
    local p2x  = r16(P2_X_H)
    if p2hp > 0 and p1hp > 0 and p2x >= 0x0100 and frame_count >= 300 then
        initialized      = true
        waiting_baseline = false
        snap_baseline    = take_snap()
        snap_prev        = take_snap()
        last_p2_hp       = p2hp
        last_p1_hp       = p1hp
        last_p2_anim     = rb(P2_ANIM)
        last_proj_slot   = rb(PROJ_SLOT_FLAG)
        last_proj_impact = rb(PROJ_IMPACT)
        was_airborne     = is_p2_airborne()
        last_p2_throwing = (rb(P2_ANIM) == 0x0C)
        log("INIT","=== Baseline tomado ===")
        log("INIT",string.format(
            "P1_HP=%d  P2_HP=%d  P2_ANIM=0x%02X  PROJ_SLOT=0x%02X",
            p1hp, p2hp, rb(P2_ANIM), rb(PROJ_SLOT_FLAG)))
        log("INIT",string.format(
            "P1_X=0x%04X(%d)  P2_X=0x%04X(%d)",
            r16(P1_X_H),r16(P1_X_H), p2x,p2x))
        log("INIT",">>> MUEVE BLANKA A LA ESQUINA IZQUIERDA ANTES DE TIRAR <<<")
        log("INIT",">>> El boom debe volar >40 frames para detectar la X     <<<")
        return true
    end
    return false
end

-- ── [1] SONIC BOOM X — byte-a-byte en ENT_PROJ ───────────────────────────────
local function track_boom_flight(snap_cur)
    if not boom_in_flight then return end
    local frames_vuelo = frame_count - boom_frame_start
    local prev = proj_frame_snaps[frame_count-1]

    if not prev then
        proj_frame_snaps[frame_count] = take_boom_snap()
        -- Log inmediato de offset+0x7C en las tres entidades PROJ
        log_proj_x_candidates()
        return
    end

    local candidates = {}

    for _,zi in ipairs(BOOM_SEARCH_ZONES) do
        local z = ZONES[zi]
        for j=0, z.size-1 do
            if prev[zi] and snap_cur[zi] then
                local old_v = prev[zi][j]
                local new_v = snap_cur[zi][j]
                local delta = old_v - new_v
                local addr  = z.start + j

                -- Filtro byte-a-byte:
                -- Rango amplio: 0x10-0xFF (coordenada X en cualquier formato)
                -- Delta positivo 5-80 (25 ud/frame en byte bajo puede variar)
                -- Excluir ignorados y flags conocidos
                if old_v >= 0x10 and old_v <= 0xFF
                   and delta >= 5 and delta <= 80
                   and not IGNORE[addr] then
                    table.insert(candidates, {
                        addr  = addr,
                        zone  = z.name,
                        off   = j,
                        old   = old_v,
                        new   = new_v,
                        delta = delta,
                    })
                end

                -- También buscar incrementos pequeños (por si la X va al revés)
                local delta_inv = new_v - old_v
                if old_v >= 0x10 and old_v <= 0xFF
                   and delta_inv >= 5 and delta_inv <= 80
                   and not IGNORE[addr] then
                    table.insert(candidates, {
                        addr  = addr,
                        zone  = z.name,
                        off   = j,
                        old   = old_v,
                        new   = new_v,
                        delta = -delta_inv,  -- negativo = incremento
                    })
                end
            end
        end
    end

    -- Loguear cada 8 frames o si hay pocos candidatos
    if #candidates > 0 and (frames_vuelo % 8 == 0 or #candidates <= 4) then
        log("BOOM_X_FLIGHT", string.format(
            "Vuelo f+%d — %d candidatos:", frames_vuelo, #candidates))
        for _,c in ipairs(candidates) do
            local dir = c.delta > 0 and "DEC" or "INC"
            log_raw(string.format(
                "    0x%06X [%s +0x%02X]: 0x%02X->0x%02X (delta=%+d %s)",
                c.addr, c.zone, c.off, c.old, c.new, c.delta, dir))
            table.insert(boom_x_history, {frame=frame_count, addr=c.addr, delta=c.delta})
        end
    end

    -- Log también el valor actual de offset+0x7C cada 16 frames
    if frames_vuelo % 16 == 0 then
        log_proj_x_candidates()
    end

    -- Veredicto cada 10 frames (antes solo a f+30)
    if frames_vuelo > 0 and frames_vuelo % 10 == 0 then
        local ac = {}
        for _,h in ipairs(boom_x_history) do
            local k = string.format("0x%06X", h.addr)
            if not ac[k] then ac[k] = {count=0, dec=0, inc=0} end
            ac[k].count = ac[k].count + 1
            if h.delta > 0 then ac[k].dec = ac[k].dec + 1
            else ac[k].inc = ac[k].inc + 1 end
        end
        if next(ac) then
            log("BOOM_X_VERDICT", string.format("=== Veredicto a f+%d ===", frames_vuelo))
            for addr,d in pairs(ac) do
                local pct = d.count*100//frames_vuelo
                log("BOOM_X_VERDICT", string.format(
                    "  %s -> %d frames (%d%%) dec=%d inc=%d  %s",
                    addr, d.count, pct, d.dec, d.inc,
                    pct >= 70 and ">>> FUERTE <<<" or
                    pct >= 40 and ">> moderado <<" or ""))
            end
        else
            if frames_vuelo == 30 then
                log("BOOM_X_VERDICT",
                    string.format("f+%d — sin candidatos aun", frames_vuelo))
            end
        end
    end

    -- Guardar snapshot
    proj_frame_snaps[frame_count] = take_boom_snap()
    proj_frame_snaps[frame_count-5] = nil
end

-- ── [FIX2] DETECCION BOOM DUAL ───────────────────────────────────────────────
local function check_boom_trigger(snap_cur, cur_anim, cur_proj_s)
    local cur_throwing = (cur_anim == 0x0C)
    local trigger = false
    local reason  = ""

    if cur_proj_s == 0xA4 and last_proj_slot ~= 0xA4 then
        trigger = true
        reason  = "PROJ_SLOT->0xA4"
    end
    if cur_throwing and not last_p2_throwing then
        if not trigger then trigger = true; reason = "ANIM_THROW 0x0C"
        else reason = reason.." + ANIM_THROW" end
    end

    if trigger then
        boom_count       = boom_count + 1
        boom_in_flight   = true
        boom_frame_start = frame_count
        boom_x_history   = {}
        proj_frame_snaps = {}

        log("BOOM_THROW", string.format(
            "BOOM #%d [%s] — P2_X=0x%04X(%d)  P1_X=0x%04X(%d)  DIST=%d",
            boom_count, reason,
            r16(P2_X_H), r16(P2_X_H),
            r16(P1_X_H), r16(P1_X_H),
            math.abs(r16(P2_X_H) - r16(P1_X_H))))

        local dist = math.abs(r16(P2_X_H) - r16(P1_X_H))
        if dist < 200 then
            log("BOOM_THROW","!!! DISTANCIA MUY CORTA (<200) — el boom llegara en <10f")
            log("BOOM_THROW","!!! Aleja mas a Blanka antes de tirar el siguiente boom")
        end

        print_diff(make_diff(snap_prev, snap_cur), "BOOM_THROW_diff", 24)
        dump_on_throw()
    end

    last_p2_throwing = cur_throwing
end

-- ── [2] FLASH KICK ────────────────────────────────────────────────────────────
local function track_flash_kick(snap_cur, cur_anim)
    local airborne = is_p2_airborne()
    local yvel     = read_p2_yvel()

    if airborne and not was_airborne then
        fk_count       = fk_count + 1
        fk_phase       = "STARTUP"
        fk_start_frame = frame_count
        fk_seen_anims  = {}
        log("FK_STARTUP", string.format(
            "FK #%d — DESPEGA Y_VEL=%d ANIM=0x%02X P2_X=%d DIST=%d",
            fk_count, yvel, cur_anim, r16(P2_X_H),
            math.abs(r16(P2_X_H)-r16(P1_X_H))))
        print_diff(make_diff(snap_baseline, snap_cur), "FK_vs_baseline", 16)
    end

    if airborne and not fk_seen_anims[cur_anim] then
        fk_seen_anims[cur_anim] = true
        log("FK_ANIM_NEW", string.format(
            "  ANIM 0x%02X Y_VEL=%d f+%d",
            cur_anim, yvel, frame_count-fk_start_frame))
        print_diff(make_diff(snap_prev, snap_cur), "FK_ANIM_diff", 10)
    end

    if not airborne and was_airborne and fk_phase == "STARTUP" then
        fk_phase          = "RECOVERY"
        fk_recovery_start = frame_count
        local s = ""
        for a,_ in pairs(fk_seen_anims) do s=s..string.format("0x%02X ",a) end
        log("FK_RECOVERY", string.format(
            "FK #%d — ATERRIZA %df airborne ANIM=0x%02X",
            fk_count, frame_count-fk_start_frame, cur_anim))
        log("FK_RECOVERY","Anims: "..s)
        log("FK_RECOVERY",">>> VENTANA ROLLING AQUI <<<")
        print_diff(make_diff(snap_prev, snap_cur), "FK_LANDING_diff", 16)
    end

    if fk_phase == "RECOVERY" and not airborne then
        local rf = frame_count - fk_recovery_start
        if (cur_anim == 0x00 and last_p2_anim ~= 0x00) or rf >= 120 then
            log("FK_RECOVERY_END", string.format(
                "FK #%d FIN — total f+%d", fk_count, frame_count-fk_start_frame))
            fk_phase = "NONE"
        end
    end

    was_airborne = airborne
end

-- ── RESUMEN PERIODICO ─────────────────────────────────────────────────────────
local function print_summary()
    local p1x = r16(P1_X_H)
    local p2x = r16(P2_X_H)
    local fv  = boom_in_flight and (frame_count-boom_frame_start) or -1
    log("SUMMARY", string.format(
        "P1_X=%d  P2_X=%d  DIST=%d  ANIM=0x%02X  FK=%s",
        p1x, p2x, math.abs(p1x-p2x), rb(P2_ANIM), fk_phase))
    log("SUMMARY", string.format(
        "BOOM#%d IN_FLIGHT=%s VUELO_F=%d HIST=%d FK#%d",
        boom_count, tostring(boom_in_flight), fv, #boom_x_history, fk_count))
    log("SUMMARY", string.format(
        "P1_HP=%d P2_HP=%d SLOT=0x%02X IMPACT=0x%02X",
        rb(P1_HP), rb(P2_HP), rb(PROJ_SLOT_FLAG), rb(PROJ_IMPACT)))
    -- Siempre mostrar offset+0x7C de las entidades PROJ
    for _,e in ipairs({
        {addr=PROJ_A_X_H, name="PROJ_A"},
        {addr=PROJ_B_X_H, name="PROJ_B"},
        {addr=PROJ_C_X_H, name="PROJ_C"},
    }) do
        local x = r16(e.addr)
        log("SUMMARY", string.format(
            "  %s offset+0x7C = 0x%04X (%d)", e.name, x, x))
    end
    if #boom_x_history >= 3 then
        local ac = {}
        for _,h in ipairs(boom_x_history) do
            local k=string.format("0x%06X",h.addr); ac[k]=(ac[k] or 0)+1
        end
        log("SUMMARY","Candidatos X acumulados:")
        for addr,count in pairs(ac) do
            log_raw(string.format("    %s -> %d",addr,count))
        end
    end
end

-- ── CALLBACK PRINCIPAL ────────────────────────────────────────────────────────
local function on_frame()
    frame_count = frame_count + 1

    if waiting_baseline then
        if frame_count % 60 == 0 then
            local p2hp=rb(P2_HP); local p2x=r16(P2_X_H)
            if p2hp > 0 then
                log_raw(string.format(
                    "[GUILE|f%06d|WAIT          ] HP=%d X=0x%04X",
                    frame_count, p2hp, p2x))
            end
        end
        try_init_baseline()
        return
    end

    local snap_cur   = take_snap()
    local cur_p2_hp  = rb(P2_HP)
    local cur_p1_hp  = rb(P1_HP)
    local cur_anim   = rb(P2_ANIM)
    local cur_proj_s = rb(PROJ_SLOT_FLAG)
    local cur_impact = rb(PROJ_IMPACT)

    check_boom_trigger(snap_cur, cur_anim, cur_proj_s)

    if cur_impact == 0x98 and last_proj_impact ~= 0x98 then
        boom_in_flight = false
        local fv = boom_frame_start > 0 and frame_count-boom_frame_start or -1
        log("BOOM_IMPACT", string.format(
            "BOOM #%d IMPACTA — %d frames vuelo P1_X=%d P2_X=%d",
            boom_count, fv, r16(P1_X_H), r16(P2_X_H)))
        if fv < 20 then
            log("BOOM_IMPACT","!!! Solo "..fv.." frames — necesitas mas distancia")
        end
        if #boom_x_history > 0 then
            local ac = {}
            for _,h in ipairs(boom_x_history) do
                local k=string.format("0x%06X",h.addr); ac[k]=(ac[k] or 0)+1
            end
            log("BOOM_IMPACT","=== CANDIDATOS FINALES ===")
            for addr,count in pairs(ac) do
                local pct = fv>0 and count*100//fv or 0
                log("BOOM_IMPACT", string.format(
                    "  %s -> %d frames (%d%%)  %s",
                    addr, count, pct,
                    pct>=70 and ">>> CONFIRMAR <<<" or
                    pct>=40 and ">> revisar <<" or ""))
            end
        else
            log("BOOM_IMPACT","Sin candidatos")
        end
        dump_on_impact()
    end

    track_boom_flight(snap_cur)
    track_flash_kick(snap_cur, cur_anim)

    if cur_p2_hp < last_p2_hp then
        log("P2_HIT",string.format("P2: %d->%d (-%d) ANIM=0x%02X",
            last_p2_hp,cur_p2_hp,last_p2_hp-cur_p2_hp,cur_anim))
    end
    if cur_p1_hp < last_p1_hp then
        log("P1_HIT",string.format("P1: %d->%d (-%d) FK=%s BOOM=%s",
            last_p1_hp,cur_p1_hp,last_p1_hp-cur_p1_hp,
            fk_phase,tostring(boom_in_flight)))
    end

    if frame_count % 600 == 0 then print_summary() end

    last_p2_hp=cur_p2_hp; last_p1_hp=cur_p1_hp; last_p2_anim=cur_anim
    last_proj_slot=cur_proj_s; last_proj_impact=cur_impact
    snap_prev=snap_cur
end

-- ── ARRANQUE ──────────────────────────────────────────────────────────────────
local function init()
    local cpu = manager.machine.devices[":maincpu"]
    if not cpu then log_raw("[GUILE] ERROR: :maincpu no encontrado"); return end
    mem = cpu.spaces["program"]
    if not mem then log_raw("[GUILE] ERROR: espacio program no encontrado"); return end
    emu.register_frame_done(on_frame,"frame")
    log_raw("=====================================================")
    log_raw("  mapeo_guile_v4.lua — SF2CE MAME 0.286")
    log_raw("  LOG -> "..LOG_PATH)
    log_raw("=====================================================")
    log_raw("  PROTOCOLO v4 — MUY IMPORTANTE:")
    log_raw("  1) Espera el baseline automatico")
    log_raw("  2) Mueve BLANKA a la esquina IZQUIERDA de pantalla")
    log_raw("     (lo mas lejos posible de Guile)")
    log_raw("  3) Lanza 3x Sonic Boom desde Guile")
    log_raw("     El boom DEBE volar >40 frames (distancia maxima)")
    log_raw("  4) Opcional: 2x Flash Kick si tienes tiempo")
    log_raw("=====================================================")
    log_raw("  HIPOTESIS: PROJ_X en ENT_PROJ_A offset+0x7C (0xFF937C)")
    log_raw("  Se loguea su valor cada frame de vuelo")
    log_raw("=====================================================")
    log_raw("  >> Esperando inicio de combate...")
    log_raw("=====================================================")
end

init()
-- =============================================================================
-- gamestate_scanner.lua  |  v1.0  |  SF2CE / MAME 0.286
-- =============================================================================
-- PROPÓSITO:
--   Registrar el valor de 0xFF8005 (GAME_STATE) y otras direcciones clave
--   en cada pantalla del juego para construir el mapa de estados.
--
-- INSTRUCCIONES: Ver README adjunto o el mensaje de Claude.
-- =============================================================================

local OUT_DIR  = "C:\\proyectos\\MAME\\dinamicos\\"
local OUT_FILE = OUT_DIR .. "gamestate_scan.txt"
local OUT_CSV  = OUT_DIR .. "gamestate_scan.csv"

-- ─── DIRECCIONES A MONITORIZAR ───────────────────────────────────────────────
local ADDR = {
    GAME_STATE   = 0xFF8005,  -- candidato principal
    P1_HP        = 0xFF83E9,
    P2_HP        = 0xFF86E9,
    P1_WINS      = 0xFF8A3F,
    P2_WINS      = 0xFF8A41,
    P2_CHAR      = 0xFF8660,
    -- Candidatos adicionales para el contador de Continue
    -- (zonas típicas de SF2CE para estado de UI)
    CAND_A       = 0xFF8000,
    CAND_B       = 0xFF8001,
    CAND_C       = 0xFF8002,
    CAND_D       = 0xFF8003,
    CAND_E       = 0xFF8004,
    CAND_F       = 0xFF8006,
    CAND_G       = 0xFF8007,
    CAND_H       = 0xFF8008,
    -- Zona de contador de Continue (hipótesis)
    CONT_CAND_1  = 0xFF8010,
    CONT_CAND_2  = 0xFF8011,
    CONT_CAND_3  = 0xFF8020,
    CONT_CAND_4  = 0xFF8B00,
    CONT_CAND_5  = 0xFF8B01,
}

-- ─── ESTADO ──────────────────────────────────────────────────────────────────
local mem         = nil
local frame       = 0
local last_gs     = -1      -- último GAME_STATE visto
local last_snap   = {}      -- snapshot anterior de todas las direcciones
local log_lines   = {}      -- buffer de texto
local csv_lines   = {}      -- buffer CSV
local snap_count  = 0
local FLUSH_EVERY = 300     -- escribir a disco cada 300 frames (~5s)

-- ─── ETIQUETAS MANUALES ──────────────────────────────────────────────────────
-- El operador puede pulsar teclas del teclado numérico para etiquetar
-- el frame actual con el nombre de la pantalla que ve en ese momento.
-- Mapeamos keycodes de MAME a etiquetas.
-- INSTRUCCIÓN: Pulsa la tecla correspondiente en el momento exacto.
--   Numpad 1 → "TITULO"
--   Numpad 2 → "INSERT_COIN"
--   Numpad 3 → "CHAR_SELECT"
--   Numpad 4 → "VS_SCREEN"
--   Numpad 5 → "COMBAT"
--   Numpad 6 → "ROUND_OVER_WIN"
--   Numpad 7 → "ROUND_OVER_LOSS"
--   Numpad 8 → "GAME_OVER_CONTINUE"
--   Numpad 9 → "CONTINUE_EXPIRED"
--   Numpad 0 → "BONUS_STAGE"

local pending_label = nil
local label_frame   = 0

-- ─── INIT ────────────────────────────────────────────────────────────────────
local function try_init()
    if mem then return true end
    local ok, sp = pcall(function()
        return manager.machine.devices[":maincpu"].spaces["program"]
    end)
    if ok and sp then mem = sp; return true end
    return false
end

local function ru8(a)
    if not mem then return 0 end
    local ok, v = pcall(function() return mem:read_u8(a) end)
    return ok and v or 0
end

-- ─── SNAPSHOT ────────────────────────────────────────────────────────────────
local function take_snapshot()
    local s = {}
    for k, a in pairs(ADDR) do
        s[k] = ru8(a)
    end
    return s
end

local function snap_changed(a, b)
    if not b then return true end
    for k, v in pairs(a) do
        if b[k] ~= v then return true end
    end
    return false
end

-- ─── FORMATO ─────────────────────────────────────────────────────────────────
local function fmt_snap(snap, label)
    local gs  = snap.GAME_STATE
    local tag = label and (" *** LABEL=" .. label .. " ***") or ""

    -- Línea compacta con los campos más importantes
    local line = string.format(
        "[F%06d] GS=%02X(%3d) P1HP=%3d P2HP=%3d W=%d/%d CH=%2d | "..
        "B=%02X C=%02X D=%02X E=%02X F=%02X G=%02X H=%02X | "..
        "CC1=%02X CC2=%02X CC3=%02X CC4=%02X CC5=%02X%s",
        frame,
        gs, gs,
        snap.P1_HP, snap.P2_HP,
        snap.P1_WINS, snap.P2_WINS,
        snap.P2_CHAR,
        snap.CAND_B, snap.CAND_C, snap.CAND_D, snap.CAND_E,
        snap.CAND_F, snap.CAND_G, snap.CAND_H,
        snap.CONT_CAND_1, snap.CONT_CAND_2, snap.CONT_CAND_3,
        snap.CONT_CAND_4, snap.CONT_CAND_5,
        tag
    )
    return line
end

local function fmt_csv(snap, label)
    return string.format("%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%s",
        frame,
        snap.GAME_STATE, snap.P1_HP, snap.P2_HP,
        snap.P1_WINS, snap.P2_WINS, snap.P2_CHAR,
        snap.CAND_A, snap.CAND_B, snap.CAND_C, snap.CAND_D,
        snap.CAND_E, snap.CAND_F, snap.CAND_G, snap.CAND_H,
        snap.CONT_CAND_1, snap.CONT_CAND_2, snap.CONT_CAND_3,
        snap.CONT_CAND_4, snap.CONT_CAND_5,
        label or ""
    )
end

-- ─── FLUSH ───────────────────────────────────────────────────────────────────
local function flush()
    if #log_lines == 0 then return end

    -- Texto legible
    local f = io.open(OUT_FILE, "a")
    if f then
        for _, l in ipairs(log_lines) do f:write(l .. "\n") end
        f:close()
    end

    -- CSV para análisis posterior
    local g = io.open(OUT_CSV, "a")
    if g then
        for _, l in ipairs(csv_lines) do g:write(l .. "\n") end
        g:close()
    end

    log_lines = {}
    csv_lines = {}
end

-- ─── BUCLE PRINCIPAL ─────────────────────────────────────────────────────────
local function on_frame()
    frame = frame + 1

    if not try_init() then return end

    -- Cabecera CSV en el primer frame
    if frame == 1 then
        local hdr = "frame,GAME_STATE,P1_HP,P2_HP,P1_WINS,P2_WINS,P2_CHAR,"..
                    "CAND_A,CAND_B,CAND_C,CAND_D,CAND_E,CAND_F,CAND_G,CAND_H,"..
                    "CONT1,CONT2,CONT3,CONT4,CONT5,label"
        local g = io.open(OUT_CSV, "w")
        if g then g:write(hdr .. "\n"); g:close() end

        local f2 = io.open(OUT_FILE, "w")
        if f2 then
            f2:write("=== gamestate_scanner.lua v1.0 | SF2CE ===\n")
            f2:write("Inicio frame=" .. frame .. "\n")
            f2:write("Columnas: GS=0xFF8005 | B=FF8001 C=FF8002 D=FF8003 E=FF8004 F=FF8006 G=FF8007 H=FF8008\n")
            f2:write("          CC1=FF8010 CC2=FF8011 CC3=FF8020 CC4=FF8B00 CC5=FF8B01\n")
            f2:write("INSTRUCCIÓN: Pulsa Numpad 1-9,0 para etiquetar la pantalla actual\n")
            f2:write(string.rep("=", 80) .. "\n")
            f2:close()
        end

        print("[SCANNER] Iniciado. Archivos:")
        print("  Texto : " .. OUT_FILE)
        print("  CSV   : " .. OUT_CSV)
        print("[SCANNER] Controles de etiquetado:")
        print("  Numpad1=TITULO  2=INSERT_COIN  3=CHAR_SELECT  4=VS_SCREEN")
        print("  Numpad5=COMBAT  6=ROUND_WIN    7=ROUND_LOSS   8=GAME_OVER_CONTINUE")
        print("  Numpad9=CONTINUE_EXPIRED       Numpad0=BONUS_STAGE")
    end

    local snap = take_snapshot()
    local gs   = snap.GAME_STATE

    -- ── Detectar cambio de GAME_STATE → siempre loguear ─────────────────────
    local force_log = (gs ~= last_gs)
    if force_log then
        local marker = string.format(
            "\n>>> GAME_STATE CAMBIÓ: %02X → %02X (frame=%d) <<<\n",
            last_gs == -1 and 0 or last_gs, gs, frame)
        log_lines[#log_lines+1] = marker
        csv_lines[#csv_lines+1] = fmt_csv(snap, "GS_CHANGE_" .. string.format("%02X", gs))
        print(marker:gsub("\n",""))
        last_gs = gs
    end

    -- ── Etiquetar frame si el operador pulsó una tecla ───────────────────────
    -- (la detección de input de teclado en MAME Lua es limitada;
    --  usamos un archivo de señal como alternativa robusta — ver instrucciones)
    local label_file = OUT_DIR .. "scanner_label.txt"
    local lf = io.open(label_file, "r")
    if lf then
        local lbl = lf:read("*l"); lf:close()
        if lbl and lbl ~= "" then
            pending_label = lbl:gsub("%s+", "")
            label_frame   = frame
            os.remove(label_file)
            print(string.format("[SCANNER] ETIQUETA RECIBIDA: '%s' en frame=%d GS=%02X",
                pending_label, frame, gs))
        end
    end

    local label = nil
    if pending_label and (frame - label_frame) <= 5 then
        label = pending_label
    elseif pending_label and (frame - label_frame) > 5 then
        pending_label = nil
    end

    -- ── Loguear si hubo cambio en cualquier dirección O si hay etiqueta ──────
    local changed = force_log or label or snap_changed(snap, last_snap)

    -- También loguear cada 60 frames para tener contexto temporal continuo
    local periodic = (frame % 60 == 0)

    if changed or periodic then
        snap_count = snap_count + 1
        local line = fmt_snap(snap, label)
        log_lines[#log_lines+1] = line
        csv_lines[#csv_lines+1] = fmt_csv(snap, label)

        if label then
            print(string.format("[SCANNER F%d] %s", frame, line))
        end
    end

    last_snap = snap

    -- ── Flush a disco ─────────────────────────────────────────────────────────
    if frame % FLUSH_EVERY == 0 then
        flush()
        print(string.format("[SCANNER] Flush F%d | snaps=%d | GS actual=%02X(%d)",
            frame, snap_count, gs, gs))
    end
end

emu.register_frame_done(on_frame, "frame")
print("[SCANNER v1.0] Registrado. Esperando frames...")
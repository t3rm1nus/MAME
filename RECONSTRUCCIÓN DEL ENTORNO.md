# 🧠 ROADMAP — HACIA LA AUTONOMÍA TOTAL (28/03/2026)

## 🟢 FASE 1: LÓGICA DE COMBATE (100% COMPLETADA)
* Ejecución de ataques especiales (Rolling, Electricidad) desde Python.
* **Rolling Attack 100% funcional** (bola + avance real).
* Control de tiempos de carga (68 frames back + release 1f + forward+Strong 4f).
* Force charge + easy flag + force anim + force posición X + sprite list.
* **Release de left reducido a milisegundo visible**.
* Macro lista para acción 15 del nuevo entorno.

**Archivos clave:**
- `force_rolling_final.lua` (versión definitiva)
- Macro integrada en el nuevo `blanka_env.py`

---

## 🟢 FASE 2: EXTRACCIÓN DE DATOS (100% COMPLETADA — 27/03/2026)

### Qué se consiguió:
1. ✅ Sincronización de HP y SIDE.
2. ✅ Detección de Posición y Lado.
3. ✅ IDs de Personajes P1 y P2 — confirmados por ingeniería inversa de RAM.
4. ✅ Detección de Modo (ARCADE vs VS) — confirmada por diff de bloques de memoria.
5. ✅ Cronómetro — dirección confirmada.

### Cómo se obtuvo la detección de modo:
- **V30**: barrido 64 bytes (FF8000-FF803F) + bloque IA (FF8C40-6F). Descartada.
- **V31**: barrido 4 bloques de 256 bytes. Identificado `FF87E0-FF87FF` como candidato.
- **V32**: veredicto definitivo. Doble dump para evitar falso negativo del primer frame.

---

## 🟢 FASE 3.1: ESTADO DINÁMICO P2 (100% COMPLETADA — 28/03/2026)

### Qué se consiguió:
1. ✅ **P2_X** (`0xFF927C-7D`, 16-bit big-endian) — evidencia: carry byte alto 02→03 durante throw. Rango: 728–855.
2. ✅ **P2_CROUCH_FLAG** (`0xFF86C4`) — valor canónico corregido a `0x03` (agachado).
3. ✅ **P2_STUN** (`0xFF865A`) y **P2_STUN_SPRITE** (`0xFF8951 = 0x24`).
4. ✅ **P2_ANIM_FRAME** (`0xFF86C1`): `0x0C` = lanzamiento Sonic Boom (y FK — ver Fase 3.2).
5. ✅ **P2_Y_VEL** (`0xFF86FC-FD`): signed 16-bit; `abs > 256` = airborne.
6. ✅ **PROJ_SLOT_FLAG** (`0xFF8E30 = 0xA4`) y **PROJ_IMPACT** (`0xFF8E00 = 0x98`).
7. ✅ **autostart_v33.lua** — narración acciones P2 en tiempo real, supresión logs oki.

---

## 🟢 FASE 3.2: FK Y SONIC BOOM — COMPLETADA (28/03/2026)

### Flash Kick — Anatomía definitiva (mapeo_guile_v4.lua)

Duración arco real: **~150 frames**. FK abortado (solo startup): ~24 frames.

| Fase | ANIM_FRAME | Y_VEL (signed) | Frame relativo |
|------|-----------|----------------|----------------|
| Startup | `0x0C` | −288 | f+0 |
| Ascenso | `0x02` | −2304 | f+26 |
| Cima | `0x00` | −2304 | f+123 |
| Descenso | `0x04` | +1760 | f+126 |
| Landing/Recovery | `0x0C` | ~0 | f+150 |

**⚠️ ANIM=0x0C es ambiguo:** aparece en Sonic Boom throw (tierra), FK startup (airborne, Y_VEL=−288) y FK landing (tierra). Desambiguar siempre con `abs(Y_VEL) > 256`:
- `0x0C + abs(Y_VEL) > 256` → FK startup o en vuelo
- `0x0C + Y_VEL~0` → Sonic Boom throw O FK landing/recovery

**Ventana de Rolling Attack:** al detectar el flanco `airborne→tierra` (FK_RECOVERY).

### Sonic Boom — PROJ_X: hipótesis descartada, workaround validado

**Investigación:** `mapeo_guile_v4.lua` con diff byte-a-byte frame-contra-frame en bloques `0xFF9300-0xFF9500` (ENT_PROJ_A/B/C) durante vuelo de boom a distancia máxima.

**Conclusión:**
- Los bloques `0xFF9300`, `0xFF9400`, `0xFF9500` permanecen a **cero durante todo el vuelo** del boom. El proyectil no tiene entidad en esos slots.
- La hipótesis `0xFF937C` (ENT_PROJ_A offset+0x7C) queda **definitivamente descartada**.
- El offset `+0x7C` de `ENT_P2` (`0xFF927C-7D`) contiene la X de **Guile**, no del proyectil.
- La X del boom en vuelo probablemente reside en la tabla de sprites/objetos de CPS1 fuera del rango escaneado. Requeriría barrido más amplio (fuera del scope del proyecto actual).

**Workaround definitivo (suficiente para el agente PPO):**
1. Detectar throw: `ANIM_FRAME == 0x0C` con `Y_VEL~0` (tierra)
2. Estimar X: `P2_X - frames_desde_throw × 25 ud/frame`
3. Impacto inminente: `PROJ_IMPACT == 0x98` (~0.5s pre-daño)

---

## 🔴 FASE 4: INTELIGENCIA ARTIFICIAL — PPO (PRÓXIMA)

**Vector de estado completo:**
```
[HP_P1, HP_P2, P1_X, P2_X, Distancia_X, Lado, Timer,
 Charge_Proxy, MODO, P1_ID, P2_ID,
 P2_CROUCH, P2_AIRBORNE, P2_STUN,
 FK_PHASE,           ← nuevo: startup/ascenso/cima/descenso/landing
 BOOM_ACTIVO, BOOM_X_EST, BOOM_INCOMING]
```

**Acciones:** Walk, Jump, Attack, Rolling, Electricidad.

**Recompensas:**
- +10 por quitar vida a P2
- -15 por recibir daño
- +100 por ganar round
- +15 por rolling en FK recovery de Guile
- +5 por saltar el Sonic Boom

---

## 🧹 LIMPIEZA DEL REPOSITORIO (HECHA)
* Archivos legacy movidos a `/legacy/`:
  - `sf2_env_blanka.py` (v3.4), todos los `test_*` antiguos
  - `autonomy.py`, `ram_reader.py` (parcial)
  - `autostart_v30.lua`, `autostart_v31.lua`, `autostart_v32.lua`
* Archivos activos:
  - `force_rolling_final.lua`
  - `lua/lua_bridge.lua`
  - `lua/autostart_v33.lua` ← diagnóstico definitivo + narración P2
  - `mame_controller.py`
  - `env/blanka_env.py` (en construcción)
  - `config/constants.py` ← fuente de verdad de todas las direcciones RAM

---

## 📋 DIRECCIONES RAM — ORO PURO (PROHIBIDO BORRAR O CAMBIAR)
```
VIDA
  P1_HP_ADDR      = 0xFF83E9
  P2_HP_ADDR      = 0xFF86E9
  P2_HP_DISPLAY2  = 0xFF86EB  (lag 1-2f)

LADO
  P1_SIDE_ADDR    = 0xFF83D0  (9 flips reales)
  P2_SIDE_ADDR    = 0xFF86D0  (18 flips reales)

PERSONAJES
  P1_CHAR_ADDR    = 0xFF864F  (byte directo, ID 0-11)
  P2_CHAR_ADDR    = 0xFF894F  (byte directo, ID 0-11)
  NOTA: 0xFF874F descartada — devuelve siempre 0x00

MODO ARCADE vs VS
  MODO_BLOCK      = 0xFF87E0 → 0xFF87FF (32 bytes)
  Lógica          = any(byte != 0) → ARCADE / all(byte == 0) → VS
  Caveat          = leer a partir del 2º dump (~3s de combate)

CRONÓMETRO
  TIMER_ADDR      = 0xFF8ACE  (alias 0xFF8AC1 obsoleto)

POSICIÓN X
  P1_X_H_ADDR     = 0xFF917C  ✅ (byte alto, 16-bit big-endian)
  P1_X_L_ADDR     = 0xFF917D  ✅ (byte bajo)
  P2_X_H_ADDR     = 0xFF927C  ✅ (byte alto, 16-bit big-endian)
  P2_X_L_ADDR     = 0xFF927D  ✅ (byte bajo)
  Rango P2        = 0x02D8 (728) .. 0x0357 (855)
  Coordenadas     = valores MAYORES = más a la DERECHA

STUN
  P2_STUN_ADDR        = 0xFF865A  (+5/hit)
  P2_STUN_SPRITE_ADDR = 0xFF8951  (0x24 = pajaritos activos)
  P1_STUN_ADDR        = 0xFF895A  (+5/hit)

POSE / ANIMACIÓN
  P2_CROUCH_FLAG_ADDR = 0xFF86C4  (0x03=agachado | 0x02=de pie)
  P2_ANIM_FRAME_ADDR  = 0xFF86C1  (ver tabla FK)

FLASH KICK — ANATOMÍA DEFINITIVA (mapeo_guile_v4 — 28/03/2026)
  Fase          ANIM   Y_VEL     Frame
  Startup       0x0C   −288      f+0
  Ascenso       0x02   −2304     f+26
  Cima          0x00   −2304     f+123
  Descenso      0x04   +1760     f+126
  Landing       0x0C   ~0        f+150
  Duración real ~150f | Abortado ~24f
  ⚠️ 0x0C ambiguo: abs(Y_VEL)>256 → FK | Y_VEL~0 → Boom throw o Landing

AIRBORNE
  P2_Y_VEL_H_ADDR = 0xFF86FC
  P2_Y_VEL_L_ADDR = 0xFF86FD  (signed 16-bit; abs>256 = en el aire)

PROYECTIL SONIC BOOM
  PROJ_SLOT_FLAG_ADDR = 0xFF8E30  (0xA4 = slot activo)
  PROJ_IMPACT_ADDR    = 0xFF8E00  (0x98 = impacto ~0.5s antes del daño)
  PROJ_X_ADDR         = ❌ NO EXISTE (ENT_PROJ_A/B/C permanecen a cero)
  Workaround          = P2_X - frames_desde_throw × 25 ud/frame
  BOOM_VEL_APPROX     = 25 ud/frame
```

---

**Próximo paso inmediato:** integrar vector de estado completo (incluyendo FK_PHASE) en `env/blanka_env.py`. Arrancar entrenamiento PPO vs Guile determinista.

**Meta final:** 100% winrate + perfects en todos los combates vs Guile determinista.
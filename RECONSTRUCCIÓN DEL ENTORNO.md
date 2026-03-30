# 🧠 ROADMAP — HACIA LA AUTONOMÍA TOTAL (29/03/2026)

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
5. ✅ Cronómetro — dirección `0xFF8ACE` encontrada pero **NO FIABLE** (siempre 0 en combate). Reemplazado por timer estimado de frames internos en el bridge.

### Cómo se obtuvo la detección de modo:
- **V30**: barrido 64 bytes (FF8000-FF803F) + bloque IA (FF8C40-6F). Descartada.
- **V31**: barrido 4 bloques de 256 bytes. Identificado `FF87E0-FF87FF` como candidato.
- **V32**: veredicto definitivo. Doble dump para evitar falso negativo del primer frame.

---

## 🟢 FASE 3.1: ESTADO DINÁMICO P2 (100% COMPLETADA — 28/03/2026)

### Qué se consiguió:
1. ✅ **P2_X** (`0xFF927C-7D`, 16-bit big-endian) — Rango: 728–855.
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

**⚠️ ANIM=0x0C es ambiguo:** aparece en Sonic Boom throw (tierra), FK startup (airborne, Y_VEL=−288) y FK landing (tierra). Desambiguar siempre con `abs(Y_VEL) > 256`.

**Ventana de Rolling Attack:** al detectar el flanco `airborne→tierra` (FK_RECOVERY).

### Sonic Boom — PROJ_X: hipótesis descartada, workaround validado

**Conclusión:** Los bloques `0xFF9300`, `0xFF9400`, `0xFF9500` permanecen a cero durante todo el vuelo del boom.

**Workaround definitivo:**
1. Detectar throw: `ANIM_FRAME == 0x0C` con `Y_VEL~0` (tierra)
2. Estimar X: `P2_X - frames_desde_throw × 25 ud/frame`
3. Impacto inminente: `PROJ_IMPACT == 0x98` (~0.5s pre-daño)

---

## 🟢 FASE 3.3: BRIDGE Y ENTORNO PPO (100% COMPLETADA — 28/03/2026 sesión 3)

### Qué se consiguió:
1. ✅ **`mame_bridge.py`** — bridge de archivos Python↔MAME con API limpia.
2. ✅ **`env/blanka_env.py` v2.1** — claves de estado alineadas con lua_bridge v2.0.
3. ✅ **`core/rival_registry.py`** — registro persistente de estadísticas por rival.
4. ✅ **`train_blanka_v1.py`** — entrenamiento PPO single-env visible.

---

## 🟢 FASE 3.4: FIXES BRIDGE Y ENTORNO (100% COMPLETADA — 29/03/2026)

### autoplay_bridge.lua v1.13
- Timer RAM `0xFF8ACE` confirmado como inútil (devuelve siempre 0).
- Timer en `state.txt` = `ceil((MAX_COMBAT_FRAMES - diag_combat_frame) / 60)`, fiable.
- `MAX_COMBAT_FRAMES = 6400` (99s × 60fps + 7% margen).

### blanka_env.py v2.7
- Recompensas Rolling elevadas para compensar crédito tardío de macro 69f:
  - Rolling hit + distancia óptima: **+30** (antes +12)
  - Rolling hit fuera de distancia: **+20** (antes +8)
  - Rolling en ventana post-FK con hit: **+35** (antes +15)
  - Rolling en ventana post-FK sin hit: **+8** (antes +4)
  - Rolling sin hit: **-2** (penalización suave)
- Bonus de carga acumulada: +0.05/step mientras se mantiene ← (total +3.4 en 68 steps).

### autoplay_bridge.lua v1.14
- **FIX CRÍTICO:** `PRESS_CONTINUE` usaba `BTN_JAB` — SF2CE no acepta punches en esa pantalla. Cambiado a `"1 Player Start"` con 3 pulsos espaciados (f=20, f=60, f=100).

### blanka_env.py v2.8
- Flag `ROLLING_ONLY: bool = False` en línea ~45.
- Cuando `True`, `step()` ignora la política del agente y fuerza acción 15.
- El flip de dirección (LEFT↔RIGHT) funciona automáticamente en ambos lados.
- Imprime stats de hit rate al final de cada episodio.

### autoplay_bridge.lua v1.15
- **FIX:** Nuevo estado `CHAR_SELECT_CONTINUE` entre `PRESS_CONTINUE` y `WAITING_COMBAT`.
- Tras el continue, SF2CE muestra char select con cursor ya en Blanka.
- Sin input hace timeout (~9s) y elige personaje aleatoriamente.
- El bridge pulsa JAB dos veces (f=20, f=60) para confirmar Blanka de inmediato.
- Flujo: `PRESS_CONTINUE → CHAR_SELECT_CONTINUE → WAITING_COMBAT`

---

## 🔴 FASE 4: INTELIGENCIA ARTIFICIAL — PPO (EN CURSO)

**Vector de estado completo (30 dimensiones):**
```
[HP_P1, P1_X, P1_AIRBORNE, P1_DIR, P1_STUN,
 HP_P2, P2_X, P2_CROUCH, P2_AIRBORNE, P2_STUN,
 DISTANCIA, DISTANCIA_SIGNED, TIMER,
 P1_EN_ESQUINA, P2_EN_ESQUINA, DIFF_VIDA,
 DAÑO_RECIBIDO_RECIENTE, DAÑO_INFLIGIDO_RECIENTE,
 FK_PHASE, BOOM_ACTIVO, BOOM_X_EST, PROJ_SLOT, BOOM_TIMER,
 CARGA_ACUMULADA, RIVAL_TIERRA_MUCHO, RIVAL_HITSTOP,
 VENTANA_POST_FK, ULTIMA_ACCION, BLANKA_ATERRIZANDO, ROLLING_JUMP_RDY]
```

**Acciones (24):** 0-14 single frame | 15 Rolling | 16 Electricidad | 17-22 Saltos con ataque | 23 Rolling-Jump

**Recompensas clave:**
- +100 por ganar ronda
- +35 rolling con hit en ventana FK
- +30 rolling con hit a distancia óptima
- +10 por quitar vida a P2
- -50 por perder ronda
- -15 por recibir daño

---

## 🧹 LIMPIEZA DEL REPOSITORIO (HECHA)
* Archivos legacy movidos a `/legacy/`:
  - `sf2_env_blanka.py` (v3.4), todos los `test_*` antiguos
  - `autonomy.py`, `ram_reader.py` (parcial)
  - `autostart_v30.lua` .. `autostart_v32.lua`
* Archivos activos:
  - `force_rolling_final.lua`
  - `lua/autoplay_bridge.lua` ← v1.15 activo
  - `lua/lua_bridge.lua`
  - `lua/autostart_v33.lua`
  - `mame_controller.py`
  - `env/blanka_env.py` ← v2.8
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
  MODO_BLOCK      = 0xFF87E0 -> 0xFF87FF (32 bytes)
  Logica          = any(byte != 0) -> ARCADE / all(byte == 0) -> VS
  Caveat          = leer a partir del 2º dump (~3s de combate)

CRONOMETRO
  TIMER_ADDR      = 0xFF8ACE  ⚠️ NO FIABLE — siempre devuelve 0 en combate.
                               Usar timer estimado: ceil((6400 - combat_frame) / 60)

POSICION X
  P1_X_H_ADDR     = 0xFF917C  (byte alto, 16-bit big-endian)
  P1_X_L_ADDR     = 0xFF917D  (byte bajo)
  P2_X_H_ADDR     = 0xFF927C  (byte alto, 16-bit big-endian)
  P2_X_L_ADDR     = 0xFF927D  (byte bajo)
  Rango P2        = 0x02D8 (728) .. 0x0357 (855)
  Coordenadas     = valores MAYORES = mas a la DERECHA

STUN
  P2_STUN_ADDR        = 0xFF865A  (+5/hit)
  P2_STUN_SPRITE_ADDR = 0xFF8951  (0x24 = pajaritos activos)
  P1_STUN_ADDR        = 0xFF895A  (+5/hit)

POSE / ANIMACION
  P2_CROUCH_FLAG_ADDR = 0xFF86C4  (0x03=agachado | 0x02=de pie)
  P2_ANIM_FRAME_ADDR  = 0xFF86C1  (ver tabla FK)

FLASH KICK — ANATOMIA DEFINITIVA (mapeo_guile_v4 — 28/03/2026)
  Fase          ANIM   Y_VEL     Frame
  Startup       0x0C   -288      f+0
  Ascenso       0x02   -2304     f+26
  Cima          0x00   -2304     f+123
  Descenso      0x04   +1760     f+126
  Landing       0x0C   ~0        f+150
  Duracion real ~150f | Abortado ~24f
  ⚠️ 0x0C ambiguo: abs(Y_VEL)>256 -> FK | Y_VEL~0 -> Boom throw o Landing

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
## 🔜 PRÓXIMO PASO
  ENTRENAMIENTO PPO
  4. Curriculum learning — Planning completo
Fase CL-1: ROLLING_ONLY Y ROLLING puro (ya tienes el flag)

ROLLING_AND_ELECTRIC_ONLY = True, 200-500 episodios
Objetivo: que el modelo entienda la geometría del rolling y del electric (distancia, dirección, timing post-FK)
Métrica de salida: hit rate > 40%

Fase CL-2: Defensa ante Boom y FK

Nueva flag DEFENSE_ONLY = True — fuerza acciones 1 (UP para saltar Boom), 3/4 (moverse), 23 (rolling-jump)
Se desactivan acciones de ataque por completo en esta fase
Objetivo: que el agente nunca reciba daño de Boom ni FK
Métrica de salida: daño recibido promedio < 20 HP por episodio

Fase CL-3: Ofensiva combinada (saltos + rolling)

Activar acciones 15-23, desactivar 0-14 excepto NOOP y direcciones
Sin macros de un solo frame al principio — forzar exploración de macros con action_mask
Objetivo: que el agente conecte al menos 1 rolling + 1 salto por episodio
Métrica: ep_p2_dmg > 30 en promedio

Fase CL-4: PPO completo sin restricciones

Cargar el modelo entrenado en Fase CL-3 como punto de partida
Las 24 acciones disponibles, recompensas normales
Objetivo final: winrate > 70%, después escalar a 100% + perfects

Implementación técnica: Una clase CurriculumScheduler que inspecciona métricas del RivalRegistry y cambia flags entre fases. Se puede integrar en train_blanka_v1.py con un callback de SB3.




**Meta final:** 100% winrate + perfects en todos los combates vs ia determinista.
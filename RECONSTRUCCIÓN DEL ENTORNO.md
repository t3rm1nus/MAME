# 🧠 ROADMAP — HACIA LA AUTONOMÍA TOTAL (actualizado 30/03/2026)

## 🟢 FASE 1: LÓGICA DE COMBATE (100% COMPLETADA)
* Ejecución de ataques especiales (Rolling, Electricidad) desde Python.
* **Rolling Attack 100% funcional** (bola + avance real).
* Control de tiempos de carga (68 frames back + release 1f + forward+Strong 4f).
* Macro lista para acciones 15/16/17 del entorno.

**Archivos clave:**
- `force_rolling_final.lua` (versión definitiva)
- Macros integradas en `blanka_env.py`

---

## 🟢 FASE 2: EXTRACCIÓN DE DATOS (100% COMPLETADA — 27/03/2026)

1. ✅ HP y SIDE sincronizados.
2. ✅ IDs de Personajes P1 y P2 — ingeniería inversa de RAM.
3. ✅ Detección de Modo ARCADE vs VS — diff de bloques de memoria.
4. ✅ Cronómetro — `0xFF8ACE` encontrada pero **NO FIABLE**. Sustituida por timer de frames internos.

---

## 🟢 FASE 3.1: ESTADO DINÁMICO P2 (100% — 28/03/2026)

1. ✅ **P2_X** (`0xFF927C-7D`, 16-bit big-endian) — Rango: 728–855.
2. ✅ **P2_CROUCH_FLAG** (`0xFF86C4 = 0x03` agachado).
3. ✅ **P2_STUN** (`0xFF865A`) y **P2_STUN_SPRITE** (`0xFF8951 = 0x24`).
4. ✅ **P2_ANIM_FRAME** (`0xFF86C1`).
5. ✅ **P2_Y_VEL** (`0xFF86FC-FD`): signed 16-bit; `abs > 256` = airborne.
6. ✅ **PROJ_SLOT_FLAG** (`0xFF8E30 = 0xA4`) y **PROJ_IMPACT** (`0xFF8E00 = 0x98`).

---

## 🟢 FASE 3.2: FK Y SONIC BOOM (100% — 28/03/2026)

### Flash Kick — Anatomía definitiva

| Fase | ANIM_FRAME | Y_VEL | Frame relativo |
|------|-----------|-------|----------------|
| Startup | `0x0C` | −288 | f+0 |
| Ascenso | `0x02` | −2304 | f+26 |
| Cima | `0x00` | −2304 | f+123 |
| Descenso | `0x04` | +1760 | f+126 |
| Landing | `0x0C` | ~0 | f+150 |

⚠️ `ANIM=0x0C` ambiguo: aparece en Boom throw, FK startup y FK landing. Desambiguar con `abs(Y_VEL) > 256`.

Ventana de Rolling Attack: flanco `airborne→tierra` (FK_RECOVERY). Obs[26] = `0 < fk_land_steps <= 20`.

### Sonic Boom — PROJ_X descartada

`0xFF9300-0xFF9500` permanecen a cero durante el vuelo. Workaround:
1. Detectar throw: `ANIM=0x0C` con `Y_VEL~0`.
2. Estimar X: `P2_X − frames_desde_throw × 25 ud/frame`.
3. Impacto inminente: `PROJ_IMPACT = 0x98`.

---

## 🟢 FASE 3.3: BRIDGE Y ENTORNO PPO (100% — 28/03/2026 sesión 3)

1. ✅ **`mame_bridge.py`** — bridge de archivos Python↔MAME.
2. ✅ **`env/blanka_env.py` v2.1** — primer entorno Gymnasium operativo.
3. ✅ **`core/rival_registry.py`** — registro persistente de estadísticas.
4. ✅ **`train_blanka_v1.py`** — entrenamiento PPO single-env visible.

---

## 🟢 FASE 3.4: FIXES BRIDGE Y ENTORNO (100% — 29/03/2026 al 30/03/2026)

### autoplay_bridge.lua → v2.3 (30/03/2026)

**v1.13:** Timer RAM `0xFF8ACE` = 0 confirmado. Timer estimado por frames internos (`MAX_COMBAT_FRAMES=6400`).

**v1.14:** FIX CRÍTICO — `PRESS_CONTINUE` usaba JAB. SF2CE no acepta punches en esa pantalla. Cambiado a `"1 Player Start"` con 3 pulsos.

**v1.15:** Estado `CHAR_SELECT_CONTINUE` entre PRESS_CONTINUE y WAIT_COMBAT. Bridge pulsa JAB ×2 para confirmar Blanka sin esperar timeout de ~9s.

**v2.3 (VERSIÓN ACTUAL):** FSM completamente rediseñada como event-driven pura.
- Eliminado `CHAR_SELECT_CONTINUE` — sustituido por `is_continue=true` en `CHAR_NAVIGATE`.
- `GAME_OVER` inserta coin + pulsa Start en bucle hasta detectar HP restaurados.
- **FIX anti-bucle**: `wait_timeout_count` — si `WAIT_COMBAT` expira con `is_continue=true` más de `WAIT_TIMEOUT_MAX=3` veces consecutivas, fuerza reinicio completo desde `INSERT_COIN`. Resuelve el bug de vuelta a pantalla de título tras continues fallidos.
- Reset de `wait_timeout_count` al entrar en `IN_COMBAT`.

### blanka_env.py → v4.4 (VERSIÓN ACTUAL)

**v2.7:** Recompensas Rolling elevadas. Bonus de carga acumulada +0.05/step.

**v2.8:** Flag `ROLLING_ONLY: bool = False`. Modo depuración que fuerza acción 15 en cada step.

**v3.x–v4.3:** (intermediate) Añadidas acciones 16–25. Tracking de bosses, bonus stages, arcade clear. Round tracking granular. Eliminado ROLLING_ONLY, introducido ROLLING_AND_ELECTRIC_ONLY. Episodio extendido a arcade completo.

**v4.4 (ACTUAL):**
- **[FIX CRÍTICO]** Eliminada truncación por `MENU_FRAMES_MAX`. Los frames fuera de combate son parte normal del flujo — no terminan el episodio.
- **[FIX]** `ep_rounds_played` se inicializa a 1 en reset (primer combate). Se incrementa solo al detectar inicio real de ronda.
- **[FIX]** `ep_matches_played` inicializado a 0 en reset; incrementa al detectar primer combate y en cada cambio de rival.
- **[LIMPIEZA]** `_out_of_combat_frames` solo es métrica diagnóstica; no trunca.

### mame_bridge.py → v1.3 (VERSIÓN ACTUAL)
- Paths movidos a `BASE_DIR\dinamicos\`. Siempre con sufijo `_N`.
- Directorio `dinamicos\` creado automáticamente.
- Consistente con Lua v2.3.

### train_blanka_v1.py → v2.1 (VERSIÓN ACTUAL)
- Episodio NO termina por Game Over.
- `reset()` no llama a `soft_reset`/`restart_game` — el Lua gestiona todo.
- Rewards en frames fuera de combate = 0.
- Tracking: `arcade_clears`, `boss_X_eps` en TensorBoard.

### train_FASE1.py → v2.1 (NUEVA)
- 6 instancias MAME headless con claim protocol.
- `SubprocVecEnv`, `N_STEPS=1365` (×6 = 8190 por rollout), `ent_coef=0.10`.
- Tracking granular: `cl1/boss_*_eps`, `cl1/arcade_clears`, `cl1/bonus_stage_eps`.
- Print inmediato al alcanzar cada boss y al completar el arcade.

---

## 🔴 FASE 4: INTELIGENCIA ARTIFICIAL — PPO (EN CURSO)

### Semántica del episodio (CLAVE)
```
reset() → espera in_combat=True, ambos HP >= 100 (hasta 60s)
step() × N:
  · frames fuera de combate: reward=0, sin truncación
  · Game Over: el Lua pulsa Continue → MISMO episodio continúa
  · Arcade Clear (Bison KO): terminated=True ← FIN
  · ep_step >= 30000: truncated=True ← FIN
```

### Vector de estado (30 dims)
```
[0-4]   HP, X, airborne, dir, stun de P1
[5-9]   HP, X, crouch, airborne, stun de P2
[10-17] distancias, timer, esquinas, diff_hp, daños recientes
[18-22] fk_phase, boom_activo, boom_x_est, proj_slot, boom_timer
[23-29] charge, rival_tierra_mucho, rival_hitstop, ventana_post_fk,
        ultima_accion, blanka_aterrizando, rolling_jump_rdy
```

### Acciones (26)
```
0-14   Single frame (NOOP, direcciones, botones, combinaciones down+botón)
15-17  Rolling Fierce / Strong / Jab (macros 73f)
18     Electricidad (macro 7f)
19-24  Saltos con ataque (macros 25f)
25     Rolling Jump (macro 1f, solo en landing window)
```

### Hiperparámetros

| Parámetro | CL-1 (train_FASE1.py) | Fase 2 (train_blanka_v1.py) |
|-----------|----------------------|------------------------------|
| n_envs | 6 | 1 |
| n_steps | 1365 | 4096 |
| batch_size | 128 | 128 |
| n_epochs | 4 | 4 |
| lr | 3e-4 | 1e-4 |
| ent_coef | 0.10 | 0.03 |
| gamma | 0.99 | 0.99 |
| total_steps | 1M | 5M |
| red arch | [256, 256] | [256, 256] |

---

## 🟡 PROBLEMAS CONOCIDOS Y LIMITACIONES

### Problema 1 — Flag global `ROLLING_AND_ELECTRIC_ONLY`
Es una constante a nivel de módulo. Si se olvida cambiar antes de lanzar `train_FASE1.py`, CL-1 corre en modo PPO completo sin aviso de error.
**Workaround temporal:** el script imprime un aviso. **Solución definitiva:** convertirlo en parámetro de `BlankaEnv.__init__`.

### Problema 2 — `ep_rounds_played` con continues
Cada continue que devuelve al combate incrementa el contador. El número refleja "intentos totales", no "rondas reales del arcade". Tenerlo en cuenta al interpretar `ep_round_win_rate` en TensorBoard.

### Problema 3 — `almost_ko` marca victoria prematura
El bloque `almost_ko = (combat_p2_dmg >= 130 and p2hp <= 30 and dp2 > 0)` marca `combat_won=True` antes del KO real. La guarda `if not self._combat_won` previene doble registro. El print de `¡KO!` puede aparecer con `p2hp=30` en lugar de 0. Cosmético, no funcional.

---

## 🔜 PRÓXIMOS PASOS INMEDIATOS

1. **Antes de lanzar CL-1**: poner `ROLLING_AND_ELECTRIC_ONLY = True` en `blanka_env.py`.
2. Ejecutar `python reset_training.py` para limpiar estado previo.
3. Lanzar `python train_FASE1.py --steps 1000000 --envs 6`.
4. Monitorizar `cl1/avg_p2_damage` y `cl1/win_rate` en TensorBoard.
5. Al superar `avg_p2_damage > 40`, guardar modelo CL-1 y decidir:
   - Pasar directamente a Fase 2 (train_blanka_v1.py con --resume).
   - Implementar CL-2 (defensa) si el agente sigue recibiendo mucho daño.

## 🔜 PRÓXIMOS PASOS MEDIOS

6. Implementar `CurriculumScheduler` que lea `RivalRegistry` y cambie flags automáticamente.
7. Convertir `ROLLING_AND_ELECTRIC_ONLY` en parámetro de `BlankaEnv.__init__`.
8. Considerar reducir `MAX_STEPS` en CL-1 (10000 en lugar de 30000) para episodios más cortos y updates más frecuentes.
9. CL-2: flag `DEFENSE_ONLY` — fuerza acciones 1 (UP), 3/4 (mover), 25 (rolling-jump).
10. CL-3: ofensiva combinada — acciones 15-25 activas, 0-14 solo NOOP y direcciones.

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
  TIMER_ADDR      = 0xFF8ACE  ⚠️ NO FIABLE — siempre 0 en combate.
  Timer fiable    = ceil((6400 - diag_combat_frame) / 60)

POSICION X
  P1_X_H_ADDR     = 0xFF917C  P1_X_L_ADDR = 0xFF917D  (big-endian)
  P2_X_H_ADDR     = 0xFF927C  P2_X_L_ADDR = 0xFF927D  (big-endian)
  Rango P2        = 0x02D8 (728) .. 0x0357 (855)

STUN
  P2_STUN_ADDR        = 0xFF865A  (+5/hit)
  P2_STUN_SPRITE_ADDR = 0xFF8951  (0x24 = pajaritos activos)
  P1_STUN_ADDR        = 0xFF895A  (+5/hit)

POSE / ANIMACION
  P2_CROUCH_FLAG_ADDR = 0xFF86C4  (0x03=agachado | 0x02=de pie)
  P2_ANIM_FRAME_ADDR  = 0xFF86C1

FLASH KICK — ANATOMIA DEFINITIVA
  Startup  0x0C  -288   f+0
  Ascenso  0x02  -2304  f+26
  Cima     0x00  -2304  f+123
  Descenso 0x04  +1760  f+126
  Landing  0x0C  ~0     f+150
  ⚠️ 0x0C ambiguo: abs(Y_VEL)>256 -> FK | ~0 -> Boom throw o Landing

VELOCIDAD VERTICAL (signed 16-bit; abs>256 = aire)
  P2: 0xFF86FC (H) / 0xFF86FD (L)
  P1: 0xFF83FC (H) / 0xFF83FD (L)

PROYECTIL SONIC BOOM
  PROJ_SLOT_FLAG  = 0xFF8E30  (0xA4 = slot activo)
  PROJ_IMPACT     = 0xFF8E00  (0x98 = impacto ~0.5s antes del daño)
  PROJ_X          = ❌ NO EXISTE (ENT_PROJ_A/B/C permanecen a cero)
  Workaround      = P2_X - frames_desde_throw × 25 ud/frame
  BOOM_VEL_APPROX = 25 ud/frame
```

---

**Meta final:** 100% winrate + perfects en todos los combates vs IA determinista.
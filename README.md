# 🕹️ Proyecto MAME RL – Control de Blanka (SF2CE)

## 🎯 OBJETIVO
Entrenamiento de un agente de Aprendizaje por Refuerzo para dominar a Blanka en *Street Fighter II': Champion Edition* con **100% winrate + perfects** vs Guile determinista.
```
[MAME]> print(emu.app_version())
0.286
```

## 🧱 ARQUITECTURA TÉCNICA
* Bridge bidireccional vía archivos (`mame_input.txt` + `state.txt`) — `mame_bridge.py`.
* `force_rolling_final.lua` ejecuta el Rolling Attack 100% fiable (bola + avance).
* `env/blanka_env.py` — entorno Gymnasium (Discrete 24 acciones, obs 30-dim).
* `lua/lua_bridge.lua` — escribe `state.txt` cada frame, lee `mame_input.txt`.
* `core/rival_registry.py` — registro persistente de estadísticas por rival.

---

## ✅ LOGROS ALCANZADOS (25/03/2026)
- [x] **Control Total de Blanka** (movimiento y ataques normales).
- [x] **Rolling Attack 100% fiable** (bola + avance real, release mínimo, sin desaparecer).
- [x] **Electricidad** (mashing) y macros de carga.
- [x] Fase 1 (Lógica de combate) cerrada al 100%.
- [x] Limpieza completa del repositorio.

## ✅ LOGROS ALCANZADOS (26/03/2026)
- [x] **Identificación de Personajes** (IDs 0-11 en 0xFF864F / 0xFF894F).
- [x] **Detección de Modo** ARCADE vs VS confirmada.
- [x] **Fase 2 (Extracción de Datos)** cerrada al 100%.

## ✅ LOGROS ALCANZADOS (27/03/2026)
- [x] Detección de Modo 100% fiable: bloque `0xFF87E0-0xFF87FF`.
- [x] `constants.py` y `direcciones_ram` actualizados.
- [x] `autostart_v32.lua` — diagnóstico definitivo en combate.

## ✅ LOGROS ALCANZADOS (28/03/2026 — sesión 1)
- [x] **P2_X** `0xFF927C-7D`, 16-bit big-endian. Rango: 0x02D8–0x0357.
- [x] **P2_CROUCH_FLAG** `0xFF86C4 = 0x03` (agachado).
- [x] **PROJ_SLOT_FLAG** `0xFF8E30 = 0xA4`, **PROJ_IMPACT** `0xFF8E00 = 0x98`.
- [x] **P2_STUN** `0xFF865A`, **P1_STUN** `0xFF895A`, **P2_STUN_SPRITE** `0xFF8951`.
- [x] **P2_Y_VEL** `0xFF86FC-FD` signed 16-bit.
- [x] `autostart_v33.lua` — narración P2 en tiempo real.
- [x] **Fase 3.1 completada** ✅

## ✅ LOGROS ALCANZADOS (28/03/2026 — sesión 2)
- [x] **P1_X** `0xFF917C-7D`, big-endian (simetría CPS1 confirmada).
- [x] **FK anatomía definitiva** (mapeo_guile_v4.lua): 5 fases, ~150 frames.
- [x] **Boom PROJ_X hipótesis descartada**: workaround `P2_X - t×25` validado.
- [x] **Fase 3.2 cerrada** ✅

## ✅ LOGROS ALCANZADOS (28/03/2026 — sesión 3)
- [x] **`mame_bridge.py` creado** — bridge de archivos Python↔MAME con API limpia.
- [x] **`env/blanka_env.py` v2.1** — claves de estado alineadas con lua_bridge v2.0.
- [x] **`core/rival_registry.py`** — registro persistente de estadísticas por rival.
- [x] **`train_blanka_v1.py`** — entrenamiento PPO single-env visible.

## ✅ LOGROS ALCANZADOS (29/03/2026)
- [x] **`autoplay_bridge.lua` v1.13** — timer fiable por frames internos (MAX_COMBAT_FRAMES=6400). Eliminada la lectura de RAM `0xFF8ACE` (siempre 0). `timer` en state.txt = estimado; `timer_raw` = diagnóstico.
- [x] **`env/blanka_env.py` v2.7** — recompensas Rolling elevadas para compensar crédito tardío de macro 69f. Bonus de carga acumulada (+0.05/step). Timer fiable.
- [x] **`autoplay_bridge.lua` v1.14** — `PRESS_CONTINUE` usa `"1 Player Start"` (3 pulsos). Fix crítico: JAB no registra en pantalla de continue de SF2CE.
- [x] **`env/blanka_env.py` v2.8** — flag `ROLLING_ONLY` para modo depuración (fuerza acción 15 en cada step). Funciona en ambos lados. Stats hit rate por episodio.
- [x] **`autoplay_bridge.lua` v1.15** — nuevo estado `CHAR_SELECT_CONTINUE`. Tras el continue SF2CE muestra char select con cursor en Blanka; el bridge pulsa JAB (2 pulsos) para confirmar de inmediato sin esperar el timeout de ~9s.

---

## 🚧 ESTADO ACTUAL
- **Fase 1** → Completada ✅
- **Fase 2** → Completada ✅
- **Fase 3.1** → Completada ✅
- **Fase 3.2** → Completada ✅
- **Fase 4** → En curso 🔄 — bridge operativo, entrenamiento PPO arrancado.

---

## 🚀 ARRANQUE RÁPIDO

### Requisitos
```bash
pip install gymnasium stable-baselines3 torch
```

### Pasos
1. Abre MAME con el Lua bridge:
```
mame64.exe sf2ce -autoboot_script lua\autoplay_bridge.lua
```
2. El bridge navega automáticamente: INSERT_COIN → PRESS_START → CHAR_NAVIGATE → CHAR_CONFIRM → IN_COMBAT.
3. Lanza el entrenamiento:
```bat
python train_blanka_v1.py
```

### Modo depuración Rolling (ROLLING_ONLY)
En `env/blanka_env.py`, línea ~45:
```python
ROLLING_ONLY: bool = True   # fuerza rolling en cada step
ROLLING_ONLY: bool = False  # entrenamiento PPO normal
```

### Ver estadísticas por rival
```bat
python train_blanka_v1.py --stats
```

---

## 📁 Estructura actual
```
C:\proyectos\MAME\
├── mame_bridge.py
├── train_blanka_v1.py
├── config\
│   └── constants.py
├── core\
│   └── rival_registry.py
├── env\
│   └── blanka_env.py          ← v2.8 (ROLLING_ONLY flag)
├── lua\
│   ├── autoplay_bridge.lua    ← v1.15 (CHAR_SELECT_CONTINUE)
│   ├── lua_bridge.lua
│   └── autostart_v33.lua
├── force_rolling_final.lua
├── train.py
├── direcciones_ram
├── README.md
├── RECONSTRUCCIÓN DEL ENTORNO.md
└── legacy\
    └── tests\
```

---

## 🔄 FLUJO COMPLETO DE LA MÁQUINA DE ESTADOS (autoplay_bridge v1.15)

```
BOOTING
  └─> DISMISS_WARNING        (360f boot)
        └─> INSERT_COIN      (pulsa Coin 1)
              └─> PRESS_START (pulsa 1P Start)
                    └─> CHAR_NAVIGATE   (2× RIGHT para Blanka)
                          └─> CHAR_CONFIRM  (JAB)
                                └─> WAITING_COMBAT
                                      └─> IN_COMBAT ──── victoria/derrota/timeout
                                                              └─> ROUND_OVER_WAIT
                                                                    ├─ HP restaurados → WAITING_COMBAT (siguiente ronda)
                                                                    └─ timeout (360f)  → GAME_OVER_WAIT
                                                                                              └─> PRESS_CONTINUE (3× Start)
                                                                                                    └─> CHAR_SELECT_CONTINUE (JAB ×2)
                                                                                                          └─> WAITING_COMBAT
```

---

## 🎮 ESPACIO DE ACCIONES (blanka_env.py v2.8)

| ID | Acción | Tipo | Frames |
|----|--------|------|--------|
| 0 | NOOP | single | 1 |
| 1 | UP | single | 1 |
| 2 | DOWN | single | 1 |
| 3 | LEFT | single | 1 |
| 4 | RIGHT | single | 1 |
| 5 | JAB | single | 1 |
| 6 | STRONG | single | 1 |
| 7 | FIERCE | single | 1 |
| 8 | SHORT | single | 1 |
| 9 | FORWARD | single | 1 |
| 10 | ROUNDHOUSE | single | 1 |
| 11 | DOWN+JAB | single | 1 |
| 12 | DOWN+FIERCE | single | 1 |
| 13 | DOWN+SHORT | single | 1 |
| 14 | DOWN+RH | single | 1 |
| 15 | **ROLLING ATTACK** | macro | 69 |
| 16 | ELECTRICIDAD | macro | 5 |
| 17 | SALTO FWD + FIERCE | macro | 25 |
| 18 | SALTO FWD + FORWARD | macro | 25 |
| 19 | SALTO FWD + RH | macro | 25 |
| 20 | SALTO NEUTRO + FIERCE | macro | 25 |
| 21 | SALTO ATRÁS + FIERCE | macro | 25 |
| 22 | SALTO ATRÁS + FORWARD | macro | 25 |
| 23 | ROLLING JUMP (ventana aterrizaje) | macro | 1 |

Las acciones con flip (15, 17-19, 21-23) invierten LEFT↔RIGHT automáticamente según `p1_dir`.

---

## 📡 CÓMO SE DETECTA P1, P2 Y MODO

### P1 — Personaje del Jugador 1
**Dirección:** `0xFF864F` — byte directo, ID 0-11. Fiabilidad 100%.

### P2 — Personaje del Jugador 2
**Dirección:** `0xFF894F` — mismo mapa que P1. `0xFF874F` descartada (siempre 0x00).

### MODO — ARCADE vs VS
**Bloque:** `0xFF87E0–0xFF87FF` (32 bytes).
- `any(byte != 0)` → ARCADE
- `all(byte == 0)` → VS

Fiable a partir del 2º dump (~3s). `autostart_v33.lua` lo maneja automáticamente.

---

## 🗺️ MAPA DE PERSONAJES (SF2CE)

| ID | Personaje | ID | Personaje |
|----|-----------|----|-----------|
| 0  | Ryu       | 6  | Zangief   |
| 1  | E.Honda   | 7  | Dhalsim   |
| 2  | Blanka    | 8  | M.Bison   |
| 3  | Guile     | 9  | Sagat     |
| 4  | Ken       | 10 | Balrog    |
| 5  | Chun-Li   | 11 | Vega      |

---

## 📋 DIRECCIONES RAM CONFIRMADAS (ORO PURO — NO MODIFICAR)
```
VIDA
  P1_HP_ADDR      = 0xFF83E9
  P2_HP_ADDR      = 0xFF86E9
  P2_HP_DISPLAY2  = 0xFF86EB  (lag 1-2f)

LADO
  P1_SIDE_ADDR    = 0xFF83D0
  P2_SIDE_ADDR    = 0xFF86D0

PERSONAJES
  P1_CHAR_ADDR    = 0xFF864F  (ID directo 0-11)
  P2_CHAR_ADDR    = 0xFF894F  (ID directo 0-11)

MODO (ARCADE vs VS)
  MODO_BLOCK_START = 0xFF87E0  <- 32 bytes hasta 0xFF87FF
  Logica: any(byte != 0) -> ARCADE | all(byte == 0) -> VS

CRONOMETRO
  TIMER_ADDR      = 0xFF8ACE  (NO FIABLE — siempre devuelve 0)
  Usar timer estimado del bridge: MAX_COMBAT_FRAMES=6400 / 60fps

POSICION X (16-bit big-endian)
  P1_X_H_ADDR     = 0xFF917C  P1_X_L_ADDR = 0xFF917D
  P2_X_H_ADDR     = 0xFF927C  P2_X_L_ADDR = 0xFF927D
  Rango P2: 0x02D8 (728) - 0x0357 (855) | Mayor = mas a la derecha

STUN
  P2_STUN_ADDR        = 0xFF865A  (+5/hit)
  P2_STUN_SPRITE_ADDR = 0xFF8951  (0x24 = pajaritos activos)
  P1_STUN_ADDR        = 0xFF895A

POSE / ANIMACION
  P2_CROUCH_FLAG_ADDR = 0xFF86C4  (0x03=agachado | 0x02=de pie)
  P2_ANIM_FRAME_ADDR  = 0xFF86C1

FLASH KICK — ANATOMIA DEFINITIVA (mapeo_guile_v4 — 28/03/2026)
  Fase        ANIM   Y_VEL   Frame
  Startup     0x0C   -288    f+0
  Ascenso     0x02   -2304   f+26
  Cima        0x00   -2304   f+123
  Descenso    0x04   +1760   f+126
  Landing     0x0C   ~0      f+150
  ⚠️ 0x0C ambiguo: abs(Y_VEL)>256 -> FK | ~0 -> Boom throw o Landing

AIRBORNE
  P2_Y_VEL_H = 0xFF86FC  P2_Y_VEL_L = 0xFF86FD  (signed 16-bit; abs>256 = aire)

PROYECTIL SONIC BOOM
  PROJ_SLOT_FLAG_ADDR = 0xFF8E30  (0xA4 = slot activo)
  PROJ_IMPACT_ADDR    = 0xFF8E00  (0x98 = impacto ~0.5s antes del daño)
  PROJ_X_ADDR         = ❌ NO EXISTE (ENT_PROJ permanece a cero)
  Workaround          = P2_X - frames_desde_throw × 25 ud/frame
```

---

## 🔜 PRÓXIMO PASO
- Arrancar entrenamiento PPO extendido vs Guile determinista.
- Validar `CHAR_SELECT_CONTINUE` con log en el siguiente game over.

**Meta final:** 100% winrate + perfects en todos los combates vs Guile determinista.
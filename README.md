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
* `env/blanka_env.py` — entorno Gymnasium (Discrete 17 acciones, obs 28-dim).
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
- [x] **`env/blanka_env.py` v2.1** — claves de estado alineadas con lua_bridge v2.0:
  - `p2_char_id` → `p2_char`
  - `proj_active` → `boom_slot_active`
  - `p2_action` → `p2_anim`
  - `p2_crouch` ahora real (antes fijo a 0.0)
- [x] **`core/rival_registry.py`** — registro persistente de estadísticas por rival.
- [x] **`train_blanka_v1.py`** — entrenamiento PPO single-env visible.

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
mame64.exe sf2ce -autoboot_script lua\lua_bridge.lua
```
2. Navega manualmente a un combate (o espera a que `autoplay_bridge.lua` lo haga automáticamente — pendiente).
3. Lanza el entrenamiento:
```bat
python train_blanka_v1.py
```

### Ver estadísticas por rival
```bat
python train_blanka_v1.py --stats
```

---

## 📁 Estructura actual
```
C:\proyectos\MAME\
├── mame_bridge.py          ← bridge Python↔MAME (archivos)
├── train_blanka_v1.py      ← entrenamiento PPO single-env
├── config\
│   └── constants.py
├── core\
│   └── rival_registry.py
├── env\
│   └── blanka_env.py
├── lua\
│   ├── lua_bridge.lua      ← bridge Lua activo (v2.0)
│   └── autostart_v33.lua   ← diagnóstico P1/P2/MODO
├── force_rolling_final.lua
├── train.py
├── direcciones_ram
├── README.md
├── RECONSTRUCCIÓN DEL ENTORNO.md
└── legacy\
    └── tests\
```

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
  MODO_BLOCK_START = 0xFF87E0  ← 32 bytes hasta 0xFF87FF
  Lógica: any(byte != 0) → ARCADE | all(byte == 0) → VS

CRONÓMETRO
  TIMER_ADDR      = 0xFF8ACE

POSICIÓN X (16-bit big-endian)
  P1_X_H_ADDR     = 0xFF917C  P1_X_L_ADDR = 0xFF917D
  P2_X_H_ADDR     = 0xFF927C  P2_X_L_ADDR = 0xFF927D
  Rango P2: 0x02D8 (728) – 0x0357 (855) | Mayor = más a la derecha

STUN
  P2_STUN_ADDR        = 0xFF865A  (+5/hit)
  P2_STUN_SPRITE_ADDR = 0xFF8951  (0x24 = pajaritos activos)
  P1_STUN_ADDR        = 0xFF895A

POSE / ANIMACIÓN
  P2_CROUCH_FLAG_ADDR = 0xFF86C4  (0x03=agachado | 0x02=de pie)
  P2_ANIM_FRAME_ADDR  = 0xFF86C1

FLASH KICK — ANATOMÍA DEFINITIVA (mapeo_guile_v4 — 28/03/2026)
  Fase        ANIM   Y_VEL   Frame
  Startup     0x0C   −288    f+0
  Ascenso     0x02   −2304   f+26
  Cima        0x00   −2304   f+123
  Descenso    0x04   +1760   f+126
  Landing     0x0C   ~0      f+150
  ⚠️ 0x0C ambiguo: abs(Y_VEL)>256 → FK | ~0 → Boom throw o Landing

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
- Crear `lua/autoplay_bridge.lua` con máquina de estados para navegación automática de menús (INSERT_COIN → CHAR_SELECT → COMBAT → CONTINUE).
- Arrancar entrenamiento PPO extendido vs Guile determinista.

**Meta final:** 100% winrate + perfects en todos los combates vs Guile determinista.
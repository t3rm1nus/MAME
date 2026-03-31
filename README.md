# 🕹️ Proyecto MAME RL – Control de Blanka (SF2CE)

## 🎯 OBJETIVO
Entrenamiento de un agente de Aprendizaje por Refuerzo para dominar a Blanka en *Street Fighter II': Champion Edition* con **100% winrate + perfects** vs la IA determinista.
```
[MAME]> print(emu.app_version())
0.286
```

## 🧱 ARQUITECTURA TÉCNICA
* Bridge bidireccional vía archivos (`mame_input_N.txt` + `state_N.txt`) — `mame_bridge.py` v1.3.
* `autoplay_bridge.lua` **v2.3** — FSM event-driven (BOOTING → IN_COMBAT → GAME_OVER → loop). Anti-bucle WAIT_COMBAT con `wait_timeout_count`.
* `env/blanka_env.py` **v4.4** — entorno Gymnasium (Discrete **26** acciones, obs 30-dim). Episodio = run arcade completa. Termina solo en Arcade Clear o MAX_STEPS.
* `mame_bridge.py` **v1.3** — polling activo, paths con sufijo `_N` en `dinamicos/`.
* `core/rival_registry.py` **v2.0** — registro persistente JSON por rival.
* `train_blanka_v1.py` **v2.1** — PPO Fase 2 (1 env visible, arcade completo, sin Game Over).
* `train_FASE1.py` **v2.1** — CL-1 (6 envs headless, Rolling + Electric only).

---

## ✅ LOGROS ALCANZADOS (25/03/2026)
- [x] **Control Total de Blanka** (movimiento y ataques normales).
- [x] **Rolling Attack 100% fiable** (bola + avance real).
- [x] **Electricidad** (mashing) y macros de carga.
- [x] Fase 1 (Lógica de combate) cerrada al 100%.

## ✅ LOGROS ALCANZADOS (26/03/2026)
- [x] **Identificación de Personajes** (IDs 0-11 en 0xFF864F / 0xFF894F).
- [x] **Detección de Modo** ARCADE vs VS confirmada.

## ✅ LOGROS ALCANZADOS (27/03/2026)
- [x] Detección de Modo 100% fiable: bloque `0xFF87E0-0xFF87FF`.
- [x] `autostart_v33.lua` — diagnóstico definitivo en combate.

## ✅ LOGROS ALCANZADOS (28/03/2026)
- [x] P2_X, P1_X, P2_CROUCH_FLAG, P2_STUN, P2_Y_VEL, PROJ_SLOT/IMPACT confirmados.
- [x] FK anatomía definitiva (mapeo_guile_v4): 5 fases, ~150 frames.
- [x] Boom PROJ_X hipótesis descartada: workaround P2_X − t×25 validado.
- [x] `mame_bridge.py`, `blanka_env.py`, `rival_registry.py`, `train_blanka_v1.py` creados.
- [x] **Fases 3.1, 3.2, 3.3 completadas** ✅

## ✅ LOGROS ALCANZADOS (30/03/2026)
- [x] **`autoplay_bridge.lua` v2.3** — FSM event-driven reescrita. Fix crítico anti-bucle WAIT_COMBAT (`wait_timeout_count`, máx 3 timeouts consecutivos → INSERT_COIN). Sin frame-counts como lógica principal.
- [x] **`env/blanka_env.py` v4.4** — episodio NO termina por Game Over. Solo termina en: (a) Arcade Clear `terminated=True`, (b) MAX_STEPS `truncated=True`. Fix `ep_rounds_played` (init=1, incremento real). Eliminado MENU_FRAMES_MAX. Tracking completo de rondas, matches, bosses, arcade clear.
- [x] **`train_blanka_v1.py` v2.1** — Fase 2 arcade completo. Métricas bosses + arcade_clears TensorBoard.
- [x] **`train_FASE1.py` v2.1** — CL-1 con 6 instancias headless. Tracking granular de bosses + arcade clears.
- [x] **`mame_bridge.py` v1.3** — paths en `dinamicos/` con sufijo `_N`.
- [x] **`reset_training.py`** — limpieza total para nuevo entrenamiento.
- [x] **Fase 3.4 completada** ✅

---

## 🚧 ESTADO ACTUAL
- **Fase 1** → Completada ✅  
- **Fase 2** → Completada ✅  
- **Fase 3.1** → Completada ✅  
- **Fase 3.2** → Completada ✅  
- **Fase 3.3** → Completada ✅  
- **Fase 3.4** → Completada ✅  
- **Fase 4 CL-1** → Listo para lanzar 🔄  
- **Fase 4 PPO Full** → Operativo, pendiente modelo CL-1 🔄  

---

## 🚀 ARRANQUE RÁPIDO

### Requisitos
```bash
pip install gymnasium stable-baselines3 torch
```

### Opción A — CL-1 (6 instancias headless, recomendado para empezar)
```bash
# 1. En env/blanka_env.py línea ~17, poner:
#    ROLLING_AND_ELECTRIC_ONLY: bool = True

# 2. Limpiar estado anterior
python reset_training.py

# 3. Lanzar
python train_FASE1.py --steps 1000000 --envs 6

# Con instancia 0 visible (debug):
python train_FASE1.py --steps 1000000 --envs 6 --visible
```

### Opción B — PPO completo Fase 2 (1 env visible)
```bash
# Asegurarse de que ROLLING_AND_ELECTRIC_ONLY = False en blanka_env.py
python train_blanka_v1.py

# Reanudar desde checkpoint:
python train_blanka_v1.py --resume models/blanka/fase2/fase2_999912_steps
```

### Ver estadísticas por rival
```bat
python train_blanka_v1.py --stats
python train_FASE1.py --stats
```

---

## 📁 Estructura actual
```
C:\proyectos\MAME\
├── mame_bridge.py              ← v1.3
├── train_blanka_v1.py          ← v2.1 (Fase 2, 1 env, arcade completo)
├── train_FASE1.py              ← v2.1 (CL-1, 6 envs headless)
├── reset_training.py           ← limpieza total para reset
├── config\
│   └── constants.py
├── core\
│   └── rival_registry.py      ← v2.0
├── env\
│   └── blanka_env.py          ← v4.4
├── lua\
│   ├── autoplay_bridge.lua    ← v2.3 (FSM event-driven, anti-bucle)
│   ├── lua_bridge.lua
│   └── autostart_v33.lua
├── dinamicos\                  ← archivos de sesión runtime (mame_input_N, state_N)
├── models\blanka\fase1\       ← checkpoints CL-1
├── models\blanka\fase2\       ← checkpoints Fase 2
├── logs\blanka\fase1\         ← TensorBoard CL-1
├── logs\blanka\fase2\         ← TensorBoard Fase 2
├── rival_stats.json
├── force_rolling_final.lua
├── README.md
├── RECONSTRUCCIÓN_DEL_ENTORNO.md
└── legacy\tests\
```

---

## 🔄 FLUJO COMPLETO FSM (autoplay_bridge v2.3)

FSM **event-driven**: transiciones basadas en RAM. Timeouts = safety-nets.
```
BOOTING
  └─> INSERT_COIN         (hardware listo: _mem_ok y _fields_ok)
        └─> PRESS_START   (1P Start cada 20f, hasta 120f total)
              └─> CHAR_NAVIGATE   (2× RIGHT hasta Blanka)
                          (si is_continue=true → salta a CHAR_CONFIRM)
                    └─> CHAR_CONFIRM    (JAB en f=10, release en f=20)
                          └─> WAIT_COMBAT
                                │  Espera ambos HP >= 100 estables 8f
                                │  timeout 1200f:
                                │    is_continue y count < 3 → CHAR_CONFIRM
                                │    count >= 3 ó !is_continue → INSERT_COIN
                                │    (reset completo — resuelve vuelta a título)
                                └─> IN_COMBAT (wait_timeout_count = 0)
                                      ├─ P1 ó P2 HP <= 10 → ROUND_OVER
                                      │     ├─ ambos HP suben → IN_COMBAT
                                      │     └─ HP sin recuperar 360f → GAME_OVER
                                      │           └─> INSERT_COIN + 3× START
                                      │                 └─> CHAR_CONFIRM (is_continue=true)
                                      │                       └─> WAIT_COMBAT → IN_COMBAT
                                      └─ diag_combat_frame >= 6400 → ROUND_OVER
```

---

## 🎮 ESPACIO DE ACCIONES (blanka_env.py v4.4) — 26 acciones

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
| 15 | **ROLLING FIERCE** | macro | 73 (68 back + 1 NOOP + 4 fwd+fierce) |
| 16 | **ROLLING STRONG** | macro | 73 |
| 17 | **ROLLING JAB** | macro | 73 |
| 18 | **ELECTRICIDAD** | macro | 7 |
| 19 | SALTO FWD + FIERCE | macro | 25 (4 up + 18 air + 3 atk) |
| 20 | SALTO FWD + FORWARD | macro | 25 |
| 21 | SALTO FWD + RH | macro | 25 |
| 22 | SALTO NEUTRO + FIERCE | macro | 25 |
| 23 | SALTO ATRÁS + FIERCE | macro | 25 |
| 24 | SALTO ATRÁS + FORWARD | macro | 25 |
| 25 | **ROLLING JUMP** | macro | 1 (solo si _rolling_jump_rdy=True) |

`ROLLING_ACTIONS = {15, 16, 17}` | `FLIP_ACTIONS = {15, 16, 17, 19, 20, 21, 23, 24, 25}`

---

## 🧠 VECTOR DE ESTADO (30 dimensiones, clip [-1, 1])
```
[0]  p1_hp / 144          HP Blanka normalizado
[1]  p1_x / 1400          X Blanka
[2]  p1_airborne          Blanka en aire
[3]  p1_dir               dirección (1=der, 0=izq)
[4]  p1_stun / 200        stun de Blanka
[5]  p2_hp / 144          HP rival
[6]  p2_x / 1400          X rival
[7]  p2_crouch            rival agachado
[8]  p2_airborne          rival en aire
[9]  p2_stun / 200        stun del rival
[10] dist / 1400          distancia absoluta
[11] (p1x-p2x) / 1400    distancia con signo
[12] timer / 99           tiempo restante
[13] p1_en_esquina        Blanka en esquina (x<150 ó x>1250)
[14] p2_en_esquina        rival en esquina
[15] (p1hp-p2hp) / 144   diferencia de vida
[16] dmg_rec_reciente     daño recibido reciente
[17] dmg_inf_reciente     daño infligido reciente
[18] fk_phase_value       fase del FK (0.0=suelo, 0.2-0.8=fases FK)
[19] boom_activo_est.     sonic boom estimado en vuelo
[20] boom_x_est / 1400   X estimada del boom
[21] proj_slot_active     slot proyectil activo (0xFF8E30=0xA4)
[22] boom_timer / 51      timer del boom (51=BOOM_FLIGHT_STEPS)
[23] charge / 68          carga acumulada para rolling
[24] rival_tierra_mucho   rival lleva >30f en tierra
[25] rival_hitstop        rival en hitstop
[26] ventana_post_fk      0 < fk_land_steps <= 20
[27] ultima_accion / 25   última acción ejecutada
[28] blanka_aterrizando   p1_land_steps en landing window
[29] rolling_jump_rdy     Rolling Jump disponible
```

---

## 💰 RECOMPENSAS CLAVE

| Evento | Reward |
|--------|--------|
| Daño infligido a P2 | +8 × dmg |
| Daño recibido | −12 × dmg |
| KO a P2 (p2hp=0, dp2>0) | +60 |
| P2 < 20HP con daño | +35 |
| P2 < 40HP con daño | +18 |
| P2 < 70HP con daño | +8 |
| Rolling hit + ventana FK | +38 |
| Rolling hit + dist óptima (180–650) | +32 |
| Rolling hit + dist subóptima | +22 |
| Rolling no hit (fuera rango) | −1.5 |
| Rolling no hit (rango ok) | −0.8 |
| Electric hit + dist < 150 | +14 |
| Electric hit + dist ≥ 150 | +7 |
| Electric miss lejos | −3.0 |
| Electric miss cerca | −1.2 |
| Mantener carga back | +0.06/step |
| P2 en esquina | +1.2 |
| Rolling Jump hit en landing window | +28 |
| Rolling Jump en ventana sin hit | +7 |
| Rolling Jump fuera de ventana | −4 |
| Salto+ataque hit | +7 (+5 extra si dist 140–520) |
| Salto+ataque recibido (p1 en aire) | −4 |
| Inactividad >60 steps | −0.003 |
| **Arcade Clear (Bison KO)** | **+200** |

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
  MODO_BLOCK_START = 0xFF87E0  (32 bytes hasta 0xFF87FF)
  Logica: any(byte != 0) -> ARCADE | all(byte == 0) -> VS

CRONOMETRO
  TIMER_ADDR      = 0xFF8ACE  ⚠️ NO FIABLE (siempre 0)
  Timer estimado  = ceil((6400 - diag_combat_frame) / 60)

POSICION X (16-bit big-endian)
  P1_X_H=0xFF917C  P1_X_L=0xFF917D
  P2_X_H=0xFF927C  P2_X_L=0xFF927D
  Rango P2: 728–855 | Mayor = más a la derecha

STUN
  P2_STUN_ADDR        = 0xFF865A  (+5/hit)
  P2_STUN_SPRITE_ADDR = 0xFF8951  (0x24 = pajaritos)
  P1_STUN_ADDR        = 0xFF895A

POSE / ANIMACION
  P2_CROUCH_FLAG_ADDR = 0xFF86C4  (0x03=agachado | 0x02=de pie)
  P2_ANIM_FRAME_ADDR  = 0xFF86C1

VELOCIDAD VERTICAL (signed 16-bit)
  P2: 0xFF86FC (H) / 0xFF86FD (L) — abs>256 = aire
  P1: 0xFF83FC (H) / 0xFF83FD (L)

FLASH KICK — ANATOMIA DEFINITIVA
  Startup  0x0C  −288   f+0
  Ascenso  0x02  −2304  f+26
  Cima     0x00  −2304  f+123
  Descenso 0x04  +1760  f+126
  Landing  0x0C  ~0     f+150
  ⚠️ 0x0C ambiguo: abs(Y_VEL)>256 → FK | ~0 → Boom throw o Landing

PROYECTIL SONIC BOOM
  PROJ_SLOT_FLAG = 0xFF8E30  (0xA4 = activo)
  PROJ_IMPACT    = 0xFF8E00  (0x98 = impacto ~0.5s antes)
  PROJ_X         = ❌ NO EXISTE en RAM
  Workaround     = P2_X − frames_desde_throw × 25 ud/frame
```

---

## 🚀 CURRICULUM LEARNING — ESTADO

| Fase | Script | Estado |
|------|--------|--------|
| CL-1: Rolling + Electric (6 envs) | `train_FASE1.py` | ✅ Implementada |
| CL-2: Defensa Boom + FK | — | 📋 Planificada |
| CL-3: Ofensiva combinada | — | 📋 Planificada |
| CL-4 / Fase 2: PPO completo | `train_blanka_v1.py` | ✅ Implementada |

⚠️ `CurriculumScheduler` automático pendiente de implementar.

**Meta final:** 100% winrate + perfects en todos los combates vs IA determinista.
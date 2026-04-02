---

### 2. Actualización para `RECONSTRUCCIÓN DEL ENTORNO.md`

Reemplaza el contenido actual con esta versión técnica profunda:

```markdown
# 🧠 ROADMAP Y ARQUITECTURA TÉCNICA (Abril 2026)

## 🎯 META DE DESARROLLO
Establecer un ecosistema de RL donde múltiples instancias de MAME funcionen 24/7 sin colapsos de sincronización, maximizando la velocidad de recolección de experiencia para PPO con Blanka.

## 🟢 HITOS COMPLETADOS HASTA LA v5.10

- **Fase 1 (Control Base)**: Macros de Rolling y Electricidad validados en MAME.
- **Fase 2 (Contexto RAM)**: Extracción de Timer, Personajes y modo (ARCADE/VS).
- **Fase 3 (Estado Dinámico & Proyectiles)**:
  - Detección anatómica del Flash Kick (Startup=0x0C, Y_VEL abs > 256).
  - Workaround para Sonic Boom (PROJ_X es dinámico en CPS1, por lo que se calcula vía `P2_X - frames × BOOM_VEL`).
- **Fase 4 (Multi-Env y Estabilidad v5.10)**:
  - **Arquitectura Claim-Protocol**: Python asigna un `instance_id` vía archivo `claim.txt`, el Lua lo consume y se auto-configura para leer/escribir en sufijos `_N` dentro de la carpeta `dinamicos/`.
  - **Bug Fixes Críticos (v5.8 a v5.10)**: 
    - Fix del "Cerrojo Maestro" (`game_state=8` eliminado, usando flags `in_combat`).
    - Detección correcta de *Arcade Clears* en estados fuera de combate.
    - Sincronización del "Rolling bonus" expandiendo la ventana a `_ROLLING_HIT_WINDOW = 80`.
  - **Reward Shaping Avanzado**: Castigos exponenciales a rolling_spam (umbral 1800), premios por remate cuando el HP de P2 cae bajo 50%, 25% y 10%. 

## 🔴 ESTADO DEL ENTRENAMIENTO ACTUAL

Estamos corriendo en esquema de dos fases, usando `stable-baselines3`:

1. **FASE 1 (Curriculum Learning 1)** -> `train_FASE1.py`
   - Configurado con 6 instancias MAME Headless en paralelo (`SubprocVecEnv`).
   - Usa un Action Space discreto de 7 (`NOOP, Left, Right, Rolling×3, Electric`).
   - Se ajustó el hiperparámetro PPO anti-colapso: `ent_coef=0.05` y `n_steps=2048` para forzar a la IA a descubrir y explotar el Rolling en etapas tempranas.
2. **FASE 2 (Arcade Completo)** -> `train_FASE2.py`
   - Action space completo (26 acciones).
   - Inicia cargando los pesos parciales de la Fase 1 (`mlp_extractor` y capas base compartidas), descartando las cabezas `action_net` y `value_net` incompatibles debido a las distintas dimensiones de acciones.
   - PPO ajustado a `clip_range=0.20` y `ent_coef=0.06` para salir de estancamientos si la IA abusa de la defensa o el castigo a recibir daño (-12.0) la hace demasiado pasiva.

## 📋 DIRECCIONES RAM CRÍTICAS (Core Logic)

**Salud (HP) y Flancos**
* `P1_HP = 0xFF83E9` | `P2_HP = 0xFF86E9` (Valor MAX = 144)
* Las transiciones `ROUND_OVER` se detectan cuando cualquier HP cae a 0, pasando por una ventana de estabilización (`HP_STABLE_NEED=8`) para evitar ruidos de frame.

**Geometría y Estado Dinámico (P2)**
* `X` (16-bit Big Endian): `P1_X = 0xFF917C-7D` | `P2_X = 0xFF927C-7D`
* Airborne Flag: Evaluado leyendo `P2_Y_VEL_H/L (0xFF86FC-7D)` como 16-bit con signo. Absoluto > 256 = Airborne.
* `P2_CROUCH = 0xFF86C4` (Valor canónico: `0x03`).
* `P2_STUN_SPRITE = 0xFF8951` (Valor `0x24` = pajaritos/estrellas).

**Proyectiles (Sonic Boom)**
* `PROJ_IMPACT = 0xFF8E00` (El valor `0x98` ocurre exactamente ~0.5s antes del impacto, lo que da ventana para que la IA genere el salto evasivo).

---
**Notas Operativas para Desarrolladores:**
NUNCA forzar cierres de proceso sin pasar por el flujo de limpieza. Los logs de TensorFlow se almacenan en `logs/blanka/faseX` y pueden seguirse en vivo mediante `tensorboard --logdir logs/blanka/fase2`.
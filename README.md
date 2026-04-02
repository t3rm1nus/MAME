# 🕹️ Proyecto MAME RL – Blanka 100% Winrate (SF2CE / MAME 0.286)

## 🎯 OBJETIVO FINAL
Entrenar un agente de IA mediante PPO (Reinforcement Learning) que derrote a la máquina de Street Fighter II': Champion Edition con **100% winrate + perfects** en todos los combates contra la IA determinista.  
**Enfoque actual**: Blanka.

## ✅ ESTADO ACTUAL (v5.10 - Abril 2026)

El framework soporta **entrenamiento multi-instancia concurrente** y transiciones fluidas de Curriculum Learning.

- **Lua bridge (`lua/autoplay_bridge.lua` v2.3)**: FSM event-driven. Navega los menús de inicio, selección de personaje (Blanka) y bucles de Game Over/Continue de forma 100% autónoma.
- **Entorno Gymnasium (`env/blanka_env.py` v5.10)**:
  - **Recompensas moldeadas (Reward Shaping)**: Sistema progresivo de HP, bonus por esquives aéreos (+2.5), y penalización severa por daño recibido (-12.0) para incentivar la defensa.
  - **Mecanismos Anti-Estancamiento**: Lógica para castigar el "rolling spam" compulsivo sin lograr KOs.
  - **Tracking Perfecto**: Detección robusta de Game Overs reales y Arcade Clears mediante flancos de HP y FSM, eliminando bugs de truncamiento prematuro.
- **Entrenamiento (Pipeline)**:
  - **CL-1 (`train_FASE1.py` v3.2)**: 6 instancias *headless* en paralelo. Espacio de acciones reducido a 7 (Rolling y Electricidad). Ajuste de hiperparámetros (ent_coef=0.05) para romper el colapso de entropía.
  - **Fase 2 (`train_FASE2.py`)**: Entorno completo con 26 acciones. Carga de pesos parcial (compartiendo `mlp_extractor`) desde la Fase 1.
- **Bridge Python↔MAME (`mame_bridge.py` v1.3)**: Polling activo, arquitectura optimizada en el directorio `dinamicos/` aislando inputs y states por `instance_id`.

## 🚀 CÓMO ARRANCAR

```bash
# 1. Limpieza de memoria temporal antes de empezar
python limpia.py

# 2. Curriculum Learning Fase 1 (6 instancias headless por defecto)
python train_FASE1.py --steps 5000000

# 3. Entrenamiento completo Fase 2 (carga parcial de Fase 1)
# Puede configurarse --envs N para lanzar múltiples instancias
python train_FASE2.py --resume models/blanka/fase1/fase1_final

# 4. Ver estadísticas de rivales en cualquier momento:
python train_FASE2.py --stats

📂 ESTRUCTURA DEL PROYECTO REAL
Plaintext
MAME/
├── config/              # constants.py (Direcciones RAM, offsets y settings globales)
├── core/                # Lógica central, rival_registry.py
├── dinamicos/           # Archivos temporales de I/O en tiempo real (mame_input_N, state_N)
├── EMULADOR/            # Binarios de MAME 0.286 y ROMs
├── env/                 # Entorno Gymnasium (blanka_env.py, reward.py, action_space.py)
├── logs/                # TensorBoard logs para PPO
├── lua/                 # Scripts MAME (autoplay_bridge.lua)
├── models/              # Checkpoints (.zip) y VecNormalize (.pkl) de fase1/fase2
└── scripts base         # train_FASE1.py, train_FASE2.py, mame_bridge.py, limpia.py
🎮 ESPACIO DE ACCIONES (26 - Fase 2)
0-14: Single frame (NOOP, direcciones, botones, combinaciones down+botón)

15-17: Rolling (Fierce, Strong, Jab - Macros completas de 73 frames)

18: Electricidad

19-24: Saltos con ataques específicos

25: Rolling Jump (Solo disponible en ventana de aterrizaje LANDING_WINDOW)
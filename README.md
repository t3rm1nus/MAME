# 🕹️ Proyecto MAME RL – Blanka 100% Winrate (SF2CE)

---

## 🎯 OBJETIVO FINAL

Entrenar un agente mediante **PPO (Reinforcement Learning)** que derrote a la máquina de *Street Fighter II': Champion Edition* con **100% winrate** en modo Arcade.

El sistema utiliza una **Fase Única** con **26 acciones disponibles desde el inicio**.

---

## 📂 GUÍA DE ARCHIVOS Y CARPETAS (Índice Técnico)

El proyecto está modularizado para separar:

* Memoria de MAME
* Motor de entrenamiento
* Comunicación


MAME/
├── config/              # constants.py (Direcciones RAM, offsets y variables estáticas)
├── core/                # rival_registry.py (Sistema de guardado de estadísticas)
├── lua/                 # autoplay_bridge.lua (Inyectado en MAME)
├── EMULADOR/            # Binarios de MAME 0.286 y roms/
│
├── dinamicos/           # [GENERADO AUTOMÁTICAMENTE] - Archivos volátiles
│   ├── bridge_version_N.txt      # Confirma que el Lua está listo
│   ├── instance_id_claim.txt     # Sistema de asignación de IDs para multiproceso
│   ├── mame_input_N.txt          # Python escribe las acciones aquí (CSV: 0,1,0...)
│   ├── mame_stdout_N.txt         # Logs internos del emulador
│   └── state_N.txt               # Lua escribe el estado de la RAM (JSON)
│
├── logs/blanka/unica/   # [GENERADO AUTOMÁTICAMENTE] - Métricas
│   └── events.out.tfevents...    # Archivos de TensorBoard (WinRate, daño, etc.)
│
├── models/blanka/unica/ # [GENERADO AUTOMÁTICAMENTE] - Pesos Neuronales
│   ├── unica_75000_steps.zip     # Checkpoints del modelo PPO (guardado cada 75k steps)
│   ├── unica_final.zip           # Modelo final al terminar / interrumpir (Ctrl+C)
│   └── vecnorm_unica.pkl         # Normalizador vectorial de observaciones (CRÍTICO)
│
└── rival_stats.json     # [GENERADO AUTOMÁTICAMENTE] - Histórico de winrates por personaje

⚠️ **No borres archivos de estas carpetas**, ya que son dependencias críticas.

---

## 📁 `/env` — *El Corazón del Entorno*

Contiene la lógica que transforma el juego en un problema de IA.

* **`blanka_env.py (v5.17)`**
  Define el entorno Gymnasium. Gestiona:

  * Steps
  * Reset de combates
  * Procesamiento de observaciones

* **`reward.py`**
  Define la "ética" del agente:

  * Recompensas por daño
  * Penalizaciones por recibir golpes o hacer spam

* **`action_space.py`**
  Catálogo de las **26 acciones**:

  * Movimientos
  * Saltos
  * Macros de Rolling

* **`input_buffer.py` & `move_detector.py`**
  Micro-lógica:

  * Gestión de cargas (mantener atrás 1s)
  * Detección de movimientos exitosos

---

## 📁 `/lua` — *El Motor dentro de MAME*

Scripts ejecutados dentro del emulador.

* **`autoplay_bridge.lua (v2.22)`**
  Script maestro:

  * FSM (Máquina de Estados)
  * Inserta monedas
  * Selecciona a Blanka
  * Lee la RAM

✔️ Es el **único archivo Lua necesario para entrenamiento**.

---

## 📁 `/config` — *La Base de Datos*

* **`constants.py`**
  Contiene todas las direcciones de memoria (offsets):

  * HP
  * Tiempo
  * Posiciones X/Y

💡 Si cambias ROM o versión → **solo editas este archivo**.

---

## 📁 `/core` — *Persistencia*

* **`rival_registry.py`**
  Gestiona `rival_stats.json`:

  * Registro de victorias/derrotas
  * Permite *Curriculum Learning automático*

---

## 📁 `/dinamicos` — *Memoria Volátil (I/O)*

Comunicación entre Python y MAME:

* `state_N.txt` → MAME ➜ Python
* `mame_input_N.txt` → Python ➜ MAME

⚠️ Puedes borrar el contenido, **pero no la carpeta**.

---

## ⚙️ FUNCIONAMIENTO DEL BUCLE DE ENTRENAMIENTO

### 🚀 Lanzamiento

`train_UNICA.py` inicia **N instancias de MAME** (por defecto 6) en modo **nothrottle**.

---

### 🔁 Ciclo de Experiencia (Rollout)

1. MAME escribe estado en `dinamicos/state_N.txt`
2. `blanka_env.py`:

   * Lee estado
   * Normaliza datos
   * Envía a la red PPO
3. La IA decide acción
4. Escribe en `dinamicos/mame_input_N.txt`
5. MAME ejecuta acción
6. 🔄 Repetir a máxima velocidad

---

### 💾 Datos Guardados

* **Modelos:**
  `models/` → checkpoints `.zip` cada **75.000 pasos**

* **Normalización:**
  `vecnorm_unica.pkl` → imprescindible para continuar entrenamiento

* **Estadísticas:**
  `rival_stats.json` → progreso contra cada rival

---

## 🖥️ CONFIGURACIONES

| Configuración | Rendimiento  | Estabilidad | Notas                       |
| ------------- | ------------ | ----------- | --------------------------- |
| 1 Instancia   | Lento        | Alta        | Debug o PCs débiles         |
| 6 Instancias  | Óptimo       | Alta        | ⭐ Configuración estándar   |
| Con Throttle  | Muy lento    | Perfecta    | Solo para `watch_blanka.py` |
| Sin Throttle  | Ultra rápido | Alta        | >500 FPS                    |
| Con Visión    | Medio        | Media       | Solo Instancia 0 visible    |

---

## 🚀 COMANDOS RÁPIDOS

### 🧹 Limpiar

```bash
python limpia.py
```

---

### 🧠 Entrenar (Modo Pro)

```bash
python train_UNICA.py --envs 6
```

---

### 👀 Entrenar y Ver

```bash
python train_UNICA.py --envs 6 --visible
```

---

### 🏆 Ver al Campeón

```bash
python watch_blanka.py
```

---

### 📊 Ver Estadísticas

```bash
python train_UNICA.py --stats
```

---

## ⚠️ AVISO CRÍTICO

El sistema de **Doble Buffer (v5.17)** evita que el entrenamiento se rompa cuando MAME corre más rápido que Python.

🚫 **NO modifiques los tiempos de espera en `mame_bridge.py`**
a menos que experimentes cierres inesperados.

---

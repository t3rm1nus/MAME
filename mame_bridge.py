"""
mame_bridge.py — Bridge Python↔MAME via archivos (state.txt / mame_input.txt)
==============================================================================
Versión: 1.2 (29/03/2026)

Cambios v1.2 — FIX CRÍTICO json_error al 100%:
  · _read_state() ya no usa sleep fijo de 1/60s. Ahora usa polling activo:
    espera hasta que el frame_id en state.txt cambie respecto al anterior.
    Esto garantiza que Python siempre lee un frame NUEVO escrito por Lua,
    eliminando la race condition donde se leía el archivo a mitad de escritura
    o se leía el mismo frame dos veces.
  · Timeout de polling: 500ms (30 frames a 60fps). Si Lua no escribe en
    500ms es señal de que MAME está pausado o colgado.
  · Reintentos de JSON (heredados de v1.1) mantenidos como segunda línea
    de defensa contra escrituras parciales.
  · step() ya no hace time.sleep() propio — el sleep está dentro de
    _wait_new_frame() como parte del polling.

Cambios v1.1 (recordatorio):
  · _read_state() reintentaba 3 veces con 2ms si leía vacío o JSON inválido.
  · soft_reset() exige "in_combat"=True además de HP >= MIN_HP_VALID.
"""

import json
import os
import time
import subprocess
from typing import Optional, Dict, List

# ── RUTAS POR DEFECTO ─────────────────────────────────────────────────────────
BASE_DIR   = r"C:\proyectos\MAME"
MAME_EXE   = r"C:\proyectos\MAME\EMULADOR\mame.exe"
MAME_ROM   = "sf2ce"
MAME_LUASC = r"C:\proyectos\MAME\lua\autoplay_bridge.lua"

MIN_HP_VALID = 100

# Polling: tiempo máximo esperando un frame nuevo de Lua
_FRAME_POLL_TIMEOUT = 0.5    # 500ms — si Lua no escribe en este tiempo, hay problema
_FRAME_POLL_SLEEP   = 0.001  # 1ms entre checks de polling

# Reintentos de parseo JSON si el archivo tiene contenido parcial
_READ_RETRIES    = 5
_READ_RETRY_DELAY = 0.002   # 2ms


class MAMEBridge:
    """
    Bridge de archivos Python↔MAME.

    Uso básico:
        bridge = MAMEBridge(instance_id=0)
        state  = bridge.step([0]*12)   # NOOP, devuelve dict con estado
        bridge.disconnect()
    """

    def __init__(self, instance_id: int = 0, base_dir: str = BASE_DIR):
        self.instance_id = instance_id
        self._base       = base_dir

        sfx = "" if instance_id == 0 else f"_{instance_id}"
        self._input_file = os.path.join(base_dir, f"mame_input{sfx}.txt")
        self._state_file = os.path.join(base_dir, f"state{sfx}.txt")
        self._reset_file = os.path.join(base_dir, f"reset_signal{sfx}.txt")

        self._mame_proc: Optional[subprocess.Popen] = None
        self._last_state: Optional[Dict]             = None
        self._last_frame_id: int                     = -1  # último frame leído

        # Contadores de diagnóstico
        self._read_fail_count  = 0
        self._read_ok_count    = 0
        self._last_fail_report = 0

        os.makedirs(base_dir, exist_ok=True)
        self._write_input([0] * 12)

    # ── I/O ──────────────────────────────────────────────────────────────────

    def _write_input(self, buttons: List[int]) -> bool:
        """Escribe los 12 botones en mame_input.txt como CSV."""
        line = ",".join(str(int(b)) for b in buttons[:12])
        try:
            with open(self._input_file, "w", encoding="ascii") as f:
                f.write(line + "\n")
            return True
        except Exception as e:
            print(f"[MAMEBridge#{self.instance_id}] ERROR write_input: {e}")
            return False

    def _parse_state_file(self) -> Optional[Dict]:
        """
        Lee y parsea state.txt. Reintenta hasta _READ_RETRIES veces si
        el archivo está vacío o el JSON es inválido (escritura parcial de Lua).
        """
        for attempt in range(_READ_RETRIES):
            try:
                with open(self._state_file, "r", encoding="utf-8") as f:
                    raw = f.read().strip()

                if not raw:
                    if attempt < _READ_RETRIES - 1:
                        time.sleep(_READ_RETRY_DELAY)
                        continue
                    self._record_fail("empty")
                    return None

                result = json.loads(raw)
                self._read_ok_count += 1
                return result

            except FileNotFoundError:
                return None
            except (json.JSONDecodeError, ValueError):
                if attempt < _READ_RETRIES - 1:
                    time.sleep(_READ_RETRY_DELAY)
                    continue
                self._record_fail("json_error")
                return None
            except Exception as e:
                print(f"[MAMEBridge#{self.instance_id}] ERROR read_state: {e}")
                return None
        return None

    def _wait_new_frame(self, timeout: float = _FRAME_POLL_TIMEOUT) -> Optional[Dict]:
        """
        [FIX v1.2] Espera activamente hasta que Lua escriba un frame NUEVO
        en state.txt (frame_id distinto al último leído).

        Esto reemplaza el time.sleep(1/60) fijo que causaba el 100% json_error:
        con sleep fijo, Python podía leer el archivo exactamente mientras Lua
        lo estaba reescribiendo (ventana de 0 bytes). Con polling, esperamos
        a que el contenido sea válido Y sea un frame nuevo.

        Retorna el dict del nuevo frame, o None si timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self._parse_state_file()
            if st is not None:
                frame_id = st.get("frame", -1)
                if frame_id != self._last_frame_id:
                    # Frame nuevo — actualizar y devolver
                    self._last_frame_id = frame_id
                    return st
            # Frame no disponible aún o mismo frame — esperar un poco
            time.sleep(_FRAME_POLL_SLEEP)

        # Timeout — devolver None
        self._record_fail("timeout")
        return None

    def _record_fail(self, reason: str):
        """Registra un fallo; solo imprime cada 100 fallos."""
        self._read_fail_count += 1
        total = self._read_fail_count + self._read_ok_count
        if self._read_fail_count - self._last_fail_report >= 100:
            self._last_fail_report = self._read_fail_count
            pct = self._read_fail_count * 100 / max(total, 1)
            print(f"[MAMEBridge#{self.instance_id}] READ FAILS: "
                  f"{self._read_fail_count}/{total} ({pct:.1f}%) reason={reason}")

    # ── API PÚBLICA ───────────────────────────────────────────────────────────

    def step(self, buttons: List[int]) -> Optional[Dict]:
        """
        Envía inputs a MAME y espera el siguiente frame.

        [FIX v1.2] Ya no usa time.sleep(1/60). En su lugar, _wait_new_frame()
        hace polling hasta que Lua escriba un frame con frame_id distinto al
        anterior. Esto garantiza que cada step() lee exactamente 1 frame nuevo.
        """
        self._write_input(buttons)
        st = self._wait_new_frame()
        if st is not None:
            self._last_state = st
        return st

    def soft_reset(self, timeout: float = 90.0) -> bool:
        """
        Espera a que MAME esté en un combate válido (in_combat=True y HP >= MIN_HP_VALID).
        """
        try:
            with open(self._reset_file, "w", encoding="ascii") as f:
                f.write("reset\n")
        except Exception:
            pass

        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self._parse_state_file()  # lectura directa, sin esperar frame nuevo
            if st:
                p1 = int(st.get("p1_hp", 0))
                p2 = int(st.get("p2_hp", 0))

                if "in_combat" in st:
                    if st["in_combat"] and p1 >= MIN_HP_VALID and p2 >= MIN_HP_VALID:
                        try:
                            os.remove(self._reset_file)
                        except OSError:
                            pass
                        return True
                else:
                    # Fallback para Lua antiguo sin campo in_combat
                    if p1 >= MIN_HP_VALID and p2 >= MIN_HP_VALID:
                        try:
                            os.remove(self._reset_file)
                        except OSError:
                            pass
                        return True

            time.sleep(0.1)

        print(f"[MAMEBridge#{self.instance_id}] soft_reset TIMEOUT ({timeout:.0f}s)")
        return False

    def restart_game(self):
        if self._mame_proc and self._mame_proc.poll() is None:
            print(f"[MAMEBridge#{self.instance_id}] Cerrando MAME (PID {self._mame_proc.pid})...")
            self._mame_proc.terminate()
            try:
                self._mame_proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._mame_proc.kill()
            self._mame_proc = None
            time.sleep(1.5)
            self.launch_mame()
        else:
            print(f"[MAMEBridge#{self.instance_id}] WARN: MAME no fue lanzado por el bridge. "
                  "Reinicia MAME manualmente con el Lua cargado.")

    def launch_mame(self, mame_exe: str = MAME_EXE,
                    rom: str = MAME_ROM,
                    lua_script: str = MAME_LUASC) -> bool:
        if not os.path.isfile(mame_exe):
            print(f"[MAMEBridge] WARN: mame_exe no encontrado: {mame_exe}")
            return False

        cmd = [mame_exe, rom, "-autoboot_script", lua_script]
        if self.instance_id > 0:
            cmd.append("-nothrottle")

        try:
            self._mame_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=os.path.dirname(mame_exe),
            )
            print(f"[MAMEBridge#{self.instance_id}] MAME lanzado (PID {self._mame_proc.pid})")
            return True
        except Exception as e:
            print(f"[MAMEBridge#{self.instance_id}] ERROR lanzando MAME: {e}")
            return False

    def is_alive(self) -> bool:
        if self._mame_proc is None:
            return True
        return self._mame_proc.poll() is None

    def disconnect(self):
        try:
            self._write_input([0] * 12)
        except Exception:
            pass
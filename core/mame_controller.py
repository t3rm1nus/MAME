import os
import time

# RUTA LOCAL: Directamente en tu carpeta de proyecto
INPUT_FILE = r"C:\proyectos\MAME\mame_input.txt"
TEMP_FILE = INPUT_FILE + ".tmp"
FRAME_TIME = 1 / 60

# --- ESTA ES LA LÍNEA QUE FALTA ---
CHARGE_FRAMES = 62
GOLPE_FRAMES = 10
# ----------------------------------

class MAMEController:
    def __init__(self):
        self.input_file = INPUT_FILE
        self._clear()
        print(f"🔥 CONTROLADOR LOCAL: {self.input_file}")

    def _clear(self):
        try:
            with open(self.input_file, "w") as f:
                f.write("\n")
        except: pass

    def write_frame(self, actions):
        content = " ".join(actions) + "\n"
        try:
            with open(TEMP_FILE, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
            os.replace(TEMP_FILE, self.input_file)
            if actions: print(f"  [DISK] -> {actions}")
        except:
            pass
        time.sleep(FRAME_TIME)

    def release(self, frames=2):
        for _ in range(frames):
            self.write_frame([])

    def hold(self, actions, frames):
        for _ in range(frames):
            self.write_frame(actions)
        self.release(2)

    def tap(self, actions, times=1, gap_frames=3, **kwargs):
        gap = kwargs.get('gap', gap_frames)
        for _ in range(times):
            self.write_frame(actions)
            self.release(gap)

    def set_sticky_input(self, buttons):
        """Escribe los botones y NO hace release al final"""
        self.write_frame(buttons)
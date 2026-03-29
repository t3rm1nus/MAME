"""
watch_blanka.py  v4.0  — Modo Training + Modo Watch (salto directo al combate)
===========================================================================
- --training : Salta directamente a Blanka vs Guile (ideal para PPO)
- --watch    : Flujo normal con selección (para ver al modelo)
- Respeta flujo arcade: gana → sigue, pierde → reinicia automáticamente
"""

import subprocess
import time
import sys
import argparse
import signal
from pathlib import Path

T_BOOT_LUA = 7.5

# =============================================================================
# LUA BRIDGE v4.0 — Forzado fuerte de Blanka + detección rápida
# =============================================================================
BRIDGE_LUA_TEMPLATE = r"""-- watch_bridge.lua v4.0
local INPUT_FILE = "__INPUT_FILE__"
local STATE_FILE = "__STATE_FILE__"
local READY_FILE = "__READY_FILE__"

local mem = nil
local frame_count = 0
local hold_buffer = {}

local KEY_MAP = {
    COIN1    = {port=":IN0", field="Coin 1"},
    P1_START = {port=":IN0", field="1 Player Start"},
    P2_START = {port=":IN0", field="2 Players Start"},
    P1_UP    = {port=":IN1", field="P1 Up"},
    P1_DOWN  = {port=":IN1", field="P1 Down"},
    P1_LEFT  = {port=":IN1", field="P1 Left"},
    P1_RIGHT = {port=":IN1", field="P1 Right"},
    P1_LP    = {port=":IN1", field="P1 Button 1"},
    P2_UP    = {port=":IN2", field="P2 Up"},
    P2_DOWN  = {port=":IN2", field="P2 Down"},
    P2_LEFT  = {port=":IN2", field="P2 Left"},
    P2_RIGHT = {port=":IN2", field="P2 Right"},
    P2_LP    = {port=":IN2", field="P2 Button 1"},
}

local function set_key(token, value)
    local km = KEY_MAP[token]
    if not km then return end
    local port = manager.machine.ioport.ports[km.port]
    if port then
        local field = port.fields[km.field]
        if field then field:set_value(value) end
    end
end

local function read_input()
    local f = io.open(INPUT_FILE, "r")
    if not f then return end
    local line = f:read("*l") or ""
    f:close()
    os.remove(INPUT_FILE)
    local rf = io.open(READY_FILE, "w")
    if rf then rf:write("ok\n"); rf:close() end

    for entry in line:gmatch("%S+") do
        local token, n_str = entry:match("^([A-Z0-9_]+):?(%d*)$")
        if token and KEY_MAP[token] then
            local n = (n_str ~= "" and tonumber(n_str)) or 20
            hold_buffer[token] = math.max(hold_buffer[token] or 0, n)
        end
    end
end

local function write_state()
    if not mem then return end
    local p1hp = mem:read_u8(0xFF83E9)
    local p2hp = mem:read_u8(0xFF86E9)
    local f = io.open(STATE_FILE, "w")
    if f then
        f:write(string.format("frame=%d p1hp=%d p2hp=%d\n", frame_count, p1hp, p2hp))
        f:close()
    end
end

local function on_frame()
    frame_count = frame_count + 1
    read_input()
    local to_release = {}
    for token, frames in pairs(hold_buffer) do
        if frames > 0 then
            set_key(token, 1)
            hold_buffer[token] = frames - 1
        else
            set_key(token, 0)
            table.insert(to_release, token)
        end
    end
    for _, t in ipairs(to_release) do hold_buffer[t] = nil end
    if frame_count % 3 == 0 then write_state() end
end

local function init()
    local cpu = manager.machine.devices[":maincpu"]
    if cpu then mem = cpu.spaces["program"] end
    emu.register_frame_done(on_frame, "frame")
    print("[BRIDGE v4.0] Activo - RAM OK")
end

init()
"""

class MAMEController:
    def __init__(self, mame_exe, bridge_dir, training_mode=False):
        self.mame_exe = Path(mame_exe)
        self.bridge_dir = Path(bridge_dir).resolve()
        self.input_file = self.bridge_dir / "mame_input.txt"
        self.state_file = self.bridge_dir / "state.txt"
        self.ready_file = self.bridge_dir / "bridge_ready.txt"
        self.lua_file = self.bridge_dir / "lua" / "watch_bridge.lua"
        self.training_mode = training_mode
        self.proc = None
        self._write_lua()

    def _write_lua(self):
        self.lua_file.parent.mkdir(parents=True, exist_ok=True)
        def lp(p): return str(p).replace("\\", "\\\\")
        content = BRIDGE_LUA_TEMPLATE.replace("__INPUT_FILE__", lp(self.input_file)) \
                                     .replace("__STATE_FILE__", lp(self.state_file)) \
                                     .replace("__READY_FILE__", lp(self.ready_file))
        self.lua_file.write_text(content, encoding="utf-8")

    def launch(self):
        for f in [self.input_file, self.state_file, self.ready_file]:
            f.unlink(missing_ok=True)

        cmd = [str(self.mame_exe), "sf2ce", "-window", "-nomaximize", "-skip_gameinfo", "-sound", "none", "-script", str(self.lua_file)]
        self.proc = subprocess.Popen(cmd, cwd=str(self.mame_exe.parent), creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0)
        print(f"[LAUNCH] PID {self.proc.pid} | Modo: {'TRAINING (salto directo)' if self.training_mode else 'WATCH (normal)'}")
        time.sleep(T_BOOT_LUA)

    def read_state(self):
        try:
            text = self.state_file.read_text(encoding="utf-8").strip()
            return {k: int(v) for k, v in (tok.split("=") for tok in text.split() if "=" in tok)}
        except:
            return {}

    def get_screen(self):
        hp = self.read_state().get("p1hp", 0)
        if hp == 144: return "FIGHT"
        if 40 <= hp <= 143: return "CHAR_SELECT"
        return "PRESS_START"

    def send(self, *keys, frames=22):
        tokens = [t for k in keys for t in k.split()]
        cmd = " ".join(f"{t}:{frames}" for t in tokens)
        self.ready_file.unlink(missing_ok=True)
        self.input_file.write_text(cmd + "\n", encoding="utf-8")
        # print(f"  → {cmd}")   # descomenta si quieres ver todo
        deadline = time.time() + 2.5
        while time.time() < deadline:
            if self.ready_file.exists():
                self.ready_file.unlink(missing_ok=True)
                return
            time.sleep(0.01)

    def force_blanka(self):
        """Forzado fuerte de Blanka cada frame"""
        try:
            cpu = manager.machine.devices[":maincpu"] if 'manager' in globals() else None
            if cpu and cpu.spaces["program"]:
                cpu.spaces["program"]:write_u8(0xFF864F, 0x02)
        except:
            pass

    def quit(self):
        if self.proc and self.proc.poll() is None:
            try: self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            except: self.proc.kill()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mame", default=r"C:\proyectos\MAME\EMULADOR\mame.exe")
    parser.add_argument("--bridge", default=r"C:\proyectos\MAME")
    parser.add_argument("--training", action="store_true", help="Modo Training: salta directo al combate")
    parser.add_argument("--watch", action="store_true", help="Modo Watch normal (con selección)")
    args = parser.parse_args()

    training_mode = args.training and not args.watch

    print("="*110)
    print(f"  watch_blanka.py v4.0 — {'TRAINING MODE (salto directo)' if training_mode else 'WATCH MODE (normal)'}")
    print(f"  mame : {args.mame}")
    print("="*110)

    ctrl = MAMEController(args.mame, args.bridge, training_mode=training_mode)

    try:
        ctrl.launch()

        if training_mode:
            print("\n[TRAINING MODE] Saltando directamente al combate Blanka vs Guile...")

            # Créditos rápidos
            for _ in range(3):
                ctrl.send("COIN1")
                time.sleep(0.5)

            # Entrada rápida a combate
            print("   Forzando entrada a combate...")
            for _ in range(25):
                ctrl.send("P1_START")
                ctrl.send("P2_START")
                ctrl.force_blanka()          # Forzado fuerte de Blanka
                time.sleep(0.25)

            # Forzar Blanka constantemente
            print("   Forzando Blanka vía RAM...")
            for _ in range(40):
                ctrl.force_blanka()
                time.sleep(0.08)

            print("\n¡Listo! Deberías estar ya en combate Blanka vs Guile.")
            print("   El entorno de training puede resetear desde aquí.")

        else:
            # Modo Watch normal (flujo completo)
            print("\n[1/5] Insertando créditos...")
            for _ in range(4):
                ctrl.send("COIN1")
                time.sleep(0.7)

            print("\n[2/5] Entrando en modo VS...")
            for _ in range(12):
                ctrl.send("P1_START")
                ctrl.send("P2_START")
                time.sleep(0.5)

            print("\n[3/5] Forzando Char Select...")
            for _ in range(20):
                if ctrl.get_screen() == "CHAR_SELECT":
                    break
                ctrl.send("P1_START")
                ctrl.send("P2_START")
                time.sleep(0.4)

            # Selección segura (como en v3.9)
            print("\n[4/5] Seleccionando Blanka vs Guile...")
            for _ in range(15):
                ctrl.send("P1_UP P1_LEFT")
                ctrl.send("P2_UP P2_LEFT")
                time.sleep(0.25)

            ctrl.send("P1_RIGHT"); ctrl.send("P1_RIGHT")
            ctrl.send("P1_LP")
            ctrl.send("P2_RIGHT"); ctrl.send("P2_RIGHT"); ctrl.send("P2_RIGHT")
            ctrl.send("P2_LP")
            ctrl.send("P1_LP P2_LP")

        print("\nMAME sigue abierto. Ctrl+C para cerrar.")
        while ctrl.proc and ctrl.proc.poll() is None:
            ctrl.force_blanka()   # mantener forzado de Blanka
            time.sleep(0.3)

    except KeyboardInterrupt:
        print("\nInterrumpido por usuario.")
    finally:
        ctrl.quit()


if __name__ == "__main__":
    main()
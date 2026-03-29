import subprocess
import os
import time
import pygetwindow as gw
import win32gui
import win32con

class MAMEClient:
    def __init__(self):
        # Rutas verificadas
        self.mame_path = r"C:\proyectos\MAME\EMULADOR\mame.exe"
        self.rom = "sf2ce"
        self.lua_script = r"C:\proyectos\MAME\lua\lua_bridge.lua"

    def launch(self):
        print("🚀 Lanzando MAME con Consola Activa...")

        # Construimos la lista de argumentos del comando
        args = [
            self.mame_path,
            self.rom,
            "-window",
            "-nomouse",
            "-skip_gameinfo",
            "-console",
            "-autoboot_script", self.lua_script
        ]

        # Ejecutamos Popen. 
        # NOTA: creationflags va FUERA de la lista de argumentos (args)
        subprocess.Popen(
            args, 
            cwd=os.path.dirname(self.mame_path),
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        
        # Esperamos a que cargue antes de intentar darle el foco
        time.sleep(5)
        self.focus_mame()

    def focus_mame(self):
        print("🎯 Intentando forzar foco en MAME...")
        # Buscamos ventanas que contengan "MAME" o el nombre del juego
        windows = gw.getAllWindows()
        
        for w in windows:
            title = w.title.upper()
            if "MAME" in title or "STREET FIGHTER" in title:
                try:
                    hwnd = w._hWnd
                    # Restaurar y traer al frente
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    win32gui.SetForegroundWindow(hwnd)
                    print(f"✅ Foco aplicado a: {w.title}")
                    return
                except Exception as e:
                    print(f"⚠️ Error al enfocar: {e}")
        
        print("❌ No se encontró la ventana de MAME para auto-foco.")
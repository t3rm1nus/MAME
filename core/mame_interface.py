# core/mame_interface.py

import subprocess
import time
from core.ram_reader import RAMReader
import os

class MAMEInterface:

    def __init__(self, mame_path, rom="sf2ce"):
        self.mame_path = mame_path
        self.rom = rom
        self.process = None
        self.ram = None
        self.state_file = "state.txt"

    def get_player_data(self, player=1):
        """Lee el archivo generado por LUA"""
        if not os.path.exists(self.state_file):
            return {"is_on_ground": True, "side": "LEFT"} # Fallback

        try:
            with open(self.state_file, "r") as f:
                data = f.read().split(",")
                # Formato LUA: HP, X, Y, Side
                y_pos = int(data[2])
                # En SF2, Y=0 o Y=constante suele ser el suelo. 
                # Si Y cambia significativamente, está saltando.
                return {
                    "hp": int(data[0]),
                    "x": int(data[1]),
                    "y": y_pos,
                    "is_on_ground": y_pos <= 0, # Ajustar según valor real en SF2
                    "side": data[3]
                }
        except:
            return {"is_on_ground": True}
            
    def start(self):
        self.process = subprocess.Popen([
            self.mame_path,
            self.rom,
            "-window",
            "-skip_gameinfo",
            "-autoboot_script", "lua/lua_bridge.lua"
        ])

        time.sleep(2)

        self.ram = RAMReader(self)

    def read_memory(self, address):
        # 🔴 esto lo tiene que implementar lua_bridge vía pipe
        raise NotImplementedError

    def send_input(self, inputs):
        # 🔴 igual: pipe hacia lua
        pass

    def read(self, key):
        return self.ram.read(key)

    def reset(self):
        # soft reset
        pass
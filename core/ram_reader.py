# core/ram_reader.py

from config.constants import RAM_MAP


class RAMReader:

    def __init__(self, emulator):
        self.emulator = emulator

    def read(self, key):
        addr = RAM_MAP[key]
        return self.emulator.read_memory(addr)

    def read_all(self):
        return {k: self.read(k) for k in RAM_MAP}
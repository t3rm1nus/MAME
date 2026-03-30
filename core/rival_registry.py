"""
rival_registry.py — Registro persistente de estadísticas por rival
===================================================================
Guarda y carga stats de combate (winrate, daño, etc.) por char_id.
Se actualiza al final de cada combate individual y se persiste en JSON.

Archivo: C:/proyectos/MAME/rival_stats.json

v2.0 (30/03/2026):
  · Guarda cada 5 episodios por rival (antes era 10)
  · Guarda también cada 50 episodios globales
  · El contador global se expone para que train.py lo use en Ctrl+C
"""

import json
import os
import time
from typing import Dict, Optional

STATS_FILE = r"C:\proyectos\MAME\rival_stats.json"

CHAR_NAMES = {
    0: "Ryu",    1: "Honda",   2: "Blanka",  3: "Guile",
    4: "Ken",    5: "Chun-Li", 6: "Zangief", 7: "Dhalsim",
    8: "M.Bison",9: "Sagat",  10: "Balrog", 11: "Vega",
}


def _empty_stats() -> Dict:
    return {
        "episodes":     0,
        "wins":         0,
        "losses":       0,
        "total_p1_dmg": 0.0,
        "total_p2_dmg": 0.0,
        "win_rate":     0.0,
        "last_seen":    "",
    }


class RivalRegistry:

    def __init__(self, stats_file: str = STATS_FILE):
        self._path = stats_file
        self._stats: Dict[int, Dict] = {}
        self._global_episodes: int = 0   # contador global para guardado periódico
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._stats = {int(k): v for k, v in raw.items()}
                print(f"[RivalRegistry] Cargado: {self._path}")
            except Exception as e:
                print(f"[RivalRegistry] WARN cargando: {e} — iniciando vacío")
                self._stats = {}
        else:
            print(f"[RivalRegistry] Nuevo registro: {self._path}")
            self._stats = {}

    def save(self):
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in self._stats.items()},
                          f, indent=2, ensure_ascii=False)
            # Escritura atómica: renombrar solo si el write fue bien
            os.replace(tmp, self._path)
        except Exception as e:
            print(f"[RivalRegistry] ERROR guardando: {e}")

    def get(self, char_id: int) -> Dict:
        if char_id not in self._stats:
            self._stats[char_id] = _empty_stats()
        return self._stats[char_id]

    def record_episode(self, char_id: int, won: bool,
                       p1_dmg: float, p2_dmg: float, extras=None):
        if char_id < 0 or char_id > 11:
            return
        s = self.get(char_id)
        s["episodes"]     += 1
        s["wins"]         += 1 if won else 0
        s["losses"]       += 0 if won else 1
        s["total_p1_dmg"] += p1_dmg
        s["total_p2_dmg"] += p2_dmg
        s["win_rate"]      = s["wins"] / max(s["episodes"], 1)
        s["last_seen"]     = time.strftime("%Y-%m-%d %H:%M:%S")

        self._global_episodes += 1

        # Guardar cada 5 episodios por rival O cada 50 globales
        if s["episodes"] % 5 == 0 or self._global_episodes % 50 == 0:
            self.save()

    def print_summary(self):
        print("\n" + "="*55)
        print(f"  {'Rival':<12} {'Eps':>5} {'W':>5} {'L':>5} {'WR%':>6} {'AvgDmg':>8}")
        print("  " + "-"*53)
        for cid in sorted(self._stats.keys()):
            s = self._stats[cid]
            eps     = s["episodes"]
            wins    = s["wins"]
            losses  = s["losses"]
            wr      = s["win_rate"] * 100
            avg_dmg = s["total_p2_dmg"] / max(eps, 1)
            name    = CHAR_NAMES.get(cid, f"ID_{cid}")
            flag    = " ✓" if wr >= 70 else (" !" if wr < 30 and eps >= 20 else "")
            print(f"  {name:<12} {eps:>5} {wins:>5} {losses:>5} {wr:>5.1f}%  {avg_dmg:>7.1f}{flag}")
        print("="*55 + "\n")

    def weakest_rival(self) -> Optional[int]:
        candidates = {cid: s for cid, s in self._stats.items()
                      if s["episodes"] >= 10}
        if not candidates:
            return None
        return min(candidates, key=lambda cid: candidates[cid]["win_rate"])

    def strongest_rival(self) -> Optional[int]:
        candidates = {cid: s for cid, s in self._stats.items()
                      if s["episodes"] >= 10}
        if not candidates:
            return None
        return max(candidates, key=lambda cid: candidates[cid]["win_rate"])
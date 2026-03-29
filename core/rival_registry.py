"""
rival_registry.py — Registro persistente de estadísticas por rival
===================================================================
Guarda y carga stats de combate (winrate, daño, etc.) por char_id.
Se actualiza al final de cada episodio y se persiste en JSON.

Archivo: C:/proyectos/MAME/rival_stats.json
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
        "total_p1_dmg": 0.0,   # daño recibido acumulado
        "total_p2_dmg": 0.0,   # daño infligido acumulado
        "win_rate":     0.0,
        "last_seen":    "",
    }


class RivalRegistry:
    """
    Registro de estadísticas por rival (char_id 0-11).
    Thread-safe para uso con SubprocVecEnv si se usa con lock,
    pero para 1 instancia es suficiente sin lock.
    """

    def __init__(self, stats_file: str = STATS_FILE):
        self._path = stats_file
        self._stats: Dict[int, Dict] = {}
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                # Las claves en JSON son strings
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
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in self._stats.items()},
                          f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[RivalRegistry] ERROR guardando: {e}")

    def get(self, char_id: int) -> Dict:
        if char_id not in self._stats:
            self._stats[char_id] = _empty_stats()
        return self._stats[char_id]

    def record_episode(self, char_id: int, won: bool,
                       p1_dmg: float, p2_dmg: float):
        """Registra el resultado de un episodio contra un rival."""
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
        # Guardar automáticamente cada 10 episodios por rival
        if s["episodes"] % 10 == 0:
            self.save()

    def print_summary(self):
        """Imprime un resumen de todos los rivales conocidos."""
        print("\n" + "="*55)
        print(f"  {'Rival':<12} {'Eps':>5} {'W':>5} {'L':>5} {'WR%':>6} {'AvgDmg':>8}")
        print("  " + "-"*53)
        for cid in sorted(self._stats.keys()):
            s = self._stats[cid]
            eps   = s["episodes"]
            wins  = s["wins"]
            losses= s["losses"]
            wr    = s["win_rate"] * 100
            avg_dmg = s["total_p2_dmg"] / max(eps, 1)
            name  = CHAR_NAMES.get(cid, f"ID_{cid}")
            flag  = " ✓" if wr >= 70 else (" !" if wr < 30 and eps >= 20 else "")
            print(f"  {name:<12} {eps:>5} {wins:>5} {losses:>5} {wr:>5.1f}%  {avg_dmg:>7.1f}{flag}")
        print("="*55 + "\n")

    def weakest_rival(self) -> Optional[int]:
        """Devuelve el char_id contra el que peor vamos (mín winrate con ≥10 eps)."""
        candidates = {cid: s for cid, s in self._stats.items()
                      if s["episodes"] >= 10}
        if not candidates:
            return None
        return min(candidates, key=lambda cid: candidates[cid]["win_rate"])

    def strongest_rival(self) -> Optional[int]:
        """Devuelve el char_id contra el que mejor vamos."""
        candidates = {cid: s for cid, s in self._stats.items()
                      if s["episodes"] >= 10}
        if not candidates:
            return None
        return max(candidates, key=lambda cid: candidates[cid]["win_rate"])
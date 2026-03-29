# env/move_detector.py

def is_rolling(buffer):
    seq = buffer[-6:]
    return (
        "LEFT" in seq and
        "RIGHT" in seq and
        any(btn in seq for btn in ["LP", "MP", "HP"])
    )


def is_electric(buffer):
    seq = buffer[-8:]
    punches = [a for a in seq if a in ["LP", "MP", "HP"]]
    return len(punches) >= 4


def is_vertical(buffer):
    seq = buffer[-6:]
    return (
        "DOWN" in seq and
        "UP" in seq and
        any(btn in seq for btn in ["LK", "MK", "HK"])
    )
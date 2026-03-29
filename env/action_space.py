# env/action_space.py

ACTIONS = [
    "NOOP",
    "LEFT",
    "RIGHT",
    "UP",
    "DOWN",
    "LP",
    "MP",
    "HP",
    "LK",
    "MK",
    "HK"
]

ACTION_MAP = {
    "NOOP": [],
    "LEFT": ["LEFT"],
    "RIGHT": ["RIGHT"],
    "UP": ["UP"],
    "DOWN": ["DOWN"],
    "LP": ["P1_BUTTON1"],
    "MP": ["P1_BUTTON2"],
    "HP": ["P1_BUTTON3"],
    "LK": ["P1_BUTTON4"],
    "MK": ["P1_BUTTON5"],
    "HK": ["P1_BUTTON6"],
}
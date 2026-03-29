# env/reward.py

from env.move_detector import is_rolling


def compute_reward(prev_state, current_state, buffer, last_actions):

    reward = 0

    # daño infligido
    reward += (prev_state["enemy_hp"] - current_state["enemy_hp"])

    # daño recibido
    reward -= (prev_state["player_hp"] - current_state["player_hp"])

    # bonus por ejecución real
    if is_rolling(buffer):
        reward += 0.5

    # penalización por spam
    if len(set(last_actions[-5:])) == 1:
        reward -= 0.1

    return reward
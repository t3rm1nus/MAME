# env/input_buffer.py

class InputBuffer:
    def __init__(self, size=10):
        self.size = size
        self.buffer = []

    def push(self, action):
        self.buffer.append(action)
        if len(self.buffer) > self.size:
            self.buffer.pop(0)

    def get(self):
        return self.buffer

    def last(self, n):
        return self.buffer[-n:]
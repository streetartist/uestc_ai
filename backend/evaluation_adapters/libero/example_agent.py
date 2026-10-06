"""Runnable transport baseline; replace Policy with your policy or agent harness.

This safe no-op does not solve the manipulation tasks and claims no success.
"""


class Policy:
    def reset(self, goal):
        self.goal = goal

    def act(self, observation):
        # Read observation['images_jpeg_base64'], eef pose and task instruction.
        # Call a learned policy / planner / model, then return a native 7D action.
        return [0, 0, 0, 0, 0, 0, -1]


class Agent:
    def __init__(self):
        self.policy = Policy()

    def configure(self, config):
        self.config = config

    def reset(self, goal):
        self.policy.reset(goal)

    def act(self, observation):
        return self.policy.act(observation)

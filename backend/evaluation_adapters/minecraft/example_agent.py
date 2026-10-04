"""Minimal agent.py example to package at the root of a submission ZIP."""


class Agent:
    def configure(self, config):
        self.config = config

    def reset(self, goal):
        self.goal = goal

    def act(self, observation):
        # Official MineDojo eight-dimensional action: move forward (index 0).
        action = list(observation["noop_action"])
        action[0] = 1
        return action

"""Public acceptance baseline: fixed known layout, NOT a hidden-scene solution.

Package this file as agent.py with config.json. A competition agent must locate
objects from camera images; these coordinates are only the public test fixture.
"""
class Agent:
    def reset(self, goal):
        self.goal = goal
        self.turn = 0

    def act(self, observation):
        red = [-.05, -.05]
        destination = self.goal.get("target_xy", [.08, .08])
        phases = [([*red, 1.02], -1, 4), ([*red, .835], -1, 4),
                  ([*red, .835], 1, 3), ([*red, 1.04], 1, 4)]
        if self.goal["task"] != "lift":
            phases.extend([([*destination, 1.04], 1, 4),
                           ([*destination, .88 if self.goal["task"] == "stack" else .84], 1, 4),
                           ([*destination, .88 if self.goal["task"] == "stack" else .84], -1, 3),
                           ([*destination, 1.04], -1, 3)])
        self.turn += 1
        total = 0
        for target, gripper, duration in phases:
            total += duration
            if self.turn <= total:
                return {"target": target, "gripper": gripper, "repeat": 20}
        return {"target": [*destination, 1.04], "gripper": -1, "repeat": 20}

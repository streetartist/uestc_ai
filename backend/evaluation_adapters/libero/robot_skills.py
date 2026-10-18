"""Feedback-controlled skills using only the robot's published state."""
import math


def vector(value, size, name):
    if (not isinstance(value, list) or len(value) != size or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError(f"{name} requires {size} finite numbers")
    return [float(v) for v in value]


def rotation_error(target, current):
    """Target * conjugate(current), xyzw quaternion -> world axis-angle."""
    a, b, c, d = target
    x, y, z, w = [-current[0], -current[1], -current[2], current[3]]
    q = [d*x+a*w+b*z-c*y, d*y-a*z+b*w+c*x,
         d*z+a*y-b*x+c*w, d*w-a*x-b*y-c*z]
    norm = math.sqrt(sum(v*v for v in q))
    q = [v / norm for v in q]
    if q[3] < 0:
        q = [-v for v in q]
    sine = math.sqrt(sum(v*v for v in q[:3]))
    if sine < 1e-8:
        return [0., 0., 0.]
    scale = 2 * math.atan2(sine, q[3]) / sine
    return [v * scale for v in q[:3]]


class Motion:
    """One skill returns one native 7D action at each environment step."""
    def __init__(self, skill, observation, previous_gripper=-1):
        self.name = skill.get("name")
        self.start = list(observation["eef_position"])
        self.target = list(self.start)
        self.orientation = list(observation["eef_quaternion"])
        self.gripper = skill.get("gripper", previous_gripper)
        if "gripper" in skill and self.gripper not in (-1, 1):
            raise ValueError("gripper must be -1 (open) or 1 (close)")
        self.limit = skill.get("steps", 50 if self.name != "gripper" else 15)
        if type(self.limit) is not int or not 1 <= self.limit <= 120:
            raise ValueError("steps must be an integer from 1 to 120")
        if self.name == "move":
            delta = vector(skill.get("delta"), 3, "delta")
            if max(abs(v) for v in delta) > .15:
                raise ValueError("each move delta must be within +/-0.15 metres")
            self.target = [a + b for a, b in zip(self.start, delta)]
        elif self.name == "move_to":
            self.target = vector(skill.get("position"), 3, "position")
        elif self.name == "rotate":
            self.orientation = vector(skill.get("quaternion"), 4, "quaternion xyzw")
            norm = math.sqrt(sum(v*v for v in self.orientation))
            if norm < 1e-8:
                raise ValueError("quaternion must have nonzero norm")
            self.orientation = [v / norm for v in self.orientation]
        elif self.name == "action":
            self.raw = vector(skill.get("action"), 7, "action")
            if max(abs(v) for v in self.raw) > 1:
                raise ValueError("native action values must be within [-1, 1]")
        elif self.name not in ("gripper", "wait"):
            raise ValueError("skill name must be move, move_to, rotate, gripper, wait or action")
        self.count = 0
        self.last_error = None

    def action(self, observation):
        delta = [a-b for a, b in zip(self.target, observation["eef_position"])]
        angle = rotation_error(self.orientation, observation["eef_quaternion"])
        self.last_error = math.sqrt(sum(v*v for v in delta))
        settled = self.last_error < .004 and math.sqrt(sum(v*v for v in angle)) < .035
        reached = settled and self.name in ("move", "move_to", "rotate")
        if self.count >= self.limit or (self.count > 0 and reached):
            self.reason = "pose_reached" if reached else "skill_step_limit"
            return None
        self.count += 1
        if self.name == "action":
            return self.raw[:]
        clip = lambda v: max(-.3, min(.3, v))
        return [clip(v / .05) for v in delta] + [clip(v / .5) for v in angle] + [self.gripper]

    def feedback(self, observation):
        return {"skill": self.name, "reason": self.reason, "steps": self.count,
                "requested_position": self.target, "actual_position": observation["eef_position"],
                "position_error_metres": self.last_error,
                "gripper_position": observation["gripper_position"],
                "gripper_width_metres": abs(observation["gripper_position"][0] - observation["gripper_position"][1]),
                "gripper_command": self.gripper,
                "actual_delta": [a-b for a, b in zip(observation["eef_position"], self.start)]}

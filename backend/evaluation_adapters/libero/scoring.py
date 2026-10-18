"""Trusted per-target completion and environment-step efficiency scoring."""
from __future__ import annotations

def validate_scoring(value):
    if not isinstance(value, dict) or set(value) != {"version", "reference_steps"} or value["version"] != 2:
        raise ValueError("LIBERO scoring requires version 2 and per-target reference_steps")
    steps = value["reference_steps"]
    if (not isinstance(steps, list) or not 1 <= len(steps) <= 2
            or any(type(step) is not int or step <= 0 for step in steps)):
        raise ValueError("LIBERO reference_steps must contain one or two positive integers")
    return steps


class GoalScore:
    def __init__(self, reference_steps):
        self.reference_steps = validate_scoring({"version": 2, "reference_steps": reference_steps})
        self.since = [None] * len(reference_steps)
        self.last_step = 0

    def update(self, step, completed):
        if type(step) is not int or step != self.last_step + 1 or len(completed) != len(self.since):
            raise ValueError("LIBERO target history must cover every control step")
        for index, done in enumerate(completed):
            if type(done) is not bool:
                raise ValueError("LIBERO target states must be boolean")
            if not done:
                self.since[index] = None
            elif self.since[index] is None:
                self.since[index] = step
        self.last_step = step

    def result(self):
        count = len(self.since)
        completed = sum(step is not None for step in self.since)
        # A target knocked out of its destination loses both components. If it is
        # restored, the uninterrupted final successful interval supplies its time.
        efficiency = sum(max(0.0, min(1.0, 1.5 - step / reference))
                         for step, reference in zip(self.since, self.reference_steps)
                         if step is not None) / count * 40
        completion = completed / count * 60
        return {"completion_score": completion, "efficiency_score": efficiency,
                "automatic_score": completion + efficiency}

    def targets(self):
        count = len(self.since)
        return [{"target": index + 1, "completed": step is not None,
                 "attainment_step": step, "reference_steps": reference,
                 "completion_score": 60 / count if step is not None else 0,
                 "efficiency_score": 40 / count * max(0.0, min(1.0, 1.5 - step / reference))
                 if step is not None else 0}
                for index, (step, reference) in enumerate(zip(self.since, self.reference_steps))]


def native_goals(env, expected_count):
    """Use the exact native BDDL terminal predicates, inside the trusted process."""
    core = env.env
    goals = core.parsed_problem["goal_state"]
    if (not isinstance(goals, list) or len(goals) != expected_count
            or any(not isinstance(goal, (list, tuple)) or len(goal) != 3
                   or goal[0] not in {"on", "in"} for goal in goals)):
        raise ValueError("LIBERO scored goals must match the published one/two placement targets")
    return [bool(core._eval_predicate(goal)) for goal in goals]

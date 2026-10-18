"""Editable image-model harness. Agent.reset/act is also the submission interface."""
import json
from pathlib import Path
from model_api import ModelClient
from robot_skills import Motion


PROMPT = """Control the robot using the instruction, two camera images and its own state.
Images are upright, 128x128: external camera first, wrist camera second.
Select one skill, observe its result, then choose the next. World XYZ is not image XY.
Skills return native 7D actions [dx,dy,dz,rx,ry,rz,gripper]. Translation scales by
0.05 metres, axis-angle rotation by 0.5 radians; gripper +1 closes and -1 opens.
The harness holds the current orientation during translation and gripping.
Available skill JSON:
{"name":"move","delta":[0,0,-0.05],"gripper":-1,"steps":50}
{"name":"move_to","position":[0,0,1.05],"gripper":1,"steps":60}
{"name":"rotate","quaternion":[0,0,0,1],"gripper":1,"steps":50}
{"name":"gripper","gripper":1,"steps":15}
{"name":"wait","gripper":-1,"steps":10}
{"name":"action","action":[0,0,0,0,0,0,-1],"steps":1}
Use small moves and compare images to establish camera/world directions. Select the
object named in the instruction and retain its identity when similar objects appear.
After closing, lift and inspect whether the correct object follows. Closing the
gripper alone does not establish a grasp. A nearly closed empty gripper is evidence
of a missed grasp. Inspect the carried object's offset before lowering and releasing.
Use skill feedback to distinguish reaching a pose from a stalled movement.
Omitting gripper retains the previous gripper command.
Each move delta component must be within [-0.15, 0.15] metres. Skill steps must
be an integer from 1 to 120. Native action components must stay within [-1, 1].
Return one JSON object, for example:
{"observation":"brief visual evidence", "memory":{},
 "skill":{"name":"wait","gripper":-1,"steps":10}}.
memory is your persistent plan/target description; the next decision receives it.
"""


class Agent:
    def configure(self, config):
        self.config = config
        self.client = ModelClient(config["model"], config.get("max_output_tokens", 4096),
                                  config.get("api_timeout", 135))
        self.log = Path(__file__).with_name("harness.jsonl")

    def reset(self, goal):
        self.goal = goal
        self.memory = {}
        self.motion = None
        self.history = []
        self.previous_images = []
        self.last_gripper = -1
        self.log_record({"event": "reset", "goal": goal})

    def log_record(self, value):
        with self.log.open("a", encoding="utf-8") as file:
            file.write(json.dumps(value, ensure_ascii=False) + "\n")
        print("HARNESS " + json.dumps(value, ensure_ascii=False), flush=True)

    def act(self, observation):
        if self.motion:
            action = self.motion.action(observation)
            if action is not None:
                self.last_gripper = action[-1]
                return action
            feedback = self.motion.feedback(observation)
            self.history.append(feedback)
            self.log_record({"event": "skill_result", "step": observation["step"], **feedback})
            self.motion = None
        images = [observation["images_jpeg_base64"][name]
                  for name in ("agentview", "robot0_eye_in_hand")]
        state = {key: value for key, value in observation.items() if key != "images_jpeg_base64"}
        context = {"task": self.goal, "state": state, "memory": self.memory,
                   "recent_skills": self.history[-4:],
                   "image_order": (["previous_external", "previous_wrist"] if self.previous_images else [])
                                  + ["current_external", "current_wrist"]}
        error = None
        for attempt in range(2):
            raw = self.client.chat(PROMPT + json.dumps({**context, "format_error": error}),
                                   self.previous_images + images, json_mode=True,
                                   thinking=self.config.get("thinking"),
                                   reasoning_effort=self.config.get("reasoning_effort"))
            try:
                reply = json.loads(raw)
                motion = Motion(reply["skill"], observation, self.last_gripper)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                error = str(exc)
                self.log_record({"event": "format_error", "step": observation["step"],
                                 "error": error, "response": raw, "api": self.client.last_response})
                continue
            self.motion = motion
            self.memory = reply.get("memory", {})
            self.previous_images = images
            self.log_record({"event": "decision", "step": observation["step"],
                             "reply": reply, "api": self.client.last_response})
            action = self.motion.action(observation)
            self.last_gripper = action[-1]
            return action
        raise ValueError("Model skill format remained invalid: " + str(error))

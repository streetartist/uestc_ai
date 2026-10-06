"""CPU imitation baseline: retrieve an action from a public demonstration feature bank."""
from pathlib import Path
import numpy as np
from policy_features import observation_feature


class Agent:
    def configure(self, config):
        self.files = config.get('policy_files', {})

    def reset(self, goal):
        filename = self.files.get(goal['task_name'])
        if not filename:
            raise ValueError('config.policy_files must map every published task_name to a trained NPZ')
        with np.load(Path(__file__).parent / filename, allow_pickle=False) as policy:
            self.features = np.array(policy['features'], dtype=np.float32)
            self.actions = np.array(policy['actions'], dtype=np.float32)
        if (self.features.ndim != 2 or self.actions.shape != (len(self.features), 7)
                or not len(self.features) or not np.isfinite(self.features).all() or not np.isfinite(self.actions).all()):
            raise ValueError('invalid policy bank')

    def act(self, observation):
        feature = observation_feature(observation)
        distance = ((self.features - feature) ** 2).mean(axis=1)
        return np.clip(self.actions[int(distance.argmin())], -1, 1).tolist()

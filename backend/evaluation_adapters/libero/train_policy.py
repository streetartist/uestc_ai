"""Fit a small baseline feature bank from an official public LIBERO HDF5 demo."""
import argparse
from pathlib import Path
import h5py
import numpy as np
from PIL import Image
from policy_features import features


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stride', type=int, default=5)
    parser.add_argument('--max-samples', type=int, default=2048)
    args = parser.parse_args()
    if args.stride < 1 or args.max_samples < 1:
        parser.error('stride and max-samples must be positive')
    values, actions = [], []
    with h5py.File(args.demo, 'r') as dataset:
        for key in sorted(dataset['data']):
            demo = dataset['data'][key]
            for index in range(0, len(demo['actions']), args.stride):
                obs = demo['obs']
                # Official LIBERO HDF5 names differ from live robosuite observations.
                images = [Image.fromarray(obs[name][index][::-1])
                          for name in ('agentview_rgb', 'eye_in_hand_rgb')]
                values.append(features(images, obs['ee_pos'][index], obs['gripper_states'][index]))
                actions.append(demo['actions'][index])
                if len(values) >= args.max_samples:
                    break
            if len(values) >= args.max_samples:
                break
    if not values:
        raise ValueError('demo dataset contains no actions')
    np.savez_compressed(args.output, features=np.asarray(values, dtype=np.float32), actions=np.asarray(actions, dtype=np.float32))
    print(f'Saved {len(values)} public demonstration samples to {args.output}', flush=True)

"""Small CPU feature baseline; use the same preprocessing for demos and deployment."""
import io
import base64
import numpy as np
from PIL import Image


def image_feature(image):
    return np.asarray(image.convert('L').resize((16, 16)), dtype=np.float32).reshape(-1) / 255


def features(images, eef, gripper):
    return np.concatenate([*(image_feature(image) for image in images),
                           np.asarray(eef, dtype=np.float32) * 10,
                           np.asarray(gripper, dtype=np.float32) * 10])


def observation_feature(observation):
    images = [Image.open(io.BytesIO(base64.b64decode(observation['images_jpeg_base64'][name])))
              for name in ('agentview', 'robot0_eye_in_hand')]
    return features(images, observation['eef_position'], observation['gripper_position'])

"""MineDojo 0.1 actions using the frozen public craft/smelt catalogue."""
import json
from pathlib import Path

CRAFT_ITEMS = json.loads(Path(__file__).with_name('action_items.json').read_text())['craft_smelt_items']


def craft_action(observation, item):
    """Request crafting/smelting; the environment checks ingredients and tools."""
    action = list(observation['noop_action'])
    action[5], action[6] = 4, CRAFT_ITEMS.index(item)
    return action


def slot_action(observation, operation, slot):
    """Equip/place/destroy the given native inventory slot (0..35)."""
    if type(slot) is not int or not 0 <= slot < 36:
        raise ValueError('inventory slot must be an integer in 0..35')
    operation_id = {'equip': 5, 'place': 6, 'destroy': 7}[operation]
    action = list(observation['noop_action'])
    action[5], action[7] = operation_id, slot
    return action

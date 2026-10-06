"""Published task families; callers supply practice or frozen judge seeds."""
from __future__ import annotations


def minecraft_scenes(seeds=(42, 43, 44)):
    if len(seeds) != 3:
        raise ValueError("Minecraft needs three independent world seeds")
    tiers = [
        ("woodcraft", "入门：木制工具链", "beginner", 600, ["log", "planks", "crafting_table"]),
        ("stone-tools", "进阶：石器工具链", "intermediate", 1500,
         ["wooden_pickaxe", "cobblestone", "stone_pickaxe", "furnace"]),
        ("iron-age", "挑战：铁器工具链", "challenge", 3000, ["iron_ore", "iron_ingot", "iron_pickaxe"]),
    ]
    return [{"id": identifier, "label": label, "difficulty": difficulty,
             "task_id": "open-ended", "world_seed": seed, "max_steps": steps,
             "goals": goals, "initial_inventory": []}
            for seed, (identifier, label, difficulty, steps, goals) in zip(seeds, tiers)]


# Task IDs refer to upstream's task_order_index=0, not a shuffled task order.
LIBERO_TASKS = [
    ("libero_spatial", 2, "pick_up_the_black_bowl_from_table_center_and_place_it_on_the_plate",
     "抓取放置：黑碗放到盘子", "beginner", 600),
    ("libero_object", 0, "pick_up_the_alphabet_soup_and_place_it_in_the_basket",
     "物体操作：字母汤罐放入篮子", "intermediate", 600),
    ("libero_10", 0, "LIVING_ROOM_SCENE2_put_both_the_alphabet_soup_and_the_tomato_sauce_in_the_basket",
     "组合任务：两件物品放入篮子", "challenge", 1000),
]


def libero_scenes(init_state_ids=(0, 1, 2), seeds=(42, 43, 44)):
    if len(init_state_ids) != 3 or len(seeds) != 3:
        raise ValueError("LIBERO needs three initial states and seeds")
    return [{"id": f"libero-{index + 1}", "label": label, "difficulty": difficulty,
             "suite": suite, "task_id": task_id, "task_name": task_name,
             "init_state_id": state, "seed": seed, "max_steps": steps}
            for index, (state, seed, (suite, task_id, task_name, label, difficulty, steps))
            in enumerate(zip(init_state_ids, seeds, LIBERO_TASKS))]

"""Deterministic demographic sampling for agent profiles.

These priors are defaults only. An event can override them in ``event.json``
under ``demographics.age_bands`` / ``demographics.gender_weights``.
"""

import random

# (lower age, upper age, weight)
AGE_BANDS = [
    (18, 29, 0.42),
    (30, 49, 0.32),
    (50, 64, 0.19),
    (65, 75, 0.07),
]

GENDER_WEIGHTS = [("Male", 0.63), ("Female", 0.37)]


def sample_age_gender(uid: int, age_bands=None, gender_weights=None) -> tuple:
    """Sample age/gender deterministically from ``uid``.

    The same uid always yields the same result, so re-running a pipeline does
    not change the agent population.
    """
    rng = random.Random(uid * 7919)
    bands = age_bands or AGE_BANDS
    genders = gender_weights or GENDER_WEIGHTS
    band = rng.choices(bands, weights=[b[2] for b in bands], k=1)[0]
    age = rng.randint(int(band[0]), int(band[1]))
    gender = rng.choices(genders, weights=[g[1] for g in genders], k=1)[0][0]
    return age, gender

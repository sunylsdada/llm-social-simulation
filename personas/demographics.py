"""网民画像的确定性人口学采样。

这里只是默认先验；事件可以在 ``event.json`` 的
``demographics.age_bands`` / ``demographics.gender_weights`` 中覆盖。
"""

import random

# (年龄下限, 年龄上限, 权重)
AGE_BANDS = [
    (18, 29, 0.42),
    (30, 49, 0.32),
    (50, 64, 0.19),
    (65, 75, 0.07),
]

GENDER_WEIGHTS = [("Male", 0.63), ("Female", 0.37)]


def sample_age_gender(uid: int, age_bands=None, gender_weights=None) -> tuple:
    """根据 ``uid`` 确定性采样年龄/性别。

    同一个 uid 永远得到相同结果，重跑流水线不会改变网民人口构成。
    """
    rng = random.Random(uid * 7919)
    bands = age_bands or AGE_BANDS
    genders = gender_weights or GENDER_WEIGHTS
    band = rng.choices(bands, weights=[b[2] for b in bands], k=1)[0]
    age = rng.randint(int(band[0]), int(band[1]))
    gender = rng.choices(genders, weights=[g[1] for g in genders], k=1)[0][0]
    return age, gender

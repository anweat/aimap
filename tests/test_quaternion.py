"""四元数核心运算测试。"""
import math
import random

import pytest

from app.quaternion.core import (
    Projector,
    Quaternion,
    axis_angle_for_branch,
    quaternion_from_hash,
)


def test_identity_norm():
    q = Quaternion.identity()
    assert q.norm() == pytest.approx(1.0)


def test_random_unit_on_sphere():
    rng = random.Random(7)
    for _ in range(200):
        q = Quaternion.random_unit(rng)
        assert q.norm() == pytest.approx(1.0, abs=1e-9)


def test_axis_angle_rotation():
    q = Quaternion.from_axis_angle((0, 0, 1), math.pi)  # 绕 z 轴 180°
    assert q.norm() == pytest.approx(1.0)
    assert q.w == pytest.approx(0.0, abs=1e-9)
    assert q.z == pytest.approx(1.0, abs=1e-9)


def test_hamilton_product_identity():
    a = Quaternion.from_axis_angle((1, 0, 0), 0.5)
    b = Quaternion.from_axis_angle((0, 1, 0), 0.3)
    c = a * b
    assert c.norm() == pytest.approx(1.0, abs=1e-9)


def test_angle_to_self_zero():
    q = Quaternion.from_axis_angle((1, 1, 1), 0.7)
    assert q.angle_to(q) == pytest.approx(0.0, abs=1e-9)


def test_angle_to_opposite():
    q = Quaternion.from_axis_angle((0, 1, 0), 0.9)
    assert q.angle_to(Quaternion(-q.w, -q.x, -q.y, -q.z)) == pytest.approx(0.0, abs=1e-9)


def test_slerp_endpoints():
    a = Quaternion.from_axis_angle((1, 0, 0), 0.2)
    b = Quaternion.from_axis_angle((1, 0, 0), 1.4)
    assert a.slerp(b, 0.0).angle_to(a) == pytest.approx(0.0, abs=1e-6)
    assert a.slerp(b, 1.0).angle_to(b) == pytest.approx(0.0, abs=1e-6)
    mid = a.slerp(b, 0.5)
    assert mid.norm() == pytest.approx(1.0, abs=1e-9)


def test_projector_center():
    p = Projector(scale=1.0)
    assert p.project(Quaternion.identity()) == (0.0, 0.0, 0.0)


def test_projector_pure_vector():
    p = Projector(scale=2.0)
    # 纯向量四元数 (0,1,0,0) → x = 2/(1-0)*1 = 2
    x, y, z = p.project(Quaternion(0.0, 1.0, 0.0, 0.0))
    assert x == pytest.approx(2.0)
    assert y == pytest.approx(0.0)
    assert z == pytest.approx(0.0)


def test_quaternion_from_hash_deterministic():
    a1 = quaternion_from_hash("attention is all you need")
    a2 = quaternion_from_hash("attention is all you need")
    b = quaternion_from_hash("different paper title")
    assert a1 == a2
    assert a1 != b


def test_branch_axes_distinct():
    axes = [axis_angle_for_branch(i, 6)[0] for i in range(6)]
    for i in range(6):
        for j in range(i + 1, 6):
            assert axes[i] != axes[j]

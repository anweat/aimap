"""四元数模块:领域地图的数学内核。

设计:
  - 领域树每个节点与每篇论文都映射为**单位四元数**(S3 球面上的点),
    即 q = w + xi + yj + zk,‖q‖ = 1;
  - 树结构通过「父四元数 × 子旋转增量」逐层嵌入 S3 —— 树父子关系
    天然对应四元数乘法(旋转复合),兄弟节点用不同旋转轴/角区分;
  - 论文位置 = 锚定领域节点的四元数 × 论文特征扰动,再归一化,
    保证同类论文聚簇、不同领域分离;
  - 4D→3D 用立体投影(stereographic projection)供前端 three.js 渲染。

本模块为零依赖纯 Python 实现,便于测试与审计。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Quaternion:
    """四元数 w + xi + yj + zk。"""

    w: float
    x: float
    y: float
    z: float

    # -- 构造 ------------------------------------------------------------
    @classmethod
    def identity(cls) -> "Quaternion":
        return cls(1.0, 0.0, 0.0, 0.0)

    @classmethod
    def from_axis_angle(cls, axis: tuple[float, float, float], angle: float) -> "Quaternion":
        """由旋转轴(单位向量)与旋转角(弧度)构造单位四元数。"""
        ax, ay, az = axis
        norm = math.sqrt(ax * ax + ay * ay + az * az)
        if norm < 1e-12:
            return cls.identity()
        ax, ay, az = ax / norm, ay / norm, az / norm
        half = angle / 2.0
        s = math.sin(half)
        return cls(math.cos(half), ax * s, ay * s, az * s)

    @classmethod
    def random_unit(cls, rng: random.Random | None = None) -> "Quaternion":
        """在 S3 上均匀随机采样(单位四元数)。"""
        r = rng or random
        u1, u2, u3 = r.random(), r.random(), r.random()
        w = math.sqrt(1 - u1) * math.sin(2 * math.pi * u2)
        x = math.sqrt(1 - u1) * math.cos(2 * math.pi * u2)
        y = math.sqrt(u1) * math.sin(2 * math.pi * u3)
        z = math.sqrt(u1) * math.cos(2 * math.pi * u3)
        return cls(w, x, y, z)

    # -- 基本运算 ----------------------------------------------------------
    def norm(self) -> float:
        return math.sqrt(self.w * self.w + self.x * self.x + self.y * self.y + self.z * self.z)

    def conjugate(self) -> "Quaternion":
        return Quaternion(self.w, -self.x, -self.y, -self.z)

    def normalized(self) -> "Quaternion":
        n = self.norm()
        if n < 1e-12:
            return self.identity()
        return Quaternion(self.w / n, self.x / n, self.y / n, self.z / n)

    def __mul__(self, other: "Quaternion | int | float") -> "Quaternion":
        """哈密顿积(旋转复合:先 other 后 self);标量乘法亦支持。"""
        if isinstance(other, (int, float)):
            return Quaternion(self.w * other, self.x * other, self.y * other, self.z * other)
        a1, b1, c1, d1 = self.w, self.x, self.y, self.z
        a2, b2, c2, d2 = other.w, other.x, other.y, other.z
        return Quaternion(
            a1 * a2 - b1 * b2 - c1 * c2 - d1 * d2,
            a1 * b2 + b1 * a2 + c1 * d2 - d1 * c2,
            a1 * c2 - b1 * d2 + c1 * a2 + d1 * b2,
            a1 * d2 + b1 * c2 - c1 * b2 + d1 * a2,
        )

    def __add__(self, other: "Quaternion") -> "Quaternion":
        return Quaternion(
            self.w + other.w, self.x + other.x, self.y + other.y, self.z + other.z
        )

    def __rmul__(self, scalar: float) -> "Quaternion":
        return Quaternion(self.w * scalar, self.x * scalar, self.y * scalar, self.z * scalar)

    # -- 几何 ------------------------------------------------------------
    def dot(self, other: "Quaternion") -> float:
        return self.w * other.w + self.x * other.x + self.y * other.y + self.z * other.z

    def angle_to(self, other: "Quaternion") -> float:
        """测地距离(弧度):两单位四元数在 S3 上的夹角 ∈ [0, π/2]。
        q 与 -q 表示同一旋转,取绝对值保证距离等价(arccos(|dot|))。"""
        d = abs(self.dot(other))
        return math.acos(min(1.0, d))

    def slerp(self, other: "Quaternion", t: float) -> "Quaternion":
        """球面线性插值(用于动画/过渡)。"""
        d = self.dot(other)
        # 处理 q 与 -q 等价与夹角为 0 的情况
        if d < 0:
            other = Quaternion(-other.w, -other.x, -other.y, -other.z)
            d = -d
        if d > 0.9995:
            return (self * (1 - t) + other * t).normalized()
        theta = math.acos(min(1.0, d))
        sin_theta = math.sin(theta)
        if sin_theta < 1e-12:
            return self
        w1 = math.sin((1 - t) * theta) / sin_theta
        w2 = math.sin(t * theta) / sin_theta
        return (self * w1 + other * w2).normalized()

    # -- 投影 ------------------------------------------------------------
    def to_3d(self, projector: "Projector | None" = None) -> tuple[float, float, float]:
        """4D→3D 投影(默认立体投影,投影中心为恒等四元数)。"""
        if projector is None:
            projector = Projector()
        return projector.project(self)

    def to_list(self) -> list[float]:
        return [self.w, self.x, self.y, self.z]

    def __repr__(self) -> str:  # pragma: no cover
        return f"Q({self.w:.4f}, {self.x:.4f}, {self.y:.4f}, {self.z:.4f})"


class Projector:
    """4D→3D 投影器。

    立体投影(Stereographic Projection)以恒等四元数 (1,0,0,0) 为投影中心,
    将 S3 上除中心外的点映到 R3,保角且视觉上中心区域展开自然,
    适合把「根领域在中心、子领域向外扩散」的树结构直观呈现。
    """

    def __init__(self, scale: float = 1.0, center: Quaternion = Quaternion.identity()):
        self.scale = scale
        self.center = center

    def project(self, q: Quaternion) -> tuple[float, float, float]:
        # 相对投影中心旋转到局部坐标系
        local = self.center.conjugate() * q
        denom = 1.0 - local.w
        if abs(denom) < 1e-9:  # 投影中心本身 → 映射到远点,退化为原点
            return (0.0, 0.0, 0.0)
        s = self.scale / denom
        return (local.x * s, local.y * s, local.z * s)


def quaternion_from_hash(seed_text: str, rng: random.Random | None = None) -> Quaternion:
    """由字符串确定性生成单位四元数(用于论文特征扰动等)。"""
    r = rng or random.Random(seed_text)
    return Quaternion.random_unit(r)


def axis_angle_for_branch(index: int, total: int, spread: float = math.pi / 2) -> tuple[tuple[float, float, float], float]:
    """为树的第 index 个兄弟节点生成旋转轴与角。

    轴在 XY 平面均匀铺开,角度随层数增大,保证同一父节点下子节点
    方向可区分且整体向外扩散。
    """
    angle_xy = 2 * math.pi * index / max(total, 1)
    axis = (math.cos(angle_xy), math.sin(angle_xy), 0.618 * math.sin(angle_xy * 1.7))
    return axis, spread

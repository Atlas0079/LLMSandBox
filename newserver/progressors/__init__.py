"""
Task progressors（任务推进器）。

推进器负责“算法”；Task/Recipe 负责“参数”。
"""

from .base import Progressor
from .registry import get_progressor, register_progressor
from .linear import LinearProgressor

# 默认注册
register_progressor(LinearProgressor())

__all__ = [
	"Progressor",
	"get_progressor",
	"register_progressor",
	"LinearProgressor",
]


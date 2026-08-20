"""Shared Pydantic base model for configuration DTOs.

This module intentionally lives outside the ``nanobot.config`` package so
runtime modules can define local config DTOs without importing the full root
configuration schema.
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


# 共享 DTO 基类统一别名规则，使文件配置、API 载荷与 Python 属性可以并存。
class Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

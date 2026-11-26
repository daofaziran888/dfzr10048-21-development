"""业务路由汇总：专用工作台路由先注册，通用资源路由居中，兼容兜底最后。"""
from __future__ import annotations

from app.routers import compat, resource, workbench

ROUTERS = [workbench, resource, compat]

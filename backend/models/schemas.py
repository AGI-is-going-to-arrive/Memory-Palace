from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class DiffRequest(BaseModel):
    """文本diff请求"""
    text_a: str = Field(..., description="旧文本")
    text_b: str = Field(..., description="新文本")


class DiffResponse(BaseModel):
    """文本diff响应"""
    diff_html: str = Field(..., description="HTML格式的diff")
    diff_unified: str = Field(..., description="unified格式的diff")
    summary: str = Field(..., description="变化摘要")


# ============ 回滚相关模型 (Rollback/Review) ============

class SessionInfo(BaseModel):
    """Session 元信息"""
    session_id: str
    created_at: Optional[str] = None
    resource_count: int


class SnapshotInfo(BaseModel):
    """快照元信息"""
    resource_id: str
    resource_type: str  # 'path' or 'memory'
    snapshot_time: str
    operation_type: Optional[str] = "modify"
    uri: Optional[str] = None  # Display URI (for memory snapshots where resource_id is memory:{id})


class SnapshotDetail(BaseModel):
    """快照详细数据"""
    resource_id: str
    resource_type: str
    snapshot_time: str
    data: Dict[str, Any]


class ResourceDiff(BaseModel):
    """资源的快照与当前状态对比"""
    resource_id: str
    resource_type: str
    snapshot_time: str
    snapshot_data: Dict[str, Any]  # 快照时的完整状态 (content, priority, disclosure)
    current_data: Dict[str, Any]   # 当前的完整状态
    diff_unified: str
    diff_summary: str
    has_changes: bool


class RollbackRequest(BaseModel):
    """回滚请求"""
    task_description: Optional[str] = Field(
        "Rollback to snapshot by human",
        description="任务描述（记录在版本历史中）"
    )


class RollbackResponse(BaseModel):
    """回滚响应"""
    resource_id: str
    resource_type: str
    success: bool
    message: str
    new_version: Optional[int] = None

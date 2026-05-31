"""Agent 状态定义"""


class WorkflowCancelledError(Exception):
    """工作流被用户取消时抛出"""
    pass

import subprocess
import sys
from langgraph.prebuilt import ToolNode
import os

from tools import tools
from config import WORKSPACE_DIR
from state import AgentState


# ==========================================
# 6. 工具执行节点 (ToolNode)
# ==========================================
# LangGraph 提供了一个开箱即用的 ToolNode，它会自动解析 Coder 返回的 tool_calls
# 并执行我们前面定义的 write_python_file 函数，然后把结果作为 ToolMessage 返回。
tool_node = ToolNode(tools)


# ==========================================
# 7. 沙盒测试节点 (Sandbox Node)
# ==========================================

def sandbox_node(state: AgentState):
    """
    Sandbox 节点：负责在本地隔离环境（workspace 目录）中执行代码，并捕获测试结果。
    它不调用大模型，纯粹是一个执行器引擎。
    """
    print("🛠️ [Sandbox] 正在启动安全沙盒运行代码...")

    active_files = state.get("active_files", [])
    retry_count = state.get("retry_count", 0)

    # 1. 寻找需要运行的主入口文件。简单起见，我们运行 active_files 里的第一个 .py 文件
    target_file = next((f for f in active_files if f.endswith(".py")), None)

    if not target_file:
        print("⚠️ [Sandbox] 没有找到需要运行的 Python 文件，跳过测试。")
        return {"error_trace": ""}

    # 确保文件路径是安全的
    safe_filename = os.path.basename(target_file)
    file_path = os.path.join(WORKSPACE_DIR, safe_filename)

    if not os.path.exists(file_path):
        return {
            "error_trace": f"执行失败：文件未找到 {safe_filename}",
            "retry_count": retry_count + 1
        }

    # 2. 启动子进程执行代码 (核心沙盒逻辑)
    try:
        # sys.executable 指代当前运行你这个程序的 python 解释器
        print(f"▶️ [Sandbox] 执行命令: python {safe_filename}")
        result = subprocess.run(
            [sys.executable, file_path],
            capture_output=True,  # 捕获标准输出和标准错误
            text=True,  # 以字符串形式返回，而不是 bytes
            timeout=10,  # 🚨 必须设置超时！防止死循环导致整个 Agent 卡死
            cwd=WORKSPACE_DIR  # 🚨 将工作目录限制在 workspace 里
        )

        # 3. 结果判定
        if result.returncode == 0:
            print(f"✅ [Sandbox] {safe_filename} 运行成功！")
            # 运行成功，清空报错信息
            return {"error_trace": ""}
        else:
            print(f"❌ [Sandbox] 运行失败 (Return Code: {result.returncode})")

            # 合并 stdout 和 stderr，有时候报错会打印在 stdout 里
            error_output = f"【标准输出】\n{result.stdout}\n【标准错误】\n{result.stderr}"

            # 🚨 极端 Token 裁剪：如果报错栈太长，只保留最后的 1500 个字符
            # 因为真正导致崩溃的原因往往在错误栈的最下面
            if len(error_output) > 1500:
                error_output = "...[前序报错已截断]...\n" + error_output[-1500:]

            return {
                "error_trace": error_output,
                "retry_count": retry_count + 1
            }

    except subprocess.TimeoutExpired:
        print("⏱️ [Sandbox] 代码执行超时！")
        return {
            "error_trace": "执行超时 (Timeout 10s)。请检查是否有 `while True` 死循环或未释放的阻塞操作。",
            "retry_count": retry_count + 1
        }
    except Exception as e:
        print(f"⚠️ [Sandbox] 沙盒系统级异常: {e}")
        return {
            "error_trace": f"沙盒底层执行错误: {str(e)}",
            "retry_count": retry_count + 1
        }
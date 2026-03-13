# import subprocess
# import sys
# from langgraph.prebuilt import ToolNode
# import os
#
# from src.tools.file_tools import tools
# from src.core.config import WORKSPACE_DIR
# from src.core.state import AgentState
#
#
# # ==========================================
# # 6. 工具执行节点 (ToolNode)
# # ==========================================
# # LangGraph 提供了一个开箱即用的 ToolNode，它会自动解析 Coder 返回的 tool_calls
# # 并执行我们前面定义的 write_python_file 函数，然后把结果作为 ToolMessage 返回。
# tool_node = ToolNode(tools)
#
#
# # ==========================================
# # 7. 沙盒测试节点 (Sandbox Node)
# # ==========================================
#
# def sandbox_node(state: AgentState):
#     """
#     Sandbox 节点：负责在本地隔离环境（workspace 目录）中执行代码，并捕获测试结果。
#     它不调用大模型，纯粹是一个执行器引擎。
#     """
#     print("🛠️ [Sandbox] 正在启动安全沙盒运行代码...")
#
#     active_files = state.get("active_files", [])
#     retry_count = state.get("retry_count", 0)
#
#     # 1. 寻找需要运行的主入口文件。简单起见，我们运行 active_files 里的第一个 .py 文件
#     target_file = next((f for f in active_files if f.endswith(".py")), None)
#
#     if not target_file:
#         print("⚠️ [Sandbox] 没有找到需要运行的 Python 文件，跳过测试。")
#         return {"error_trace": ""}
#
#     # 确保文件路径是安全的
#     safe_filename = os.path.basename(target_file)
#     file_path = os.path.join(WORKSPACE_DIR, safe_filename)
#
#     if not os.path.exists(file_path):
#         return {
#             "error_trace": f"执行失败：文件未找到 {safe_filename}",
#             "retry_count": retry_count + 1
#         }
#
#     # 2. 启动子进程执行代码 (核心沙盒逻辑)
#     try:
#         # sys.executable 指代当前运行你这个程序的 python 解释器
#         print(f"▶️ [Sandbox] 执行命令: python {safe_filename}")
#         result = subprocess.run(
#             [sys.executable, file_path],
#             capture_output=True,  # 捕获标准输出和标准错误
#             text=True,  # 以字符串形式返回，而不是 bytes
#             timeout=10,  # 🚨 必须设置超时！防止死循环导致整个 Agent 卡死
#             cwd=WORKSPACE_DIR  # 🚨 将工作目录限制在 workspace 里
#         )
#
#         # 3. 结果判定
#         if result.returncode == 0:
#             print(f"✅ [Sandbox] {safe_filename} 运行成功！")
#             # 运行成功，清空报错信息
#             return {"error_trace": ""}
#         else:
#             print(f"❌ [Sandbox] 运行失败 (Return Code: {result.returncode})")
#
#             # 合并 stdout 和 stderr，有时候报错会打印在 stdout 里
#             error_output = f"【标准输出】\n{result.stdout}\n【标准错误】\n{result.stderr}"
#
#             # 🚨 极端 Token 裁剪：如果报错栈太长，只保留最后的 1500 个字符
#             # 因为真正导致崩溃的原因往往在错误栈的最下面
#             if len(error_output) > 1500:
#                 error_output = "...[前序报错已截断]...\n" + error_output[-1500:]
#
#             return {
#                 "error_trace": error_output,
#                 "retry_count": retry_count + 1
#             }
#
#     except subprocess.TimeoutExpired:
#         print("⏱️ [Sandbox] 代码执行超时！")
#         return {
#             "error_trace": "执行超时 (Timeout 10s)。请检查是否有 `while True` 死循环或未释放的阻塞操作。",
#             "retry_count": retry_count + 1
#         }
#     except Exception as e:
#         print(f"⚠️ [Sandbox] 沙盒系统级异常: {e}")
#         return {
#             "error_trace": f"沙盒底层执行错误: {str(e)}",
#             "retry_count": retry_count + 1
#         }


import os
import docker
from langgraph.prebuilt import ToolNode

from src.tools.file_tools import tools
from src.core.config import WORKSPACE_DIR
from src.core.state import AgentState

# 工具执行节点保持不变
tool_node = ToolNode(tools)

# ==========================================
# 🌟 初始化 Docker 客户端
# ==========================================
try:
    docker_client = docker.from_env()
except Exception as e:
    print(f"⚠️ [系统警告] 无法连接到 Docker 守护进程，请确认 Docker 已启动: {e}")
    docker_client = None


def sandbox_node(state: AgentState):
    """
    Sandbox 节点：负责在 Docker 隔离环境中执行代码，并捕获测试结果。
    """
    print("🛠️ [Sandbox] 正在启动 Docker 安全沙盒运行代码...")

    active_files = state.get("active_files", [])
    retry_count = state.get("retry_count", 0)

    target_file = next((f for f in active_files if f.endswith(".py")), None)

    if not target_file:
        print("⚠️ [Sandbox] 没有找到需要运行的 Python 文件，跳过测试。")
        return {"error_trace": ""}

    safe_filename = os.path.basename(target_file)
    file_path = os.path.join(WORKSPACE_DIR, safe_filename)

    if not os.path.exists(file_path):
        return {
            "error_trace": f"执行失败：文件未找到 {safe_filename}",
            "retry_count": retry_count + 1
        }

    # 如果没有 Docker 环境，触发降级保护逻辑 (可选)
    if not docker_client:
        return {
            "error_trace": "沙盒环境未就绪：Docker 客户端未启动，出于安全考虑拒绝执行代码。",
            "retry_count": retry_count + 1
        }

    # ==========================================
    # 🌟 核心改造：Docker 容器化安全执行
    # ==========================================
    try:
        print(f"🐳 [Sandbox] 拉起容器执行: python {safe_filename}")

        # 启动一个临时的轻量级 Python 容器
        logs = docker_client.containers.run(
            image="python:3.10-slim",  # 使用官方轻量级镜像
            command=f"python {safe_filename}",  # 执行目标文件
            volumes={
                WORKSPACE_DIR: {'bind': '/workspace', 'mode': 'rw'}  # 将工作区挂载到容器内
            },
            working_dir="/workspace",  # 设置工作目录
            mem_limit="256m",  # 🚨 安全限制：最大内存 256MB
            network_disabled=True,  # 🚨 安全限制：断开网络，防止恶意下载/外发数据
            remove=True,  # 🚨 运行完毕后自动销毁容器，不留垃圾
            stdout=True,
            stderr=True
        )

        print(f"✅ [Sandbox] {safe_filename} 运行成功！(Docker 隔离环境)")
        return {"error_trace": ""}

    except docker.errors.ContainerError as e:
        # 当容器内命令退出码不为 0 时触发
        print(f"❌ [Sandbox] 运行失败 (Exit Code: {e.exit_status})")

        # 获取容器的标准输出和错误
        error_output = e.stderr.decode('utf-8') if e.stderr else ""
        error_output += "\n" + (e.stdout.decode('utf-8') if e.stdout else "")

        # Token 裁剪：保留核心报错
        if len(error_output) > 1500:
            error_output = "...[前序报错已截断]...\n" + error_output[-1500:]

        return {
            "error_trace": f"【沙盒报错】\n{error_output.strip()}",
            "retry_count": retry_count + 1
        }

    except docker.errors.ImageNotFound:
        print("📥 [Sandbox] 首次运行正在拉取 python:3.10-slim 镜像，请稍候...")
        return {
            "error_trace": "系统正在初始化 Docker 镜像，请重试。",
            "retry_count": retry_count  # 不计入失败次数
        }
    except Exception as e:
        print(f"⚠️ [Sandbox] Docker 底层执行异常: {e}")
        return {
            "error_trace": f"沙盒底层执行错误: {str(e)}",
            "retry_count": retry_count + 1
        }
import os
import docker
from langgraph.prebuilt import ToolNode

from src.tools.file_tools import tools
from src.core.config import WORKSPACE_DIR
from src.core.state import AgentState

tool_node = ToolNode(tools)

try:
    docker_client = docker.from_env()
except Exception as e:
    print(f"[系统警告] 无法连接到 Docker 守护进程，请确认 Docker 已启动: {e}")
    docker_client = None


def sandbox_node(state: AgentState):
    """
    Sandbox 节点：负责在 Docker 隔离环境中执行代码，并捕获测试结果。
    """
    print("[Sandbox] 正在启动 Docker 安全沙盒运行代码...")

    active_files = state.get("active_files", [])
    retry_count = state.get("retry_count", 0)

    # ==========================================
    # 🌟 核心修改：智能判断运行命令
    # ==========================================
    # 1. 优先寻找测试文件 (通常以 test_ 开头或 _test.py 结尾)
    test_file = next((f for f in active_files if
                      os.path.basename(f).startswith("test_") or os.path.basename(f).endswith("_test.py")), None)

    if test_file:
        safe_filename = os.path.basename(test_file)
        # 使用 unittest 模块运行测试，这会自动执行文件内的所有 Test 类
        run_command = f"python {safe_filename}"
        print(f"[Sandbox] 检测到测试文件，将执行测试: {run_command}")
    else:
        # 2. 如果没有测试文件，退化为寻找普通 Python 文件
        target_file = next((f for f in active_files if f.endswith(".py")), None)

        if not target_file:
            print("[Sandbox] 没有找到需要运行的 Python 文件，跳过测试。")
            return {"error_trace": ""}

        safe_filename = os.path.basename(target_file)
        run_command = f"python {safe_filename}"
        print(f"[Sandbox] 未检测到测试文件，将执行普通脚本: {run_command}")

    # 校验最终决定执行的文件是否存在
    file_path = os.path.join(WORKSPACE_DIR, safe_filename)
    if not os.path.exists(file_path):
        return {
            "error_trace": f"执行失败：文件未找到 {safe_filename}",
            "retry_count": retry_count + 1
        }

    if not docker_client:
        return {
            "error_trace": "沙盒环境未就绪：Docker 客户端未启动，出于安全考虑拒绝执行代码。",
            "retry_count": retry_count + 1
        }

    try:
        print(f"[Sandbox] 拉起容器执行: {run_command}")

        logs = docker_client.containers.run(
            image="python:3.10-slim",
            command=run_command,  # 🌟 使用动态生成的命令
            volumes={
                WORKSPACE_DIR: {'bind': '/workspace', 'mode': 'rw'}
            },
            working_dir="/workspace",
            mem_limit="256m",
            network_disabled=True,
            remove=True,
            stdout=True,
            stderr=True
        )

        print(f"[Sandbox] {safe_filename} 运行成功！(Docker 隔离环境)")
        return {"error_trace": ""}

    except docker.errors.ContainerError as e:
        print(f"[Sandbox] 运行失败 (Exit Code: {e.exit_status})")
        error_output = e.stderr.decode('utf-8') if e.stderr else ""
        error_output += "\n" + (e.stdout.decode('utf-8') if e.stdout else "")

        if len(error_output) > 1500:
            error_output = "...[前序报错已截断]...\n" + error_output[-1500:]

        return {
            "error_trace": f"【沙盒报错】\n{error_output.strip()}",
            "retry_count": retry_count + 1
        }

    except docker.errors.ImageNotFound:
        print("[Sandbox] 首次运行正在拉取 python:3.10-slim 镜像，请稍候...")
        return {
            "error_trace": "系统正在初始化 Docker 镜像，请重试。",
            "retry_count": retry_count
        }
    except Exception as e:
        print(f"[Sandbox] Docker 底层执行异常: {e}")
        return {
            "error_trace": f"沙盒底层执行错误: {str(e)}",
            "retry_count": retry_count + 1
        }
import os
import docker
from langgraph.prebuilt import ToolNode

from src.tools.file_tools import tools
from src.core.config import WORKSPACE_DIR
from src.core.state import AgentState

# 工具执行节点保持不变
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

    target_file = next((f for f in active_files if f.endswith(".py")), None)

    if not target_file:
        print("[Sandbox] 没有找到需要运行的 Python 文件，跳过测试。")
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

    try:
        print(f"[Sandbox] 拉起容器执行: python {safe_filename}")

        # 启动一个临时的轻量级 Python 容器
        logs = docker_client.containers.run(
            image="python:3.10-slim",  # 使用官方轻量级镜像
            command=f"python {safe_filename}",  # 执行目标文件
            volumes={
                WORKSPACE_DIR: {'bind': '/workspace', 'mode': 'rw'}  # 将工作区挂载到容器内
            },
            working_dir="/workspace",       # 设置工作目录
            mem_limit="256m",               # 安全限制：最大内存 256MB
            network_disabled=True,          # 安全限制：断开网络，防止恶意下载/外发数据
            remove=True,                    # 运行完毕后自动销毁容器，不留垃圾
            stdout=True,
            stderr=True
        )

        print(f"[Sandbox] {safe_filename} 运行成功！(Docker 隔离环境)")
        return {"error_trace": ""}

    except docker.errors.ContainerError as e:
        # 当容器内命令退出码不为 0 时触发
        print(f"[Sandbox] 运行失败 (Exit Code: {e.exit_status})")

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
        print("[Sandbox] 首次运行正在拉取 python:3.10-slim 镜像，请稍候...")
        return {
            "error_trace": "系统正在初始化 Docker 镜像，请重试。",
            "retry_count": retry_count  # 不计入失败次数
        }
    except Exception as e:
        print(f"[Sandbox] Docker 底层执行异常: {e}")
        return {
            "error_trace": f"沙盒底层执行错误: {str(e)}",
            "retry_count": retry_count + 1
        }
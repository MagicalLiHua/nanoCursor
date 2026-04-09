import os
import json
import docker
from langgraph.prebuilt import ToolNode

from src.tools.file_tools import tools
from src.core.config import WORKSPACE_DIR, SANDBOX_IMAGE, SANDBOX_MEM_LIMIT, SANDBOX_CONTAINER_STARTUP_TIMEOUT, FUZZY_MATCH_THRESHOLD
from src.core.state import AgentState

tool_node = ToolNode(tools)

try:
    docker_client = docker.from_env()
except Exception as e:
    print(f"[系统警告] 无法连接到 Docker 守护进程，请确认 Docker 已启动: {e}")
    docker_client = None


# ---------------------------------------------------------------
# 测试文件发现
# ---------------------------------------------------------------

def _discover_test_files(workspace_dir: str) -> list[str]:
    """扫描工作区中所有测试文件（test_*.py 和 *_test.py）。"""
    test_files = []
    for root, dirs, files in os.walk(workspace_dir):
        # 跳过隐藏目录和备份/快照目录
        dirs[:] = [d for d in dirs if not d.startswith((".backups", ".snapshots"))]
        for f in files:
            if f.startswith("test_") and f.endswith(".py"):
                test_files.append(os.path.join(root, f))
            elif f.endswith("_test.py"):
                test_files.append(os.path.join(root, f))
    return test_files


# ---------------------------------------------------------------
# Docker 运行器
# ---------------------------------------------------------------

def _sanitize_shell_arg(s: str) -> str:
    """Escape single quotes in a string so it is safe inside bash -c '...'."""
    return s.replace("'", "'\\''")


def _run_in_container(
    run_command: str,
    image: str = SANDBOX_IMAGE,
) -> tuple[bool, str]:
    """在 Docker 容器中执行命令。

    Returns:
        (success, output_or_error)
    """
    if not docker_client:
        return False, "沙盒环境未就绪：Docker 客户端未启动，出于安全考虑拒绝执行代码。"

    print(f"[Sandbox] 拉起容器执行: {run_command}")

    # 准备安装脚本：如果工作区有 requirements.txt，先 pip install
    install_cmd = ""
    if os.path.exists(os.path.join(WORKSPACE_DIR, "requirements.txt")):
        install_cmd = "pip install -r requirements.txt && "

    try:
        safe_install = _sanitize_shell_arg(install_cmd)
        safe_run = _sanitize_shell_arg(run_command)
        full_command = f"bash -c '{safe_install}{safe_run}'"
        container_timeout = int(os.environ.get("SANDBOX_TIMEOUT_SECONDS", "60"))

        # 使用 detach + wait(timeout) 模式，防止死循环容器永久挂起
        container = docker_client.containers.create(
            image=SANDBOX_IMAGE,
            command=full_command,
            volumes={
                WORKSPACE_DIR: {'bind': '/workspace', 'mode': 'rw'}
            },
            working_dir="/workspace",
            mem_limit=SANDBOX_MEM_LIMIT,
            network_disabled=True,
            auto_remove=True,
        )

        container.start()

        try:
            result = container.wait(timeout=container_timeout)
            exit_code = result.get("StatusCode", 1)
            logs = container.logs(stdout=True, stderr=True)

            if exit_code == 0:
                print(f"[Sandbox] 运行成功！(Docker 隔离环境)")
                return True, (logs.decode("utf-8") if logs else "")
            else:
                print(f"[Sandbox] 运行失败 (Exit Code: {exit_code})")
                error_output = logs.decode('utf-8') if logs else ""
                if len(error_output) > 1500:
                    error_output = "...[前序报错已截断]...\n" + error_output[-1500:]
                return False, error_output.strip()

        except Exception as wait_error:
            # 超时或其他等待异常：停止并清理容器
            print(f"[Sandbox] 执行超时或异常 ({container_timeout}s): {wait_error}")
            try:
                container.stop(timeout=SANDBOX_CONTAINER_STARTUP_TIMEOUT)
            except Exception:
                pass
            return False, f"沙盒执行超时 ({container_timeout}s)，可能存在死循环或长时间等待的代码。"

    except docker.errors.ImageNotFound:
        print("[Sandbox] 首次运行正在拉取 python:3.10-slim 镜像，请稍候...")
        return False, "系统正在初始化 Docker 镜像，请重试。"

    except Exception as e:
        print(f"[Sandbox] Docker 底层执行异常: {e}")
        return False, f"沙盒底层执行错误: {str(e)}"


# ---------------------------------------------------------------
# 测试执行策略
# ---------------------------------------------------------------

def _run_test_files_with_unittest(test_files: list[str]) -> tuple[bool, str]:
    """用 unittest 逐个运行多个测试文件，自动安装 pytest 依赖（如果有导入失败）。

    Returns:
        (all_passed, combined_output_or_error)
    """
    all_passed = True
    results = []

    # 先检测是否安装了 pytest（如果测试文件里有 import pytest）
    needs_pytest = False
    for f in test_files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                if "import pytest" in fh.read():
                    needs_pytest = True
                    break
        except (FileNotFoundError, PermissionError) as e:
            print(f"[Sandbox] 跳过无法读取的测试文件 {f}: {e}")
            continue

    for tf in test_files:
        safe_name = os.path.relpath(tf, WORKSPACE_DIR)
        prefix = f"python -m unittest {safe_name}"
        if needs_pytest:
            # 尝试用 pip 安装 pytest（容器内自动执行）
            prefix = f"pip install pytest -q && {prefix}"

        success, output = _run_in_container(f"{prefix} -v")
        rel = os.path.relpath(tf, WORKSPACE_DIR)
        if success:
            results.append(f"[PASS] {rel}")
        else:
            all_passed = False
            results.append(f"[FAIL] {rel}\n{output}")

    combined = "\n".join(results)
    return all_passed, combined


def _run_test_files_with_pytest(test_files: list[str]) -> tuple[bool, str]:
    """尝试用 pytest 运行测试（优先）；如果容器里没有 pytest，回退到 unittest。"""
    rel_paths = " ".join(os.path.relpath(f, WORKSPACE_DIR) for f in test_files)
    success, output = _run_in_container(f"pytest {rel_paths} -v")
    if not success and ("pytest" in output.lower() or "not found" in output.lower()):
        return _run_test_files_with_unittest(test_files)
    return success, output


# ---------------------------------------------------------------
# Sandbox 节点
# ---------------------------------------------------------------

def _parse_unittest_summary(output: str) -> dict:
    """从 unittest 输出中解析摘要，例如 'Ran 12 tests in 0.001s' 和 OK/FAILED。"""
    import re
    info = {"total": 0, "passed": 0, "failed": 0, "errors": 0}
    # 匹配 "Ran N tests in Xs"
    ran_match = re.search(r"Ran (\d+) tests?\s+in", output)
    if ran_match:
        info["total"] = int(ran_match.group(1))

    if "OK" in output and "FAILED" not in output:
        info["passed"] = info["total"]
        info["status"] = "OK"
    else:
        failed_match = re.search(r"FAILED\s+\((failures=(\d+),?\s*)?(errors=(\d+))?\.?", output)
        if failed_match:
            info["failed"] = int(failed_match.group(2) or 0)
            info["errors"] = int(failed_match.group(4) or 0)
        info["status"] = "FAILED"
    return info


def _parse_pytest_summary(output: str) -> dict:
    """从 pytest 输出中解析摘要。"""
    import re
    info = {"total": 0, "passed": 0, "failed": 0, "errors": 0}
    # 匹配 "passed, failed, error" pattern
    summary_match = re.search(r"=+ (\d+) passed(?:, (\d+) failed)?(?:, (\d+) error)?", output)
    if summary_match:
        info["passed"] = int(summary_match.group(1))
        info["failed"] = int(summary_match.group(2) or 0)
        info["errors"] = int(summary_match.group(3) or 0)
        info["total"] = info["passed"] + info["failed"] + info["errors"]
        info["status"] = "PASS" if info["failed"] == 0 and info["errors"] == 0 else "FAIL"
    else:
        info["status"] = "UNKNOWN"
    return info


def sandbox_node(state: AgentState):
    """
    Sandbox 节点：负责在 Docker 隔离环境中执行代码，智能发现测试文件，
    自动选择 pytest / unittest 运行测试，并输出结构化测试报告。
    """
    from src.core.metrics import metrics

    # 标记一次修复循环开始（在此记录而非路由函数中，避免重复计数）
    metrics.record_repair_cycle_start()

    print("[Sandbox] 正在启动 Docker 安全沙箱运行代码...")

    active_files = state.get("active_files", [])
    retry_count = state.get("retry_count", 0)

    # ==========================================
    # Step 1: 扫描工作区中所有测试文件
    # ==========================================
    test_files = _discover_test_files(WORKSPACE_DIR)

    if test_files:
        print(f"[Sandbox] 发现 {len(test_files)} 个测试文件:")
        for f in test_files:
            print(f"  - {os.path.relpath(f, WORKSPACE_DIR)}")
        test_report = f"测试文件 ({len(test_files)} 个):\n"
        for f in test_files:
            test_report += f"  - {os.path.relpath(f, WORKSPACE_DIR)}\n"
        # 优先用 pytest 运行
        success, output = _run_test_files_with_pytest(test_files)

        if success:
            test_report += f"\n结果: 全部通过\n{output}"
            print(f"[Sandbox] {test_report}")
            # 解析测试结果
            if "passed" in output:
                summary = _parse_pytest_summary(output)
            else:
                summary = _parse_unittest_summary(output)
            # 写测试结果文件（供 Reviewer 等使用）
            _write_test_result_json(summary, output[:5000], test_files)
            return {
                "error_trace": "",
                "retry_count": retry_count,
            }
        else:
            test_report += f"\n结果: 失败\n{output}"
            print(f"[Sandbox] {test_report}")
            error_output = output
            if len(error_output) > 2000:
                error_output = "...[前序报错已截断]...\n" + error_output[-2000:]
            # 即使是失败时也写入测试结果文件，保持数据一致性
            if "passed" in output:
                summary = _parse_pytest_summary(output)
            else:
                summary = _parse_unittest_summary(output)
            _write_test_result_json(summary, error_output[:5000], test_files)
            return {
                "error_trace": f"【沙盒测试失败】\n{test_report}\n\n【详细报错】\n{error_output.strip()}",
                "retry_count": retry_count + 1,
            }

    # ==========================================
    # Step 2: 没有测试文件，退化为直接运行目标脚本
    # ==========================================
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

    run_command = f"python -B {safe_filename}"
    print(f"[Sandbox] 未检测到测试文件，将执行普通脚本: {run_command}")

    if not docker_client:
        return {
            "error_trace": "沙盒环境未就绪：Docker 客户端未启动，出于安全考虑拒绝执行代码。",
            "retry_count": retry_count + 1
        }

    success, output = _run_in_container(run_command)
    if success:
        print(f"[Sandbox] {safe_filename} 运行成功！(Docker 隔离环境)")
        return {"error_trace": ""}
    else:
        error_output = output
        if len(error_output) > 1500:
            error_output = "...[前序报错已截断]...\n" + error_output[-1500:]
        return {
            "error_trace": f"【沙盒报错】\n{error_output.strip()}",
            "retry_count": retry_count + 1
        }


def _write_test_result_json(summary: dict, raw_output: str, test_files: list[str]):
    """将测试结果写入 workspace/.test_result.json，方便 Reviewer 和用户查看。"""
    result_path = os.path.join(WORKSPACE_DIR, ".test_result.json")
    try:
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump({
                "summary": summary,
                "files": [os.path.relpath(f, WORKSPACE_DIR) for f in test_files],
                "raw_output": raw_output[:3000],
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Sandbox] 写入测试结果文件失败: {e}")
import ast
import os

from src.core.config import WORKSPACE_DIR


def generate_repo_map(directory=WORKSPACE_DIR):
    """生成带有函数签名摘要的目录树"""
    repo_map = []
    ignore_dirs = {'.git', '__pycache__', 'venv', '.idea'}

    for root, dirs, files in os.walk(directory):
        # 过滤忽略的目录
        dirs[:] = [d for d in dirs if d not in ignore_dirs]

        rel_path = os.path.relpath(root, directory)
        if rel_path == '.':
            rel_path = ""

        for file in files:
            if not file.endswith('.py'):
                continue

            filepath = os.path.join(root, file)
            display_path = os.path.join(rel_path, file)

            repo_map.append(f"📄 {display_path}:")

            # 使用 AST 提取摘要
            try:
                with open(filepath, encoding='utf-8') as f:
                    tree = ast.parse(f.read())
                    for node in tree.body:
                        if isinstance(node, ast.FunctionDef):
                            repo_map.append(f"  - def {node.name}(...)")
                        elif isinstance(node, ast.ClassDef):
                            repo_map.append(f"  - class {node.name}:")
            except Exception:
                repo_map.append("  - (无法解析的 Python 文件)")

    return "\n".join(repo_map)

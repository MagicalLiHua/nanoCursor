import os
from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from src.tools.file_tools import tools

# 加载 .env 文件中的变量
load_dotenv()

# 从环境变量中获取配置，如果获取不到则使用默认值
base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
model_name = os.getenv("OLLAMA_MODEL", "qwen3")

# 初始化 LLM
llm = ChatOllama(
    base_url=base_url,
    model=model_name,
    reasoning=False,
)

llm = llm.bind_tools(tools)
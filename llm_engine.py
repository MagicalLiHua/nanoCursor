from langchain_ollama import ChatOllama
from tools import tools

# 初始化本地大语言模型 (接入你部署好的 Ollama)
llm = ChatOllama(
    base_url="http://10.160.108.2:11434",
    model="qwen3-vl:8b",
    reasoning=False,
)

llm = llm.bind_tools(tools)  # 将工具绑定到 LLM 上，使其能够调用工具完成任务
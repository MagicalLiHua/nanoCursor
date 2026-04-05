"""
LLM 引擎初始化模块
支持多模型提供商（OpenAI、Claude、Ollama、DeepSeek 等）
自动根据环境变量选择合适的模型提供商
"""

import os
from typing import Optional
from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from src.core.logger import logger

# 加载 .env 文件（位于 src/core/ 目录下）
_env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=_env_path)


def _create_openai_llm(
    api_key: str,
    base_url: Optional[str],
    model: str,
    temperature: float,
    max_tokens: int
) -> BaseChatModel:
    """创建 OpenAI 兼容接口的 LLM 实例"""
    try:
        from langchain_openai import ChatOpenAI
        kwargs = {
            "api_key": api_key,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)
    except ImportError:
        raise ImportError("请安装 langchain-openai: pip install langchain-openai")


def _create_anthropic_llm(
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int
) -> BaseChatModel:
    """创建 Claude (Anthropic) LLM 实例"""
    try:
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except ImportError:
        raise ImportError("请安装 langchain-anthropic: pip install langchain-anthropic")


def _create_ollama_llm(
    base_url: str,
    model: str,
    temperature: float
) -> BaseChatModel:
    """创建 Ollama 本地模型 LLM 实例"""
    try:
        from langchain_ollama import ChatOllama
        return ChatOllama(
            base_url=base_url,
            model=model,
            temperature=temperature,
        )
    except ImportError:
        raise ImportError("请安装 langchain-ollama: pip install langchain-ollama")


def _create_deepseek_llm(
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int
) -> BaseChatModel:
    """创建 DeepSeek LLM 实例（使用 OpenAI 兼容接口）"""
    return _create_openai_llm(
        api_key=api_key,
        base_url="https://api.deepseek.com",
        model=model or "deepseek-chat",
        temperature=temperature,
        max_tokens=max_tokens,
    )


# 模型提供商创建函数映射
_PROVIDER_CREATORS = {
    "openai": _create_openai_llm,
    "anthropic": _create_anthropic_llm,
    "ollama": _create_ollama_llm,
    "deepseek": _create_deepseek_llm,
}


def _detect_provider() -> str:
    """根据环境变量自动检测模型提供商"""
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    elif os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    elif os.getenv("DEEPSEEK_API_KEY"):
        return "deepseek"
    elif os.getenv("OLLAMA_BASE_URL"):
        return "ollama"
    else:
        return "ollama"  # 默认使用 Ollama


def create_llm(
    provider: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> BaseChatModel:
    """
    创建 LLM 实例，支持多模型提供商。
    
    Args:
        provider: 模型提供商名称 (openai, anthropic, ollama, deepseek)
                  如果为 None，自动从环境变量检测
        temperature: 温度参数，控制输出的随机性
        max_tokens: 最大输出 token 数
    
    Returns:
        LangChain ChatModel 实例
    """
    # 自动检测提供商
    if provider is None:
        provider = _detect_provider()
    
    provider = provider.lower()
    
    # 获取默认配置
    default_temp = temperature if temperature is not None else float(os.getenv("LLM_TEMPERATURE", "0.2"))
    default_max_tokens = max_tokens if max_tokens is not None else int(os.getenv("LLM_MAX_TOKENS", "4096"))
    
    logger.info(f"初始化 LLM: provider={provider}, model={os.getenv(f'{provider.upper()}_MODEL', 'default')}, temperature={default_temp}")
    
    try:
        if provider == "openai":
            return _create_openai_llm(
                api_key=os.getenv("OPENAI_API_KEY", ""),
                base_url=os.getenv("OPENAI_API_BASE", os.getenv("OPENAI_BASE_URL")),
                model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                temperature=default_temp,
                max_tokens=default_max_tokens,
            )
        elif provider == "anthropic":
            return _create_anthropic_llm(
                api_key=os.getenv("ANTHROPIC_API_KEY", ""),
                model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
                temperature=default_temp,
                max_tokens=default_max_tokens,
            )
        elif provider == "ollama":
            return _create_ollama_llm(
                base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                model=os.getenv("OLLAMA_MODEL", "qwen2.5-coder"),
                temperature=default_temp,
            )
        elif provider == "deepseek":
            return _create_deepseek_llm(
                api_key=os.getenv("DEEPSEEK_API_KEY", ""),
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
                temperature=default_temp,
                max_tokens=default_max_tokens,
            )
        else:
            raise ValueError(f"不支持的模型提供商: {provider}。支持的提供商: {list(_PROVIDER_CREATORS.keys())}")
    except Exception as e:
        logger.error(f"创建 LLM 实例失败: {e}")
        raise


class LLMWithRetry:
    """
    为 LLM 添加重试机制的包装类。
    由于 Pydantic BaseModel 不支持动态设置属性，我们使用包装类来实现。
    """
    def __init__(self, llm: BaseChatModel, max_retries: int = 3):
        self._llm = llm
        self._max_retries = max_retries
    
    async def ainvoke(self, messages, *args, **kwargs):
        """异步调用，带指数退避重试"""
        last_exception = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._llm.ainvoke(messages, *args, **kwargs)
            except Exception as e:
                last_exception = e
                logger.warning(f"LLM 异步调用失败 (尝试 {attempt}/{self._max_retries}): {e}")
                if attempt < self._max_retries:
                    # 指数退避重试
                    wait_time = 2 ** (attempt - 1)
                    logger.info(f"等待 {wait_time} 秒后重试...")
                    import asyncio
                    await asyncio.sleep(wait_time)

        logger.error(f"LLM 异步调用失败，已达到最大重试次数 ({self._max_retries})")
        raise last_exception

    def invoke(self, messages, *args, **kwargs):
        last_exception = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return self._llm.invoke(messages, *args, **kwargs)
            except Exception as e:
                last_exception = e
                logger.warning(f"LLM 调用失败 (尝试 {attempt}/{self._max_retries}): {e}")
                if attempt < self._max_retries:
                    # 指数退避重试
                    wait_time = 2 ** (attempt - 1)
                    logger.info(f"等待 {wait_time} 秒后重试...")
                    import time
                    time.sleep(wait_time)

        logger.error(f"LLM 调用失败，已达到最大重试次数 ({self._max_retries})")
        raise last_exception
    
    def bind_tools(self, *args, **kwargs):
        """代理 bind_tools 调用到内部 LLM"""
        return self._llm.bind_tools(*args, **kwargs)
    
    def __getattr__(self, name):
        """代理其他属性访问到内部 LLM"""
        return getattr(self._llm, name)


def create_llm_with_retry(
    llm: BaseChatModel,
    max_retries: int = 3,
    retry_callback=None
) -> LLMWithRetry:
    """
    为 LLM 添加重试机制的包装器。
    
    Args:
        llm: 原始的 LLM 实例
        max_retries: 最大重试次数
        retry_callback: 重试时的回调函数 (可选)
    
    Returns:
        带重试机制的 LLM 包装器
    """
    return LLMWithRetry(llm, max_retries)


# 初始化全局 LLM 实例（带重试机制）
_llm_instance = None


def get_llm(
    provider: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    use_retry: bool = True,
    max_retries: int = 3,
) -> BaseChatModel:
    """
    获取全局 LLM 实例（单例模式）。
    
    Args:
        provider: 模型提供商
        temperature: 温度参数
        max_tokens: 最大 token 数
        use_retry: 是否启用重试机制
        max_retries: 最大重试次数
    
    Returns:
        LangChain ChatModel 实例
    """
    global _llm_instance
    
    if _llm_instance is None:
        _llm_instance = create_llm(provider, temperature, max_tokens)
        
        if use_retry:
            _llm_instance = create_llm_with_retry(_llm_instance, max_retries)
    
    return _llm_instance


# 保持向后兼容的默认导出 -- 懒加载，首次被访问时才初始化
class _LazyLLM:
    """懒加载 LLM 代理，避免在模块 import 时就执行网络请求。"""
    def _get(self):
        return get_llm()
    def __getattr__(self, name):
        return getattr(self._get(), name)

llm = _LazyLLM()
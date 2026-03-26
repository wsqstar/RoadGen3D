import json
import logging
import os
import time

import requests


def _chat_completions_url(*, api_base: str | None = None) -> str:
    """由 API 根路径（.../v1）得到 Chat Completions 完整 URL。"""
    base = (
        api_base
        or os.environ.get("GRAPHRAG_API_BASE")
        or os.environ.get("OPENAI_API_BASE")
        or "https://api.linkapi.org"
    )
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


class APIClient:
    """OpenAI 兼容的 Chat Completions 客户端。

    默认请求 URL 为官方 ``https://api.linkapi.org``。使用兼容网关（如 LinkAPI）时，
    将环境变量 ``GRAPHRAG_API_BASE`` 或 ``OPENAI_API_BASE`` 设为 ``https://<host>/v1``（与 GraphRAG
    ``settings.yaml`` 中 ``api_base`` 的主机一致），本客户端会拼接为 ``.../v1/chat/completions``。
    也可在构造时传入 ``api_base``（同上，为 v1 根路径，勿只写主机名而不带 ``/v1``，除非平台另有说明）。

    trust_env_proxy: 为 True（默认）时与 requests 一致，使用环境变量中的 HTTP(S)_PROXY；
    若代理导致连接问题，可设为 False 以直连目标主机。
    """

    def __init__(
        self,
        api_key="",
        temperature=0.2,
        max_new_tokens=2000,
        top_p=0.8,
        do_sample=False,
        trust_env_proxy=True,
        api_base: str | None = None,
    ):
        self.base_url = _chat_completions_url(api_base=api_base)
        self.api_key = (
            api_key
            or os.environ.get("GRAPHRAG_API_KEY", "")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        # requests 默认会读取 HTTP(S)_PROXY；设为 False 时强制直连目标主机（绕过系统/环境代理）
        self._proxies = None if trust_env_proxy else {"http": None, "https": None}
        
        # 从配置加载参数
        self.temperature = temperature
        self.max_new_tokens = max_new_tokens
        self.top_p = top_p
        self.do_sample = do_sample
        
        #logger.info(f"API Client initialized with temperature={self.temperature}, max_new_tokens={self.max_new_tokens}")
    
    def chat_completion(self, messages, model="gpt-4o", temperature=None, max_new_tokens=None, stream=False):
        """
        调用API进行对话补全，支持流式传输
        
        Args:
            messages: 消息列表，格式为[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
            model: 使用的模型名称
            temperature: 温度参数，覆盖默认值
            max_new_tokens: 最大新token数，覆盖默认值（请求体中映射为 max_tokens）
            stream: 是否使用流式传输
            
        Returns:
            模型响应内容（非流式）或响应对象（流式）
        """
        try:
            data = {
                "model": model,
                "messages": messages,
                "temperature": temperature or self.temperature,
                "max_tokens": max_new_tokens or self.max_new_tokens,
                "top_p": self.top_p,
                "stream": stream
            }
            
            # 重试机制
            max_retries = 3
            for retry in range(max_retries):
                try:
                    response = requests.post(
                            self.base_url,
                            headers=self.headers,
                            data=json.dumps(data),
                            timeout=(30, 360),  # 连接超时30秒，读取超时360秒
                            proxies=self._proxies,
                        )
                    if not response.ok:
                        try:
                            err_body = response.json()
                            logging.warning(
                                "API error %s: %s", response.status_code, err_body
                            )
                        except Exception:
                            pass
                    response.raise_for_status()
                    
                    if stream:
                        # 流式传输模式，返回响应对象
                        return response
                    else:
                        # 非流式传输，返回完整响应内容
                        result = response.json()
                        if "error" in result and result["error"]:
                            err = result["error"]
                            msg = err.get("message", str(err))
                            code = err.get("code", "")
                            typ = err.get("type", "")
                            raise RuntimeError(
                                f"API 返回错误 (type={typ}, code={code}): {msg}"
                            )
                        if "choices" in result and result["choices"]:
                            return result["choices"][0]["message"]["content"]
                        return ""
                            
                except (
                    requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError,
                ):
                    if retry == max_retries - 1:
                        raise
                    time.sleep(5)
            
                
        except requests.exceptions.RequestException as e:
            #logger.error(f"API request failed: {e}")
            raise
        except json.JSONDecodeError as e:
            #logger.error(f"API response JSON decode failed: {e}")
            raise

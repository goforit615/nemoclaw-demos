"""LLM Service - Centralized LLM access with LangSmith tracing.

This module provides a unified interface for creating LLM instances,
automatically handling API keys and endpoint configuration.

Endpoint:
  - inference-api.nvidia.com   (NVIDIA Inference Hub – INFERENCE_API_KEY)

Migration note (March 2026):
  The NVIDIA ASTRA deployment (datarobot.prd.astra.nvidia.com) was
  deactivated.  The ``"astra"`` model alias now resolves to
  ``openai/openai/gpt-5-nano`` on the Inference Hub instead.

  Capability differences vs. old ASTRA models:
    - GPT-5-nano is a *reasoning* model; it spends tokens on internal
      chain-of-thought before producing visible output.  Keep max_tokens
      generous (>=4096) to leave room for both reasoning and answer.
    - Streaming still works but chunks may arrive in bursts (reasoning
      phase produces no visible output, then answer streams quickly).
    - Rate limits on the Inference Hub are 100 req/min (vs 50 on ASTRA).

  To restore a private ASTRA deployment, add a custom entry to MODELS:
      MODELS["astra_private"] = (model, "ASTRA_TOKEN", "https://...")
  then point the "astra" USE_CASE to "astra_private".

Usage:
    from llm import create_llm
    
    # Use case based (recommended)
    llm = create_llm("study_material_generation")
    response = llm.invoke("Hello").content
    
    # With chains
    chain = prompt_template | create_llm("chapter_title_generation") | StrOutputParser()
    result = chain.invoke({"topic": "..."})
"""

import os
import warnings
from typing import Optional, Dict

from langchain_nvidia_ai_endpoints import ChatNVIDIA

DEFAULT_TIMEOUT = 120
DEFAULT_MAX_RETRIES = 2

warnings.filterwarnings("ignore", message=".*does not end in /v1.*")

# =============================================================================
# Endpoint base URL
# =============================================================================
INFERENCE_BASE_URL = "https://inference-api.nvidia.com/v1"

# =============================================================================
# Model registry: alias → (model_name, api_key_env, base_url)
# All models route through the NVIDIA Inference Hub using INFERENCE_API_KEY.
# =============================================================================
MODELS = {
    # --- NVIDIA Inference Hub (inference-api.nvidia.com) ---
    # Auth: INFERENCE_API_KEY   |  Rate limit: 100 req/min
    # Note: gpt-5-nano is a reasoning model; it uses "reasoning tokens"
    # internally before producing visible output.  Ensure max_tokens is
    # large enough for both the hidden reasoning and the visible answer.
    "gpt_5_nano": ("openai/openai/gpt-5-nano", "INFERENCE_API_KEY", INFERENCE_BASE_URL),
    "gpt_5_2": ("openai/openai/gpt-5.2", "INFERENCE_API_KEY", INFERENCE_BASE_URL),
    "gemini_3_flash": ("gcp/google/gemini-3-flash-preview", "INFERENCE_API_KEY", INFERENCE_BASE_URL),

    # --- Capability aliases (all on Inference Hub) ---
    "fast": ("gcp/google/gemini-3-flash-preview", "INFERENCE_API_KEY", INFERENCE_BASE_URL),
    "powerful": ("openai/openai/gpt-5.2", "INFERENCE_API_KEY", INFERENCE_BASE_URL),
    "reasoning": ("openai/openai/gpt-5-nano", "INFERENCE_API_KEY", INFERENCE_BASE_URL),

    # --- Legacy alias ---
    # "astra" previously pointed to the ASTRA deployment (now inactive).
    # Redirected to gpt-5-nano on the Inference Hub for backward compat.
    "astra": ("openai/openai/gpt-5-nano", "INFERENCE_API_KEY", INFERENCE_BASE_URL),
}

# =============================================================================
# Use case configurations
#
# Format: use_case → (model_alias, temperature, top_p, max_tokens)
#
# max_tokens guidance for reasoning models (gpt_5_nano):
#   The model uses "reasoning tokens" internally before producing visible
#   output.  A simple question may consume ~256 reasoning tokens; a complex
#   one ~2k+.  Set max_tokens high enough so the model has room for both
#   reasoning AND the answer.  If max_tokens is too low the response will
#   be empty (all budget consumed by reasoning).
# =============================================================================
USE_CASES = {
    # Chat / Study Buddy  (was ASTRA → now Inference Hub gpt-5-nano)
    "astra": ("astra", 0.6, 0.95, 36000),

    # Curriculum Generation
    "chapter_title_generation": ("fast", 0.7, 1.0, 1024),
    "subtopic_title_generation": ("fast", 0.7, 1.0, 512),
    "curriculum_modification": ("gpt_5_nano", 0.5, 1.0, 4096),
    "extract_sub_chapters": ("gpt_5_nano", 0.3, 1.0, 36000),

    # Study Material Generation
    "study_material_generation": ("gpt_5_nano", 0.6, 1.0, 65000),

    # Document Search & RAG
    "document_search_rerank": ("fast", 0.3, 1.0, 512),

    # Memory Operations
    "memory_routing": ("gpt_5_nano", 0.3, 1.0, 36000),
    "memory_extraction": ("gpt_5_nano", 0.3, 1.0, 36000),

    # Calendar
    "calendar_parsing": ("gpt_5_nano", 0.3, 1.0, 36000),

    # Query Decomposition
    "query_decomposition": ("fast", 0.3, 1.0, 36000),
}


def create_llm(
    use_case: str = "fast",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
    max_retries: Optional[int] = None,
    **kwargs
) -> ChatNVIDIA:
    """Create a ChatNVIDIA instance for a specific use case.
    
    Args:
        use_case: Use case name (see USE_CASES) or model alias (see MODELS).
        temperature: Override default temperature (0.0-1.0).
        max_tokens: Override default max tokens.
        timeout: Request timeout in seconds (default: 120s).
        max_retries: Max retries on failure (default: 2).
        **kwargs: Additional ChatNVIDIA parameters.
        
    Returns:
        ChatNVIDIA instance. Supports LangSmith tracing when LANGSMITH_API_KEY is set.
    """
    if use_case in USE_CASES:
        model_alias, default_temp, default_top_p, default_max_tokens = USE_CASES[use_case]
        temperature = temperature if temperature is not None else default_temp
        top_p = default_top_p
        max_tokens = max_tokens if max_tokens is not None else default_max_tokens
    else:
        model_alias = use_case
        temperature = temperature if temperature is not None else 0.7
        top_p = 1.0
        max_tokens = max_tokens if max_tokens is not None else 4096

    timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
    max_retries = max_retries if max_retries is not None else DEFAULT_MAX_RETRIES

    model_name, api_key_env, base_url = MODELS.get(
        model_alias,
        (model_alias, "INFERENCE_API_KEY", INFERENCE_BASE_URL),
    )

    run_name = f"llm:{use_case}" if use_case in USE_CASES else f"llm:{model_alias}"

    llm_kwargs: Dict = {
        "model": model_name,
        "api_key": os.getenv(api_key_env),
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "timeout": timeout,
        "max_retries": max_retries,
        **kwargs,
    }

    if base_url:
        llm_kwargs["base_url"] = base_url

    return ChatNVIDIA(**llm_kwargs).with_config(run_name=run_name)


def get_available_use_cases() -> list:
    """Get list of available use cases."""
    return list(USE_CASES.keys())


def get_available_models() -> list:
    """Get list of available model aliases."""
    return list(MODELS.keys())


__all__ = [
    "create_llm",
    "get_available_use_cases",
    "get_available_models",
    "MODELS",
    "USE_CASES",
    "DEFAULT_TIMEOUT",
    "DEFAULT_MAX_RETRIES",
    "INFERENCE_BASE_URL",
]

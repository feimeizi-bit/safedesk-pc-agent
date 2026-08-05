from llama_cpp import Llama
import os

_llm = None

def get_llm(model_path: str | None = None):
    global _llm
    if _llm is None:
        model_path = model_path or os.environ.get(
            "PC_AGENT_MODEL_PATH",
            "./models/Qwen3.5-4B-Q4_K_M.gguf",
        )
        _llm = Llama(
            model_path=model_path,
            n_ctx=int(os.environ.get("PC_AGENT_N_CTX", "4096")),
            n_threads=int(os.environ.get("PC_AGENT_N_THREADS", "8")),
            n_gpu_layers=int(os.environ.get("PC_AGENT_N_GPU_LAYERS", "0")),
            verbose=False
        )
    return _llm

def chat_completion(messages, max_tokens=384, temperature=0.0):
    llm = get_llm()
    response = llm.create_chat_completion(
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        stop=["\n用户：", "\nUser:"]
    )
    return response['choices'][0]['message']['content']

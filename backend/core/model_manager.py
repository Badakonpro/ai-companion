import os
from pydantic import BaseModel
import platform

class ModelConfig(BaseModel):
    device_type: str = "auto"
    model_name: str = ""

def detect_best_device() -> str:
    system = platform.system()
    machine = platform.machine()
    
    if system == "Darwin" and machine == "arm64":
        return "mac_ane_or_cpu" # Apple Silicon
    elif system == "Windows":
        return "windows_npu_or_gpu" # PC with RTX5080 and NPU
    return "cpu"

class ModelManager:
    """
    Manages loading and routing to different models based on hardware architecture.
    """
    def __init__(self):
        self.device = detect_best_device()
        print(f"[ModelManager] Detected environment for offloading: {self.device}")

    def get_embedding_model(self):
        # Placeholder for lightweight NPU/CPU embedding model setup
        pass
        
    def generate_chat(self, prompt: str, system_prompt: str = ""):
        # Placeholder for routing to core GPU LLM (e.g. via Ollama)
        pass

# Singleton instance
model_manager = ModelManager()

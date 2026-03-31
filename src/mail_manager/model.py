import os
from transformers import AutoModelForCausalLM, AutoTokenizer

DEFAULT_CHECKPOINT = "Qwen/Qwen3-4B"

token = os.getenv("HF_TOKEN")


def load_model(checkpoint: str = DEFAULT_CHECKPOINT, device: str = "cpu"):
    """
    Downloads (if needed) and loads the model + tokenizer.

    Args:
        checkpoint (str): HuggingFace model name
        device (str): "cpu", "cuda", or "auto"

    Returns:
        tokenizer, model
    """
    tokenizer = AutoTokenizer.from_pretrained(checkpoint, token=token)

    model = AutoModelForCausalLM.from_pretrained(
        checkpoint,
        token=token,
        device_map="auto" if device == "auto" else None,
    )

    if device != "auto":
        model = model.to(device)

    return tokenizer, model


if __name__ == "__main__":
    load_model()
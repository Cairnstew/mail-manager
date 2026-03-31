import os
import json
import datetime
from mail_manager.model import load_model

LOG_PATH = os.path.expanduser("~/.mail_manager/classifications.jsonl")


def _append_log(entry: dict) -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


class ChatModel:
    def __init__(self, device="auto", debug=False):
        self.tokenizer, self.model = load_model(device=device)
        self.debug = debug

    def generate(self, messages, enable_thinking=False, max_new_tokens=2048):
        inputs = self.tokenizer.apply_chat_template(
            messages,
            enable_thinking=enable_thinking,
            add_generation_prompt=True,
            tokenize=True,
            return_tensors="pt"
        )

        input_ids = inputs["input_ids"].to(self.model.device)
        attention_mask = inputs["attention_mask"].to(self.model.device)
        input_length = input_ids.shape[1]

        outputs = self.model.generate(
            input_ids=input_ids,
            attention_mask=attention_mask,
            max_new_tokens=max_new_tokens,
            pad_token_id=self.tokenizer.eos_token_id,
            temperature=0,
            do_sample=False,
        )

        new_tokens = outputs[0][input_length:]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

    def classify(
        self,
        email_text: str,
        categories: dict[str, str],
        confidence_threshold: float = 0.0,
        fallback: str = "Spam",
    ) -> tuple[str, float]:
        """
        Classify an email into one of the provided categories.

        Returns:
            A tuple of (bucket_name, confidence).
        """
        category_list = "\n".join(f"- {name}: {desc}" for name, desc in categories.items())

        prompt = f"""Classify this email into ONE of these categories:

{category_list}

Email:
"{email_text}"

Respond in exactly this format:
Category: <category name>
Confidence: <number between 0.0 and 1.0>
"""
        messages = [{"role": "user", "content": prompt}]
        raw = self.generate(messages, enable_thinking=True, max_new_tokens=2048)

        if self.debug:
            token_count = len(self.tokenizer.encode(raw))
            print(f"\n── raw model output {token_count} tokens ──")
            print(raw)
            print("──────────────────────\n")

        category, confidence = self._parse_response(raw, categories)

        fell_back = category is None or confidence < confidence_threshold
        bucket = fallback if fell_back else category

        # Log every classification
        _append_log({
            "timestamp":   datetime.datetime.now().isoformat(),
            "bucket":      bucket,
            "category":    category,
            "confidence":  confidence,
            "threshold":   confidence_threshold,
            "fell_back":   fell_back,
            "preview":     email_text.splitlines()[0][:120],
        })

        return bucket, confidence

    def _parse_response(
        self,
        raw: str,
        categories: dict[str, str],
    ) -> tuple[str | None, float]:
        """Parse 'Category: X\nConfidence: Y' from the model response."""
        # Strip Qwen3 thinking block if present
        if "<think>" in raw:
            raw = raw.split("</think>")[-1].strip()

        category = None
        confidence = 0.0

        for line in raw.splitlines():
            line_lower = line.lower()

            if line_lower.startswith("category:"):
                value = line.split(":", 1)[1].strip().lower()
                for name in categories:
                    if name.lower() in value:
                        category = name
                        break

            elif line_lower.startswith("confidence:"):
                value = line.split(":", 1)[1].strip()
                try:
                    confidence = max(0.0, min(1.0, float(value)))
                except ValueError:
                    confidence = 0.0

        return category, confidence
"""
Thin wrapper around the captioning model (BLIP-base, CPU only).
"""
import os

MODEL_NAME = os.getenv("MODEL_NAME", "Salesforce/blip-image-captioning-base")


class BlipCaptioner:
    def __init__(self, model_name: str = MODEL_NAME):
        import torch
        from transformers import BlipForConditionalGeneration, BlipProcessor

        self._torch = torch
        self.processor = BlipProcessor.from_pretrained(model_name)
        self.model = BlipForConditionalGeneration.from_pretrained(model_name)
        self.model.eval()
        self.param_bytes = sum(p.numel() * p.element_size() for p in self.model.parameters())

    def caption(self, image) -> str:
        inputs = self.processor(images=image, return_tensors="pt")
        with self._torch.inference_mode():
            out = self.model.generate(**inputs, max_new_tokens=30, num_beams=3)
        return self.processor.decode(out[0], skip_special_tokens=True).strip()


def load_captioner():
    return BlipCaptioner()

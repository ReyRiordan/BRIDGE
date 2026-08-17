#!/usr/bin/env python3
"""
Background removal via two-pass difference matting using Gemini Flash Image Preview.

Usage: python3 make_transparent.py <image_path>
Output: visuals/<stem>.png (transparent RGBA PNG)
"""

import sys
import os
import base64
import io
from pathlib import Path

import requests
import numpy as np
from PIL import Image
from dotenv import load_dotenv

load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = "google/gemini-3.1-flash-image-preview"

RESOURCES = Path(__file__).parent / "resources"
PROMPT_WHITE_BG = (RESOURCES / "prompt_white_bg.txt").read_text()
PROMPT_BLACK_BG = (RESOURCES / "prompt_black_bg.txt").read_text()


def image_to_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def call_openrouter(messages: list) -> dict:
    resp = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": messages,
            "modalities": ["image", "text"],
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def extract_image_bytes(result: dict) -> bytes:
    choices = result.get("choices", [])
    if not choices:
        raise ValueError(f"No choices in response: {result}")
    message = choices[0]["message"]
    images = message.get("images") or []
    if not images:
        # Some models embed images in content list
        content = message.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    url = block["image_url"]["url"]
                    _, data = url.split(",", 1)
                    return base64.b64decode(data)
        raise ValueError(f"No images in response message: {message}")
    url = images[0]["image_url"]["url"]
    _, data = url.split(",", 1)
    return base64.b64decode(data)


BACKGROUND_PATH = Path(__file__).parent / "scenes" / "background.jpg"


def step1_white_bg(image_path: str) -> bytes:
    b64 = image_to_base64(image_path)
    ext = Path(image_path).suffix.lstrip(".").lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"

    content = []

    if BACKGROUND_PATH.exists():
        bg_b64 = image_to_base64(str(BACKGROUND_PATH))
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{bg_b64}"}})

    content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    content.append({"type": "text", "text": PROMPT_WHITE_BG})

    messages = [{"role": "user", "content": content}]
    result = call_openrouter(messages)
    return extract_image_bytes(result)


def step2_black_bg(white_bytes: bytes) -> bytes:
    b64 = base64.b64encode(white_bytes).decode()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": PROMPT_BLACK_BG},
            ],
        }
    ]
    result = call_openrouter(messages)
    return extract_image_bytes(result)


def difference_matte(white_bytes: bytes, black_bytes: bytes) -> Image.Image:
    white_img = Image.open(io.BytesIO(white_bytes)).convert("RGB")
    black_img = Image.open(io.BytesIO(black_bytes)).convert("RGB")

    if white_img.size != black_img.size:
        black_img = black_img.resize(white_img.size, Image.LANCZOS)

    w = np.array(white_img, dtype=np.float32)
    b = np.array(black_img, dtype=np.float32)

    # alpha per channel: alpha = 1 - (white - black) / 255
    alpha = 1.0 - (w - b) / 255.0
    alpha = np.mean(alpha, axis=2)          # average across RGB
    alpha = np.clip(alpha, 0.0, 1.0)

    # recover foreground via un-premultiplication: fg = black / alpha
    fg = np.zeros_like(w)
    mask = alpha > 1e-6
    fg[mask] = b[mask] / alpha[mask, np.newaxis]
    fg = np.clip(fg, 0.0, 255.0)

    rgba = np.zeros((w.shape[0], w.shape[1], 4), dtype=np.uint8)
    rgba[:, :, :3] = fg.astype(np.uint8)
    rgba[:, :, 3] = (alpha * 255).astype(np.uint8)

    return Image.fromarray(rgba, "RGBA")


def process_image(image_path: str, output_dir: Path) -> None:
    print(f"Step 1: rendering on white background...")
    white_bytes = step1_white_bg(image_path)
    print(f"Step 2: converting to black background...")
    black_bytes = step2_black_bg(white_bytes)
    print(f"Step 3: applying difference matting...")
    result = difference_matte(white_bytes, black_bytes)
    stem = Path(image_path).stem
    out_path = output_dir / f"{stem}.png"
    result.save(out_path, "PNG")
    print(f"Saved: {out_path}")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python3 make_transparent.py <image_path>")
        sys.exit(1)

    image_path = sys.argv[1]
    if not Path(image_path).exists():
        print(f"Error: file not found: {image_path}")
        sys.exit(1)

    if not OPENROUTER_API_KEY:
        print("Error: OPENROUTER_API_KEY not set in .env")
        sys.exit(1)

    output_dir = Path("visuals")
    output_dir.mkdir(exist_ok=True)

    process_image(image_path, output_dir)


if __name__ == "__main__":
    main()

from __future__ import annotations

import sys
from pathlib import Path

from groq import Groq


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agentorchestra.config import ConfigurationError, get_settings


def main() -> int:
    settings = get_settings()
    try:
        settings.require_groq()
    except ConfigurationError as exc:
        print(f"FAIL Groq configuration: {exc}")
        return 1

    try:
        client = Groq(api_key=settings.groq_api_key_value)
        response = client.chat.completions.create(
            model=settings.groq_model_value,
            messages=[{"role": "user", "content": "Return only the single word READY."}],
            temperature=0,
            max_tokens=4,
        )
        content = response.choices[0].message.content or ""
    except Exception as exc:
        print(f"FAIL Groq request using model {settings.groq_model_value}: {exc}")
        return 1

    print(f"PASS Groq connectivity: model={settings.groq_model_value}, response={content.strip()!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

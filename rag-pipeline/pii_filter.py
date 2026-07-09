"""基础 PII/敏感信息检测 / Basic PII/sensitive data detection.

使用正则表达式检测常见敏感信息模式，可在配置中开关。
Uses regex to detect common sensitive data patterns. Toggleable via config.
"""

from __future__ import annotations

import re
from typing import Pattern

# 常见 PII 模式 / Common PII patterns
_PATTERNS: list[tuple[Pattern[str], str]] = [
    (re.compile(r"\b\d{3}-\d{4}-\d{4}\b"), "银行卡号 / Bank card number"),
    (re.compile(r"(?<!\d)\d{15,19}(?!\d)"), "长数字序列 / Long numeric sequence"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "邮箱地址 / Email address"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "中国大陆手机号 / Chinese phone number"),
    (re.compile(r"\b\d{18}[\dXx]\b"), "身份证号 / Chinese ID number"),
    (re.compile(r"(?:password|passwd|pwd)\s*[:=]\s*\S+", re.IGNORECASE), "密码 / Password"),
    (re.compile(r"(?:api[_-]?key|secret[_-]?key|token)\s*[:=]\s*['\"]?[A-Za-z0-9+/=]{16,}", re.IGNORECASE), "API Key / Secret"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "Secret key pattern"),
    (re.compile(r"pk-[A-Za-z0-9]{20,}"), "Public key pattern"),
]


def detect_pii(text: str, max_matches: int = 5) -> list[str]:
    """检测文本中的 PII/敏感信息 / Detect PII/sensitive info in text.

    Args:
        text: Text to scan
        max_matches: Max matches per pattern to report

    Returns:
        List of detected pattern descriptions
    """
    findings: list[str] = []
    for pattern, label in _PATTERNS:
        matches = pattern.findall(text)
        if matches:
            count = min(len(matches), max_matches)
            findings.append(f"{label}: {count} found")
    return findings


def sanitize_text(text: str, replacements: dict[str, str] | None = None) -> str:
    """替换文本中的敏感信息 / Replace sensitive info in text.

    Args:
        text: Original text
        replacements: Custom replacement map (pattern_label -> replacement_string)

    Returns:
        Sanitized text
    """
    result = text
    defaults = {
        "邮箱地址 / Email address": "[EMAIL_REDACTED]",
        "中国大陆手机号 / Chinese phone number": "[PHONE_REDACTED]",
        "身份证号 / Chinese ID number": "[ID_REDACTED]",
        "密码 / Password": "[PASSWORD_REDACTED]",
        "API Key / Secret": "[KEY_REDACTED]",
        "Secret key pattern": "[SECRET_REDACTED]",
        "Public key pattern": "[PUBLIC_KEY_REDACTED]",
    }
    replacements = replacements or defaults
    for pattern, label in _PATTERNS:
        if label in replacements:
            result = pattern.sub(replacements[label], result)
    return result

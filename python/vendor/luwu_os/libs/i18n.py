#!/usr/bin/env python3
"""
Luwu OS 全局国际化（i18n）公共库。

- 全局唯一语言配置：LUWU_ROOT/configs/language.ini
  文件内容只有 "cn" 或 "en" 一行纯文本。
- 字体路径优先 luwu-os/model/msyh.ttc，fallback 到系统中文字体。
- 任意 PySide6 App 通过 from libs.i18n import get_lang, t, FONT_PATH 接入。

使用：
    from libs.i18n import get_lang, Translator, FONT_PATH
    tr = Translator({
        "cn": {"hello": "你好"},
        "en": {"hello": "Hello"},
    })
    print(tr("hello"))

或者直接：
    from libs.i18n import t
    t({"cn": "你好", "en": "Hello"})
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ---- 路径常量 ----
LUWU_ROOT = Path(os.environ.get("LUWU_ROOT", "/opt/luwu-os"))
LANGUAGE_INI = Path(os.environ.get('DUCK_LANGUAGE_FILE', LUWU_ROOT / "configs" / "language.ini"))

# 兼容旧的 settings 内 language.ini（一次性迁移）
_LEGACY_INI = LUWU_ROOT / "apps" / "settings" / "language.ini"

# 字体 fallback 链
_FONT_CANDIDATES = [
    LUWU_ROOT / "model" / "msyh.ttc",
    Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]


def _resolve_font() -> str:
    for p in _FONT_CANDIDATES:
        if p.exists():
            return str(p)
    return ""


FONT_PATH = _resolve_font()


def _migrate_legacy_if_needed() -> None:
    """如果全局 INI 不存在但 settings 旧 INI 存在，自动迁移过去。"""
    try:
        if LANGUAGE_INI.exists():
            return
        if _LEGACY_INI.exists():
            LANGUAGE_INI.parent.mkdir(parents=True, exist_ok=True)
            LANGUAGE_INI.write_text(_LEGACY_INI.read_text().strip() or "cn")
    except Exception:
        pass


def get_lang() -> str:
    """返回 'cn' 或 'en'，读取失败默认 'cn'。"""
    _migrate_legacy_if_needed()
    try:
        v = LANGUAGE_INI.read_text().strip()
        return v if v in ("cn", "en") else "cn"
    except Exception:
        return "cn"


def set_lang(code: str) -> bool:
    """写入全局语言配置（'cn' 或 'en'）。"""
    if code not in ("cn", "en"):
        return False
    try:
        LANGUAGE_INI.parent.mkdir(parents=True, exist_ok=True)
        LANGUAGE_INI.write_text(code)
        # 同步写回旧路径，最大兼容（settings 等老调用方仍可读到）
        try:
            _LEGACY_INI.write_text(code)
        except Exception:
            pass
        return True
    except Exception:
        return False


class Translator:
    """轻量翻译器：传入 {'cn': {...}, 'en': {...}}，调用实例(key) 取值。"""

    def __init__(self, table: dict, lang: str | None = None):
        self.table = table or {}
        self.lang = lang or get_lang()

    def __call__(self, key: str, *args) -> str:
        text = self.table.get(self.lang, self.table.get("cn", {})).get(key, key)
        if args:
            try:
                text = text.format(*args)
            except Exception:
                pass
        return text


def t(pair: dict, *args) -> str:
    """快速翻译一对中英文：t({'cn': '你好', 'en': 'Hello'})"""
    lang = get_lang()
    text = pair.get(lang) or pair.get("cn") or pair.get("en") or ""
    if args:
        try:
            text = text.format(*args)
        except Exception:
            pass
    return text

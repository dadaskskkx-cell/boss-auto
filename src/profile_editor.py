"""启动前岗位配置向导。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from .profile_builder import build_profile_from_jd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = PROJECT_ROOT / "config" / "profile.yaml"


def main() -> int:
    print("=== Boss 自动筛选启动向导 ===")
    print("每次启动都需要重新输入本次岗位 JD。")
    print("粘贴 JD 时，结束请输入单独一行 /done")
    print()

    job_title = _prompt_non_empty("岗位名称: ")
    job_description = _prompt_multiline("请粘贴岗位 JD")

    profile = build_profile_from_jd(job_title, job_description)
    print()
    print("=== 自动生成的筛选规则 ===")
    print(_dump_profile(profile))
    print()

    profile = _apply_overrides_interactively(profile)

    if not _confirm("确认保存以上配置并开始执行？[Y/n]: ", default=True):
        print("已取消，本次不执行。")
        return 0

    _save_profile(profile, PROFILE_PATH)
    print(f"已保存岗位配置: {PROFILE_PATH}")
    print("开始执行 Boss 自动筛选...")
    print()

    result = subprocess.run([sys.executable, "-m", "src.script_runner"], cwd=PROJECT_ROOT)
    return int(result.returncode)


def _prompt_non_empty(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("该项不能为空，请重新输入。")


def _prompt_multiline(title: str) -> str:
    print(title)
    lines: list[str] = []
    while True:
        line = input()
        if line.strip() == "/done":
            break
        lines.append(line.rstrip())
    value = "\n".join(lines).strip()
    if value:
        return value
    print("JD 不能为空，请重新输入。")
    return _prompt_multiline(title)


def _apply_overrides_interactively(profile: dict) -> dict:
    rules = profile.setdefault("rules", {})

    print("直接回车表示使用自动生成值。")
    rules["min_education"] = _prompt_text("最低学历", rules.get("min_education", "不限"))
    rules["min_experience"] = _prompt_int("最低经验(年)", int(rules.get("min_experience", 0) or 0))
    rules["required_keywords_any"] = _prompt_keywords(
        "关键命中词(逗号分隔)",
        rules.get("required_keywords_any", []),
    )
    rules["keyword_pool"] = _prompt_keywords(
        "关键词池(逗号分隔)",
        rules.get("keyword_pool", []),
    )
    rules["blacklist_keywords"] = _prompt_keywords(
        "黑名单词(逗号分隔)",
        rules.get("blacklist_keywords", []),
    )

    print()
    print("=== 最终执行配置 ===")
    print(_dump_profile(profile))
    print()
    return profile


def _prompt_text(label: str, default: str) -> str:
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def _prompt_int(label: str, default: int) -> int:
    value = input(f"{label} [{default}]: ").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        print("请输入整数。")
        return _prompt_int(label, default)


def _prompt_keywords(label: str, default: list[str]) -> list[str]:
    default_text = ", ".join(default)
    value = input(f"{label} [{default_text}]: ").strip()
    if not value:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


def _confirm(prompt: str, *, default: bool) -> bool:
    value = input(prompt).strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "1"}


def _save_profile(profile: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(profile, fh, allow_unicode=True, sort_keys=False)


def _dump_profile(profile: dict) -> str:
    return yaml.safe_dump(profile, allow_unicode=True, sort_keys=False)


if __name__ == "__main__":
    raise SystemExit(main())

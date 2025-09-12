#!/usr/bin/env python3
"""統一的測試腳本，執行所有 linter 和格式化工具。

這個腳本會依序執行：
1. Black 格式化
2. isort 匯入排序
3. Ruff 靜態檢查
4. Pylint 靜態分析

所有輸出會集中顯示，方便檢查錯誤。
"""

from pathlib import Path
import subprocess
import sys


def run_command(cmd: list[str], description: str) -> tuple[bool, str]:
    """執行命令並返回成功狀態和輸出。"""
    print(f"\n{'='*60}")
    print(f"執行: {description}")
    print(f"命令: {' '.join(cmd)}")
    print("=" * 60)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False, cwd=Path(__file__).parent
        )

        success = result.returncode == 0
        output = result.stdout + result.stderr

        if success:
            print("✅ 成功")
        else:
            print("❌ 失敗")

        if output.strip():
            print("\n輸出:")
            print(output)
        else:
            print("(無輸出)")

        return success, output

    except Exception as e:
        print(f"❌ 執行錯誤: {e}")
        return False, str(e)


def main() -> None:
    """主函數：依序執行所有檢查。"""
    print("開始執行所有 linter 和格式化工具...")

    # 定義要執行的命令
    commands = [
        (["python", "-m", "black", ".", "--check"], "Black 格式化檢查"),
        (["python", "-m", "isort", ".", "--check-only"], "isort 匯入排序檢查"),
        (["python", "-m", "ruff", "check", "."], "Ruff 靜態檢查"),
        (["python", "-m", "pylint", "app", "core", "infrastructure"], "Pylint 靜態分析"),
    ]

    results = []

    for cmd, description in commands:
        success, output = run_command(cmd, description)
        results.append((description, success, output))

    # 總結報告
    print(f"\n{'='*60}")
    print("總結報告")
    print("=" * 60)

    all_passed = True
    for description, success, _ in results:
        status = "✅ 通過" if success else "❌ 失敗"
        print(f"{description}: {status}")
        if not success:
            all_passed = False

    print(f"\n整體結果: {'✅ 全部通過' if all_passed else '❌ 有錯誤'}")

    if not all_passed:
        print("\n詳細錯誤資訊:")
        for description, success, output in results:
            if not success and output.strip():
                print(f"\n--- {description} 錯誤 ---")
                print(output)

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()

請用 Python 3.10+，嚴格遵循 Google Python Style Guide，並符合以下要點：

- 格式與靜態檢查
  - 以自動格式化工具（Black + isort + Ruff + Pylint）統一格式和檢查。
  - 行長限制 100 字元（Black 預設），必要時可到 120。
  - 必跑所有 linter：Black、isort、Ruff、Pylint；禁止使用 disable 註解關閉規則。
  - 使用 `python run_all_tests.py` 統一執行所有檢查。

- 匯入與套件
  - 僅匯入模組或套件（import x 或 from x import y）；避免匯入函式/類別星號匯入。
  - 一律使用絕對匯入；禁止相對匯入。
  - 匯入排序：標準庫、第三方、本地；群組間以空行分隔；不留未使用匯入。

- 命名
  - 模組/函式/變數：lower_snake_case；類別：CapWords；常數：UPPER_SNAKE_CASE。
  - 內部/非公開成員以單底線開頭。

- 型別註記（Typing）
  - 公開 API 必須完整型別註記；重要內部函式建議註記。
  - 使用內建泛型（list[str], dict[str, int]）；優先抽象型別（Sequence, Mapping 等，來自 collections.abc）。
  - 使用 X | None 而非 Optional[X]；必要時使用 TypeVar/ParamSpec；僅為型別用途的匯入可置於 if TYPE_CHECKING 區塊。
  - 避免 Any，除非有明確理由。

- 文件與註解
  - 模組、類別與公開函式必有 docstring，採 Google 風格：
    - 首行一句話摘要；視需要加入 Args/Returns/Raises/Examples 段。
  - 內部函式和方法也建議加上簡潔的 docstring。
  - TODO 註解格式：TODO(username): 簡述（可含日期/追蹤連結）。

- 語言慣例
  - 縮排 4 空白；每行一個語句；不使用分號；保持運算子與逗號的合理空白。
  - 避免可變預設參數（用 None 並於函式內建立）。
  - 優先使用推導式/產生器，但保持可讀性（巢狀不超過 2 層）。
  - 文字用 f-string；日誌使用延遲格式化：logger.info("x=%s", x)。
  - 例外：擲出具體例外；禁止裸 except；使用「raise ... from e」保留原因鏈。
  - 檔案/資源以 with 管理；腳本入口使用 if __name__ == "__main__": main()。
  - Windows API 相關常數和結構體必須保持原始命名（如 Data1, Data2）以符合 Windows 慣例。
  - 對於 ctypes 結構體，優先遵循目標 API 的命名慣例而非 Python 慣例。

- 一致性原則
  - 與既有程式碼風格一致；如需偏離規範，請以註解簡短說明理由並盡量局部化。
  - 所有 linter 必須通過；對於 Windows API 結構體等特殊情況，可調整 pylint 配置而非使用 disable 註解。

- 工具配置
  - 使用 `pyproject.toml` 統一配置 Black、isort、Ruff、Pylint。
  - 執行 `python run_all_tests.py` 進行完整檢查。
  - 開發時建議設定 pre-commit hooks 自動執行檢查。

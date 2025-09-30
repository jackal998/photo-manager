# Photo Manager 設計路線 v1.0（Python 版）

本文件為本專案之唯一權威設計依據（Single Source of Truth）。後續開發、測試與發佈以此文件為準。

## 決策總結（Final）

- **語言/平台**: Python 3.11+（Windows Only）
- **GUI**: PySide6（Qt 6）
- **HEIC**: 需安裝 `pillow-heif` 才能完整解碼原圖；若未安裝則以 Windows Shell/WIC 取得縮圖做為備援（不崩潰，僅縮圖/預覽尺寸受限）
- **刪除**: 預設送資源回收桶（send2trash）
- **鎖定**: 僅 App 內部狀態，不改動檔案屬性
- **匯出 FileSize**: 一律重新讀取實體檔案大小，以 Bytes（整數）輸出
- **資料量級**: 約 20,000 組群、50,000 檔案（需虛擬化、懶載入與快取）
- **分群來源**: 一律由 CSV 提供；本 App 不做相似/重複分析
- **Select/UnSelect**: 「Select by Field/Regex」對話框支援批次選擇/取消選擇
- **群組全選刪除策略**: 允許，但跳出強烈警告並需二次確認

---

## 1. 目標與非目標

- **目標**
  - 讀取/顯示大量 CSV 之照片資料，依 `GroupNumber` 分群可折疊/展開
  - 操作：勾選/取消勾選（Selected）、刪除（回收桶）、基本排序、匯入/匯出
  - Select 對話框：依欄位 + Regex 批次選擇/取消選擇
  - 預覽：單檔原圖預覽；群組格狀縮圖（磁碟+記憶體快取）
  - 效能：50k 筆流暢操作（虛擬化、懶載入、背景 IO）
- **非目標**
  - 內建重複/相似度分析（外部流程完成，CSV 帶入）
  - 刪除的 Undo（以回收桶保險）

---

## 2. CSV 規格與匯出規則

- 欄位（順序固定）：
  - `GroupNumber, IsMark, IsLocked, FolderPath, FilePath, Capture Date, Modified Date, Creation Date, Shot Date, FileSize`
- 匯入：
  - 標頭對應必須；缺欄/型別錯誤以容錯策略處理並記錄日誌；忽略其他欄位
  - `FileSize` 欄位若為人類可讀字串（如 "1.44MB"），匯入後將在內部以檔案實際大小覆寫（Bytes），若已經是Bytes（整數），則直接使用
  - `Capture Date`：沿用舊欄位，視為來源拍攝日期備援
  - `Creation Date`：以檔案系統建立時間 `os.path.getctime` 取得（Windows 即建立時間）；匯入時若 CSV 無此欄位或空值，內部以檔案系統值覆寫
  - `Shot Date`：以 EXIF `DateTimeOriginal` 為主；匯入時若 CSV 無此欄位或空值，先嘗試 EXIF；若無 EXIF，則以 `Capture Date` 值備援
- 匯出：
  - 欄位順序維持一致
  - `FileSize` 一律輸出實際檔案大小 Bytes（整數）
  - 日期欄位一律輸出格式：`YYYY-MM-DD HH:MM:SS`；無值輸出空字串

---

## 3. 系統架構

- 分層：`App(UI) → Core(商業邏輯) → Infrastructure(IO/外部)`
- 依賴注入：簡易 Service Locator 或 `dependency-injector`
- 日誌：`loguru`（檔案輪替）

```mermaid
flowchart LR
  UI[PySide6 Views/ViewModels] --> Core[Core Services & Rule Engine]
  Core --> Repo[CSV Repository]
  Core --> Img[ImageService (Shell/WIC, Cache)]
  Core --> Del[DeleteService (Recycle Bin)]
  Core --> Cfg[Settings]
```text

---

## 4. 專案結構（目錄）

```text
photo_manager/
  app/
    views/            # MainWindow, GroupPanel, PhotoTable, PreviewPane, RulePanel
    viewmodels/       # MainVM, GroupVM, PhotoVM, RuleVM, Commands
    models/           # 僅 UI 狀態（排序/篩選/視圖設定）
    widgets/          # 縮圖格、對話框、進度指示
  core/
    models.py         # PhotoRecord, PhotoGroup 等
    rules/            # Rule, Condition, Action, RuleEngine, Commands(undo/redo)
    services/         # 介面與共用服務（I*）
    utils/            # 轉換、比較器、型別輔助
  infrastructure/
    csv_repository.py # 讀寫 CSV
    image_service.py  # 縮圖/原圖載入、快取、Shell/WIC
    delete_service.py # 回收桶刪除、檢查
    settings.py       # 設定載入/驗證
    logging.py        # 日誌初始化
  schemas/
    rules.schema.json # 規則 JSON Schema（AI/驗證用）
  samples/            # 範例 CSV 與規則（後續實作時補）
  main.py             # App 入口、組態注入、啟動 UI
```

---

## 5. 核心資料模型（Core）

- `PhotoRecord`
  - `group_number:int`
  - `is_mark:bool`
  - `is_locked:bool`（僅 App 內）
  - `folder_path:str`
  - `file_path:str`
  - `capture_date:datetime|None`
  - `modified_date:datetime|None`
  - `file_size_bytes:int`（以 `os.path.getsize` 重新讀取）
  - `gps_latitude:float|None`
  - `gps_longitude:float|None`
  - `pixel_height:int|None`, `pixel_width:int|None`
  - `dpi_width:int|None`, `dpi_height:int|None`
  - `orientation:int|None`
- `PhotoGroup`
  - `group_number:int`
  - `items:list[PhotoRecord]`
  - `is_expanded:bool`（UI 狀態）

---

## 6. 服務介面（Core → Infrastructure）

- `IPhotoRepository`
  - `load(csv_path:str) -> Iterable[PhotoRecord]`
  - `save(csv_path:str, groups:Iterable[PhotoGroup]) -> None`
- `IImageService`
  - `get_thumbnail(path:str, size:int) -> QImage | bytes`
  - `get_preview(path:str, max_side:int) -> QImage | bytes`
  - 具備磁碟+記憶體 LRU 快取；快取鍵：`sha1(path + mtime + size)`
- `IDeleteService`
  - `plan_delete(groups, selected_paths) -> DeletePlan`（略過 locked，彙總受影響群組/筆數、偵測全選群組）
  - `delete_to_recycle(paths:list[str]) -> DeleteResult`
  - `execute_delete(groups, plan, log_dir=None) -> DeleteResult`（執行並寫出 CSV 紀錄）
- `IRuleService`
  - `execute(groups, rule) -> Command`（可 `undo()`/`redo()`）
- `ISortService`
  - 多鍵排序組態與執行
- `ISettings`
  - 讀寫 `settings.json`（縮圖尺寸、快取大小、刪除策略、預設排序等）
- `IUndoRedoService`
  - `push(cmd:Command)`, `undo()`, `redo()`, 邏輯限定於狀態變更（非刪除）

---

## 7. 選擇（Select）對話框（Rule v0：UI-based Selection Rules）

對話框提供簡化批次選擇/取消選擇能力（Rule v0 為「UI-based Selection Rules」）：

- 元件：
  - 下拉選單 `Field`：列出主清單顯示的所有欄位（如 Group、File Name、Folder、Size(Bytes) 等可匹配項）
  - 文字框 `Regex`：接受使用者輸入，支援標準正則（RE），即時校驗格式
  - 按鈕：`Select`、`Unselect`
- 行為：
  - `Select`：遍歷所有檔案列，將欄位值符合 Regex 者勾選為 Selected
  - `Unselect`：遍歷所有檔案列，將欄位值符合 Regex 者取消勾選 Selected
  - 僅作用於檔案列（群組列不具勾選）

說明：目前規則能力由 UI 直接呼叫選取服務（批次勾選/取消），非通用規則引擎。此為 v0 能力，用以滿足批次操作需求。

---

## 8. UI/UX 與互動流程

- 主畫面布局：
  - 中：群組樹（`QTreeView`，父=Group、子=Photo）。第 0 欄為群組；子列包含 `Selected` 勾選框（僅檔案列）。
  - 右：預覽（單檔原圖；群組縮圖格）
  - 預設視窗大小：可用螢幕寬與高的 50%（約 1/4 面積）
  - 樹狀表頭：可拖曳、互動調整寬度；新增 `Group Count` 欄位，`Size (Bytes)` 獨立顯示
  - 預設行為：展開所有群組、各欄寬以「內容寬度」自動調整；調整分隔線以讓樹狀寬度恰好顯示當前欄位
  - 預覽捲動：若影像高度大於可見高度，使用垂直捲動條以完整檢視
- 操作：
  - 勾選/取消勾選（Selected）
  - 功能表：`File > Import/Export/ Delete Selected…`、`Select > Select by Field/Regex/…`
  - 刪除：
    - 摘要：顯示受影響群組/全部群組、將刪除檔案/總檔案；若包含全選群組，列出群組清單並需勾選同意
    - 執行：送回收桶（略過 locked）；寫刪除 CSV；自清單移除成功刪除檔案；移除僅剩單檔之群組；詢問是否覆寫來源 CSV
- 虛擬化與懶載入：
  - 群組先載入索引，展開時才載入子項（可選）
  - 縮圖在可見範圍內懶載入，背景預取鄰近項
  - 縮圖格大小：依 `settings.json` 的 `thumbnail_size` 設為上限，最小 200px，動態計算欄數

---

## 9. 影像處理策略（Windows / HEIC）

- 平台：Windows Only
- 主流程：
  1) `QImageReader` 嘗試載入（常見格式）
  2) 若為 HEIC/HEIF：
     - 已安裝 `pillow-heif`：以 Pillow 解碼並產生縮圖/預覽
     - 未安裝 `pillow-heif`：以 Windows Shell/WIC 取得指定尺寸縮圖（備援），確保不崩潰
  3) 單檔預覽需要更大尺寸時，再請求更大縮圖；若仍不足，提供「外部開啟」
- 快取：
  - 磁碟：`%LOCALAPPDATA%/PhotoManager/thumbs/{sha1(path+mtime+size)}.jpg`
  - 記憶體：LRU（容量可配置，預設 512 張）

---

## 10. 刪除與保護

- `send2trash` 送回收桶；`is_locked == True` 之項目一律跳過
- 刪除前檢查：
  - 集合中若包含任一群組「全選刪除」，需顯示強烈警告與二次確認對話框
  - 對話框列出涉及群組與各群刪除筆數，要求使用者逐步勾選確認或一次性確認
- 刪除結果：產生 CSV Log（時間、群組、檔案路徑、成功/失敗原因；成功與失敗都記錄）

---

## 11. 效能與穩定性

- 大量資料最佳化：
  - `QStandardItemModel` 並利用 Qt 內建排序（核心仍保留 SortService 以利匯出/後處理）
  - 延遲展開載入與釋放不可見群組的子項（可選進階）
  - 背景 IO：`QThreadPool` + `QRunnable`（CSV 讀取、檔案大小、縮圖）
  - 磁碟/記憶體快取（縮圖）、分批更新 UI（batched signals）
- 容錯：
  - 檔案不存在顯示占位縮圖；操作日誌記錄缺失項
  - CSV 個別列錯誤跳過並記錄

---

## 12. 設定與國際化

- `settings.json`：
  - `thumbnail_size`（256/512/1024）
  - `thumbnail_mem_cache`（如 512）
  - `thumbnail_disk_cache_dir`
  - `delete.confirm_group_full_delete`（true）
  - `sorting.defaults`（欄位/方向陣列）
  - `ui.locale`（"zh-TW"）
- 所有字串集中管理，預設中文

---

## 13. 測試策略

- 單元：
  - CSV 解析與容錯
  - 檔案大小 Bytes 重新讀取
  - 規則引擎（條件/聚合）與 Command 的 undo/redo（目前引擎為擴充點，未在執行路徑）
  - 刪除策略檢查（群組全選偵測、locked 跳過）
- 整合：
  - 匯入 → 套規則 → 選取/標記/鎖定 → 匯出 → 刪除（模擬 send2trash）
- UI：
  - ViewModel 行為測試；核心互動人測

---

## 14. 發佈與相依

- 相依套件：`PySide6`, `send2trash`, `loguru`, `pydantic`(可選), `pywin32`
- 打包：`PyInstaller`（單檔或資料夾模式）
- 系統前置：Microsoft Store 安裝「HEIF Image Extensions」

---

## 15. 風險與緩解

- HEIC 無法取大圖：以 WIC 最大縮圖 + 外部開啟備援
- 50k 筆縮圖壓力：僅對可見與當前群組產生；可選背景預產
- 記憶體佔用：虛擬化、懶載入、LRU 快取、釋放不可見節點

---

## 16. 里程碑與 DoD

- M1 最小可用版本（Week 1）
  - 匯入/匯出 CSV、群組樹與列表（檔案列具 Selected 勾選）、單/群縮圖預覽（含快取）、基本排序（Qt 內建）、Select 對話框（Field/Regex 的 Select/Unselect）、刪除（摘要/強制確認/回收桶/自動 CSV Log/清單修剪/詢問覆寫 CSV）
  - DoD：50k 筆可順暢瀏覽與基本操作；文件與打包指引齊備
- M2 穩定與優化（Week 2）
  - 進階篩選、更多聚合器、設定面板、操作日誌匯出、UI 優化

---

## 17. 版本管理

- 分支策略：`main` 穩定、`feature/*` 開發分支，PR 後合併
- 版本：語意化版本（SemVer），以 M1 為 `v1.0.0`

---

## 18. 附錄：範例 CSV 標頭與列

```csv
GroupNumber,IsMark,IsLocked,FolderPath,FilePath,Capture Date,Modified Date,FileSize
1,0,0,H:\\Photos\\MobileBackup\\iPhone\\2023\\01\\,h:\\photos\\mobilebackup\\iphone\\2023\\01\\img_7611_original.heic,2023-01-27 13:56:23,2023-01-27 12:56:23,1.44MB
```

匯入後 `FileSize` 將以檔案實際大小（Bytes）覆蓋並於匯出輸出整數。

---

## 19. 定義與術語

- Selected：UI 當前選取狀態（不直接對應 CSV 欄位）
- Marked：`IsMark == 1`
- Locked：`IsLocked == 1`（僅 App 內，非檔案屬性）

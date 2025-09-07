## Photo Manager 設計路線 v1.0（Python 版）

本文件為本專案之唯一權威設計依據（Single Source of Truth）。後續開發、測試與發佈以此文件為準。

### 決策總結（Final）

- **語言/平台**: Python 3.11+（Windows）
- **GUI**: PySide6（Qt 6）
- **HEIC**: 依賴系統的 HEIF Image Extensions；以 Windows Shell/WIC 取得縮圖/預覽
- **刪除**: 預設送資源回收桶（send2trash）
- **鎖定**: 僅 App 內部狀態，不改動檔案屬性
- **匯出 FileSize**: 一律重新讀取實體檔案大小，以 Bytes（整數）輸出
- **資料量級**: 約 20,000 組群、50,000 檔案（需虛擬化、懶載入與快取）
- **分群來源**: 一律由 CSV 提供；本 App 不做相似/重複分析
- **Undo/Redo**: 僅針對「規則導致的 標記/鎖定/選取 變更」支援；刪除不支援 Undo
- **群組全選刪除策略**: 允許，但跳出強烈警告並需二次確認

---

## 1. 目標與非目標

- **目標**
  - 讀取/顯示大量 CSV 之照片資料，依 `GroupNumber` 分群可折疊/展開
  - 規則引擎：條件式與聚合式（perGroup / global），支援 Undo/Redo（僅狀態變更）
  - 操作：標記/取消、鎖定/取消、刪除（回收桶）、多鍵排序、匯入/匯出
  - 預覽：單檔原圖預覽；群組格狀縮圖（磁碟+記憶體快取）
  - 效能：50k 筆流暢操作（虛擬化、懶載入、背景 IO）
- **非目標**
  - 內建重複/相似度分析（外部流程完成，CSV 帶入）
  - 刪除的 Undo（以回收桶保險）

---

## 2. CSV 規格與匯出規則

- 欄位（順序固定）：
  - `GroupNumber, IsMark, IsLocked, FolderPath, FilePath, Capture Date, Modified Date, FileSize`
- 匯入：
  - 標頭對應必須；缺欄/型別錯誤以容錯策略處理並記錄日誌；忽略其他欄位
  - `FileSize` 欄位若為人類可讀字串（如 "1.44MB"），匯入後將在內部以檔案實際大小覆寫（Bytes），若已經是Bytes（整數），則直接使用
- 匯出：
  - 欄位順序維持一致
  - `FileSize` 一律輸出實際檔案大小 Bytes（整數）

---

## 3. 系統架構

- 分層：`App(UI) → Core(商業邏輯) → Infrastructure(IO/外部)`
- 依賴注入：簡易 Service Locator 或 `dependency-injector`
- 日誌：`loguru`（檔案輪替）

```mermaid
flowchart LR
  UI[PySide6 Views/ViewModels] --> Core[Core Services & Rule Engine]
  Core --> Repo[IPhotoRepository (CSV)]
  Core --> Img[IImageService (Shell/WIC, Cache)]
  Core --> Del[IDeleteService (Recycle Bin)]
  Core --> Cfg[ISettings]
```

---

## 4. 專案結構（目錄）

```
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
  - `delete_to_recycle(paths:list[str]) -> DeleteResult`（略過 locked，回傳成功/失敗明細）
  - 刪除前檢查群組全選，回傳需二次確認清單
- `IRuleService`
  - `execute(groups, rule) -> Command`（可 `undo()`/`redo()`）
- `ISortService`
  - 多鍵排序組態與執行
- `ISettings`
  - 讀寫 `settings.json`（縮圖尺寸、快取大小、刪除策略、預設排序等）
- `IUndoRedoService`
  - `push(cmd:Command)`, `undo()`, `redo()`, 邏輯限定於狀態變更（非刪除）

---

## 7. 規則引擎

- 範圍：`global` / `perGroup`
- 類型：
  - 條件式（欄位比較、regex、contains/startsWith/endsWith）
  - 聚合式（每群 min/max/first/last/shortest/longest）
- 動作：`mark`, `lock`, `aggregateSelect`, `selectBySameFolder`
- 執行：回傳一個 `Command`（封裝所有受影響項目的狀態變更）；支援 undo/redo

### 規則 JSON Schema（簡版）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "PhotoManager Rule",
  "type": "object",
  "properties": {
    "name": { "type": "string" },
    "scope": { "enum": ["global", "perGroup"] },
    "conditions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "field": { "type": "string" },
          "operator": { "enum": ["eq","neq","gt","lt","regex","contains","startsWith","endsWith"] },
          "value": {}
        },
        "required": ["field","operator","value"]
      }
    },
    "actions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "type": { "enum": ["mark","lock","aggregateSelect","selectBySameFolder"] },
          "value": {},
          "field": { "type": "string" },
          "operator": { "enum": ["min","max","first","last","shortest","longest"] },
          "pathField": { "type": "string" },
          "regex": { "type": "string" },
          "mark": { "type": "boolean" },
          "lock": { "type": "boolean" }
        },
        "required": ["type"]
      }
    }
  },
  "required": ["name","scope","actions"]
}
```

### 規則範例

```json
{
  "name": "每群選最大檔案",
  "scope": "perGroup",
  "actions": [
    { "type": "aggregateSelect", "field": "file_size_bytes", "operator": "max", "mark": true }
  ]
}
```

```json
{
  "name": "選同資料夾",
  "scope": "global",
  "actions": [
    { "type": "selectBySameFolder", "pathField": "folder_path", "regex": ".*\\\\iPhone\\\\2023\\\\02\\\\.*", "mark": true }
  ]
}
```

```json
{
  "name": "檔名後綴",
  "scope": "perGroup",
  "conditions": [
    { "field": "file_path", "operator": "regex", "value": ".*_original\\.heic$" }
  ],
  "actions": [
    { "type": "mark", "value": true },
    { "type": "lock", "value": false }
  ]
}
```

---

## 8. UI/UX 與互動流程

- 主畫面布局：
  - 左：規則與快速篩選（已鎖定/已標記/路徑 Regex 等）
  - 中：群組樹（`QTreeView`，父=Group、子=Photo）+ 列表欄位可排序
  - 右：預覽（單檔原圖；群組縮圖格）
- 操作：
  - 勾選列 → 選取（Selected）
  - 工具列：標記/取消、鎖定/取消、刪除、排序、匯入/匯出、規則執行/撤銷/重做
  - 刪除：
    - 跳出摘要：將刪除 N 筆，其中 M 組為「全選刪除」→ 需二次確認
    - 可顯示群組清單與筆數，使用者逐項確認
- 虛擬化與懶載入：
  - 群組先載入索引，展開時才載入子項（可選）
  - 縮圖在可見範圍內懶載入，背景預取鄰近項

---

## 9. 影像處理策略（系統 HEIC）

- 主流程：
  1) `QImageReader` 嘗試載入（常見格式）
  2) 若為 HEIC/HEIF 或載入失敗 → 以 Windows Shell/WIC 取得指定尺寸縮圖
  3) 單檔預覽需要更大尺寸時，再請求更大縮圖；若仍不足，提供「外部開啟」
- 快取：
  - 磁碟：`%LOCALAPPDATA%/PhotoManager/thumbs/{sha1(path+mtime+size)}.jpg`
  - 記憶體：LRU（容量可配置，預設 256–512 張）

---

## 10. 刪除與保護

- `send2trash` 送回收桶；`is_locked == True` 之項目一律跳過
- 刪除前檢查：
  - 集合中若包含任一群組「全選刪除」，需顯示強烈警告與二次確認對話框
  - 對話框列出涉及群組與各群刪除筆數，要求使用者逐步勾選確認或一次性確認
- 刪除結果：產生 CSV Log（時間、群組、檔案路徑、成功/失敗原因）

---

## 11. 效能與穩定性

- 大量資料最佳化：
  - `QAbstractItemModel` + `QSortFilterProxyModel`，僅維護必要狀態
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
  - 規則引擎（條件/聚合）與 Command 的 undo/redo
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
  - 匯入/匯出 CSV、群組樹與列表、單/群縮圖預覽（含快取）、基本排序、規則執行（含 Undo/Redo）、刪除（警告+回收桶）
  - DoD：50k 筆可順暢瀏覽與基本操作；文件與打包指引齊備
- M2 穩定與優化（Week 2）
  - 進階篩選、更多聚合器、設定面板、操作日誌匯出、UI 優化

---

## 17. 版本管理

- 分支策略：`main` 穩定、`feature/*` 開發分支，PR 後合併
- 版本：語意化版本（SemVer），以 M1 為 `v1.0.0`

---

## 18. 附錄：範例 CSV 標頭與列

```
GroupNumber,IsMark,IsLocked,FolderPath,FilePath,Capture Date,Modified Date,FileSize
1,0,0,H:\\Photos\\MobileBackup\\iPhone\\2023\\01\\,h:\\photos\\mobilebackup\\iphone\\2023\\01\\img_7611_original.heic,2023-01-27 13:56:23,2023-01-27 12:56:23,1.44MB
```

匯入後 `FileSize` 將以檔案實際大小（Bytes）覆蓋並於匯出輸出整數。

---

## 19. 定義與術語

- Selected：UI 當前選取狀態（不直接對應 CSV 欄位）
- Marked：`IsMark == 1`
- Locked：`IsLocked == 1`（僅 App 內，非檔案屬性）

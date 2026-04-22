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
- **分群來源**: 由 migration_manifest.sqlite 提供；本 App 不做相似/重複分析
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

## 2. 系統架構

- 分層：`App(UI) → Core(商業邏輯) → Infrastructure(IO/外部)`
- 服務在 `main.py` 中直接實例化並注入（不使用框架）
- 日誌：`loguru`（檔案輪替）

```mermaid
flowchart LR
  UI[PySide6 Views/ViewModels] --> Core[Core Services & Rule Engine]
  Core --> Repo[ManifestRepository]
  Core --> Img[ImageService (Shell/WIC, Cache)]
  Core --> Del[DeleteService (Recycle Bin)]
  Core --> Cfg[Settings]
```

---

## 4. 專案結構（目錄）

```text
photo-manager/
  run.bat                  # Launch GUI (activates .venv automatically)
  main.py                  # PySide6 GUI entry point
  scan.py                  # Deduplication scanner CLI
  review.py                # REVIEW_DUPLICATE triage CLI
  pyproject.toml           # Tool configuration (Black, isort, Ruff, Pylint)
  settings.json            # User configuration (source paths, thumbnail cache, …)

  scanner/                 # Scanner engine (no Qt dependency)
    media.py               # Extensions, magic-byte detection, filename parsing
    walker.py              # Directory walk + Live Photo pairing
    hasher.py              # SHA-256 + pHash (Pillow / pillow-heif / rawpy)
    exif.py                # Batch EXIF date reads via exiftool -stay_open
    dedup.py               # Classification: exact → format → near-dup → UNDATED
    manifest.py            # SQLite writer + summary printer

  app/
    views/
      main_window.py       # Main window — wires all components
      tree_model_builder.py
      constants.py         # Column indices and header labels
      components/
        menu_controller.py
        tree_controller.py
        selection_controller.py
      handlers/
        file_operations.py  # set_decision, batch_set_decision, execute_action
        context_menu.py     # Right-click Set Action routing
      dialogs/
        scan_dialog.py
        execute_action_dialog.py
        group_deletion_check_dialog.py  # Safety check for complete-group deletes
        select_dialog.py                # Select by Field/Regex dialog
        filters_dialog.py  # [deprecated — legacy stub, pragma: no cover]
        rules_dialog.py    # [deprecated — legacy stub, pragma: no cover]
      workers/
        scan_worker.py          # Background QThread for scan pipeline
        manifest_load_worker.py # Background QThread for manifest load
    viewmodels/
      main_vm.py           # Groups/marks logic; loads manifest

  core/
    models.py              # PhotoRecord (action, user_decision), PhotoGroup
    rules/                 # [orphaned — empty, no implementation]
    services/
      interfaces.py        # DeleteResult, DeletePlan, IListService
      selection_service.py # RegexSelectionService
      sort_service.py      # SortService

  infrastructure/
    manifest_repository.py  # load/save/batch_update_decisions; mark_executed()
    delete_service.py
    settings.py

  tests/                   # 270+ tests
    conftest.py
    test_dedup.py           test_hasher.py          test_walker.py
    test_review.py          test_manifest_repository.py
    test_scanner_manifest.py  test_scanner_exif.py
    test_settings.py        test_utils.py           test_delete_service.py
    test_main_vm.py         test_file_operations.py test_sort_service.py
    test_selection_service.py  test_context_menu.py
    test_execute_action_dialog.py  test_group_deletion_check_dialog.py
    test_manifest_load_worker.py
```

---

## 5. 核心資料模型（Core）

- `PhotoRecord`
  - `group_number:int`
  - `is_mark:bool` — Sel checkbox 狀態（CSV workflow 使用）
  - `is_locked:bool` — 保護欄位（目前在 GUI 中一律為 False；Delete service 仍尊重此欄）
  - `folder_path:str`
  - `file_path:str`
  - `capture_date:datetime|None`
  - `modified_date:datetime|None`
  - `creation_date:datetime|None` — 檔案系統建立時間
  - `shot_date:datetime|None` — EXIF DateTimeOriginal
  - `file_size_bytes:int`（以 `os.path.getsize` 重新讀取）
  - `action:str` — Scanner 分類（唯讀）：`EXACT` / `REVIEW_DUPLICATE` / `MOVE` / `KEEP` / `UNDATED` / `""` (reference role)
  - `user_decision:str` — 使用者操作決定（可寫）：`"delete"` / `"keep"` / `""` (undecided)
- `PhotoGroup`
  - `group_number:int`
  - `items:list[PhotoRecord]`
  - `is_expanded:bool`（UI 狀態）

**action vs user_decision 分離原則：**

| 欄位 | 來源 | 用途 | 可變？ |
|------|------|------|-------|
| `action` | Scanner 寫入，manifest 載入 | 分類標籤（col 0 Match 顯示依據） | 否（唯讀）|
| `user_decision` | 使用者透過 Set Action 設定 | 實際檔案操作（col 2 Action 顯示依據）| 是 |

---

## 6. 服務介面（Core → Infrastructure）

`core/services/interfaces.py` 定義跨層共用的資料類別與服務介面：

- `DeleteResult` — 刪除操作結果（`success_paths`、`failed`）
- `DeletePlan` — 計劃刪除操作（`delete_paths`、`group_summaries`）
- `DeletePlanGroupSummary` — 單群組刪除摘要（`group_number`、`selected_count`、`is_full_delete`）
- `RemoveResult` — 從清單移除的結果（`success_paths`、`failed`）
- `IListService` — 從清單移除檔案的介面（`remove_from_list`）

排序與選取服務在 `core/services/sort_service.py`（`SortService`）與 `core/services/selection_service.py`（`RegexSelectionService`）中實作。

縮圖與影像服務在 `infrastructure/` 中以具體類別實作（無抽象介面）。

---

## 7. 選擇（Select）對話框（Rule v0：UI-based Selection Rules）

對話框提供簡化批次選擇/取消選擇能力（Rule v0 為「UI-based Selection Rules」）：

- 元件：
  - 下拉選單 `Field`：列出主清單顯示的所有欄位（如 Match、Action、File Name、Folder、Size(Bytes)、Group Count、Creation Date、Shot Date 等可匹配項）
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
  - 功能表：`File > Set Action to Activated Files > delete/keep`、`File > Set Action to Selected (Sel) Files > delete/keep`、`File > Execute Action…`、`Select > Select by Field/Regex/…`
  - 刪除：
    - 摘要：顯示受影響群組/全部群組、將刪除檔案/總檔案；若包含全選群組，列出群組清單並需勾選同意
    - 執行：送回收桶（略過 locked）；自清單移除成功刪除檔案；移除僅剩單檔之群組
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

> **注意**：`delete.confirm_group_full_delete` 與 `ui.locale` 目前儲存於 `settings.json` 但尚未被應用程式碼讀取。

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
  - 載入 manifest、群組樹與列表（檔案列具 Selected 勾選）、單/群縮圖預覽（含快取）、基本排序（Qt 內建）、Select 對話框（Field/Regex 的 Select/Unselect）、Set Action via Activated Files 或 Selected (Sel) Files（delete/keep）、Save Manifest Decisions（另存新檔對話框）、Execute Action（回收桶/清單修剪）
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

- **Activated Files（Highlighted）**：樹狀視圖中以點擊或多選反白的列（`QTreeView` selectionModel rows）；透過 `File > Set Action to Activated Files` 批次設定 user_decision
- **Selected（Sel）**：UI Sel 勾選框狀態，用於批次操作目標選擇；透過 `File > Set Action to Selected (Sel) Files` 批次設定 user_decision
- **Marked（IsMark）**：`is_mark == True`（CSV workflow 使用；manifest workflow 中由 Sel 取代）
- **Locked（IsLocked）**：`is_locked == True`（僅 App 內狀態；Delete service 跳過 locked 項目；manifest workflow 中目前一律為 False）
- **action**：Scanner 分類（`EXACT` / `REVIEW_DUPLICATE` / `MOVE` / `KEEP` / `UNDATED`）；唯讀，col 0 "Match" 顯示
- **user_decision**：使用者決定（`"delete"` / `"keep"` / `""` / `"removed"`）；col 2 "Action" 顯示；透過 Set Action 設定；`"removed"` 為系統內部哨兵值，表示已從審查清單移除

---

## 20. Scanner Architecture (Phase 1 — pHash Deduplication)

Replaces Cisdem Duplicate Finder. Produces `migration_manifest.sqlite` which
the review GUI loads via `ManifestRepository`.

### Pipeline stages

```
scan_sources()  →  batch_read_dates()  →  compute_sha256/phash()  →  classify()  →  write_manifest()
   walker           exif (exiftool)         hasher                    dedup          manifest
```

### Module responsibilities

| Module | File | Responsibility |
|--------|------|---------------|
| `scanner.walker` | `scanner/walker.py` | rglob each source dir; detect media type; pair Live Photos (same-stem HEIC+MOV); yield `FileRecord` |
| `scanner.exif` | `scanner/exif.py` | Persistent exiftool `-stay_open` process; batch EXIF reads (chunked ≤500/call) |
| `scanner.hasher` | `scanner/hasher.py` | SHA-256 (all files) + pHash via `imagehash` (photos only; RAW uses embedded JPEG thumb) |
| `scanner.dedup` | `scanner/dedup.py` | Group by SHA-256 → EXACT_DUPLICATE; group by pHash → FORMAT_DUPLICATE or REVIEW_DUPLICATE; apply RAW+lossy exception; propagate Live Photo pairs |
| `scanner.manifest` | `scanner/manifest.py` | Write SQLite `migration_manifest`; `print_summary` action counts |
| `scanner.media` | `scanner/media.py` | `MEDIA_EXTENSIONS`, `SKIP_FILENAMES`, Live Photo pair logic, `_magic_type` |

### Classification rules

| Condition | Action |
|-----------|--------|
| SHA-256 match | `EXACT` (exact duplicate — lower-priority copy) |
| pHash hamming == 0, both lossy | `EXACT` lower-priority (format duplicate) |
| pHash hamming == 0, one RAW + one lossy | Both `MOVE` (complementary) |
| pHash hamming 1–10 | `REVIEW_DUPLICATE` (human review in GUI) |
| No EXIF DateTimeOriginal | `UNDATED` |
| Otherwise | `MOVE` |

Source priority (EXACT_DUPLICATE): `iphone > takeout > jdrive`  
Format priority (FORMAT_DUPLICATE): `heic > jpeg > png > others`

### GUI integration

`ScanDialog` (in `app/views/dialogs/`) lets the user pick source folders and
runs `ScanWorker` (a `QThread`) in the background. On completion the manifest
path is passed to `ManifestLoadWorker` (a `QThread` in `app/views/workers/`),
which calls `ManifestRepository.load()` in the background and emits
`finished(list[PhotoGroup])` when done. `FileOperationsHandler` receives the
signal and populates the review tree — keeping the UI fully responsive during load.

`ManifestRepository.save()` writes `user_decision` values (`delete`/`keep`/`""`) back
to the manifest for each record.  The `action` column (scanner classification) is
never modified by the GUI.

### CLI

```bash
python scan.py \
  --source iphone="\NAS\Photos\MobileBackup" \
  --source takeout="D:\Downloads\Takeout" \
  --source jdrive="J:\圖片" \
  --output migration_manifest.sqlite

python scan.py ... --limit 200 --dry-run   # bounded debug run
```

### manifest schema (key columns)

| Column | Type | Notes |
|--------|------|-------|
| `source_path` | TEXT | Absolute path |
| `source_label` | TEXT | `iphone` / `takeout` / `jdrive` |
| `dest_path` | TEXT | NULL for EXACT/UNDATED |
| `action` | TEXT | Scanner classification: `EXACT` / `REVIEW_DUPLICATE` / `MOVE` / `KEEP` / `UNDATED` |
| `source_hash` | TEXT | SHA-256 hex |
| `phash` | TEXT | 64-bit perceptual hash hex; NULL for video |
| `hamming_distance` | INTEGER | Distance to `duplicate_of`'s phash |
| `duplicate_of` | TEXT | source_path of kept file |
| `executed` | INTEGER | 0=pending 1=done |
| `user_decision` | TEXT | User's planned file operation: `delete` / `keep` / `""` (undecided) / `"removed"` (hidden from review) |
| `file_size_bytes` | INTEGER | Cached at scan time; NULL in pre-existing manifests (auto-migrated) |
| `shot_date` | TEXT | ISO 8601 from EXIF DateTimeOriginal; NULL if not available |
| `creation_date` | TEXT | ISO 8601 filesystem ctime at scan time |
| `mtime` | TEXT | ISO 8601 filesystem mtime at scan time |

All five nullable columns (`user_decision`, `file_size_bytes`, `shot_date`,
`creation_date`, `mtime`) are added automatically to older manifests via
`ALTER TABLE … ADD COLUMN` migrations on first load.

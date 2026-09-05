// FsBrowser — a filesystem picker modal backed by GET /api/fs/browse.
//
// Three modes (mirrors Qt's QFileDialog variants):
//   "directory" — pick an existing folder   (scan source paths)
//   "file"      — pick an existing file      (Open Manifest)
//   "save"      — pick a folder + filename   (scan output .db)
//
// The server returns OS-native paths (e.g. "C:\Users\…" on Windows, "/…" on
// POSIX). We never parse them — navigation is driven entirely by the
// `path`/`parent`/`entries` the API hands back, so no client-side path logic
// leaks in. The only join we do is save-mode `<cwd><sep><filename>`, with the
// separator sniffed from cwd (Python's pathlib normalises mixed separators on
// Windows anyway, but a clean display is nicer).

import { useState, useEffect, useCallback, useRef } from "react";

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { browseFs } from "@/api/client";
import type { FsEntry } from "@/api/types";
import { useT } from "@/i18n/useT";
import {
  FS_BROWSER,
  FS_BROWSER_UP,
  FS_BROWSER_PATH,
  FS_BROWSER_FILENAME,
  FS_BROWSER_CONFIRM,
  FS_BROWSER_CANCEL,
  FS_BROWSER_ENTRY,
  FS_BROWSER_ERROR,
} from "@/testids";

// ---------------------------------------------------------------------------
// Types + helpers
// ---------------------------------------------------------------------------

export type FsBrowserMode = "directory" | "file" | "save";

export interface FsBrowserProps {
  open: boolean;
  mode: FsBrowserMode;
  title: string;
  /** Directory to open at; defaults to the filesystem roots ("This PC"). */
  initialPath?: string;
  /** Save mode: pre-filled file name (e.g. "migration_manifest.sqlite"). */
  defaultFilename?: string;
  onConfirm: (path: string) => void;
  onOpenChange: (open: boolean) => void;
}

/** Join a directory with a filename using the directory's own separator. */
function joinPath(dir: string, name: string): string {
  const sep = dir.includes("\\") ? "\\" : "/";
  return dir.endsWith(sep) ? `${dir}${name}` : `${dir}${sep}${name}`;
}

/** Strip the last path segment (and any trailing separators). "" if none. */
function parentOf(p: string): string {
  return p.replace(/[/\\]+$/, "").replace(/[/\\][^/\\]*$/, "");
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function FsBrowser({
  open,
  mode,
  title,
  initialPath,
  defaultFilename,
  onConfirm,
  onOpenChange,
}: FsBrowserProps) {
  const t = useT();

  const [cwd, setCwd] = useState(""); // "" → filesystem roots
  const [parent, setParent] = useState<string | null>(null);
  const [entries, setEntries] = useState<FsEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [filename, setFilename] = useState("");

  // Monotonic request id — a newer load() supersedes any in-flight one, so a
  // slow earlier response (rapid clicks, or React StrictMode's double-invoke)
  // can't clobber the latest directory.
  const seqRef = useRef(0);

  const load = useCallback(async (path: string): Promise<boolean> => {
    const seq = ++seqRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await browseFs(path === "" ? undefined : path);
      if (seq !== seqRef.current) return true; // superseded by a newer load
      setCwd(res.path);
      setParent(res.parent);
      setEntries(res.entries);
      return true;
    } catch (e) {
      if (seq !== seqRef.current) return false; // superseded — drop stale error
      setError(e instanceof Error ? e.message : String(e));
      setEntries([]);
      return false;
    } finally {
      if (seq === seqRef.current) setLoading(false);
    }
  }, []);

  // Reset and load the initial directory whenever the dialog opens. The
  // initialPath is a best-effort hint (it may be a file path, or a partial
  // path the user typed); if it can't be listed, fall back to its parent
  // directory (so a manifest *file* hint opens at its folder) and finally to
  // the roots, so navigation is never dead-ended on an error screen.
  useEffect(() => {
    if (!open) return;
    setSelectedFile(null);
    setFilename(defaultFilename ?? "");
    const hint = initialPath ?? "";
    void (async () => {
      if (await load(hint)) return;
      if (hint === "") return;
      const parent = parentOf(hint);
      if (parent !== "" && parent !== hint && (await load(parent))) return;
      await load("");
    })();
  }, [open, initialPath, defaultFilename, load]);

  const handleEntryClick = useCallback(
    (entry: FsEntry) => {
      if (entry.is_dir) {
        setSelectedFile(null);
        void load(entry.path);
      } else if (mode === "file") {
        setSelectedFile(entry.path);
      } else if (mode === "save") {
        setFilename(entry.name);
      }
    },
    [load, mode]
  );

  const handleEntryDouble = useCallback(
    (entry: FsEntry) => {
      if (!entry.is_dir && mode === "file") {
        onConfirm(entry.path);
        onOpenChange(false);
      }
    },
    [mode, onConfirm, onOpenChange]
  );

  const atRoots = cwd === "";

  const handleUp = useCallback(() => {
    setSelectedFile(null);
    void load(parent ?? ""); // null parent (drive root) → back to roots
  }, [parent, load]);

  // Confirm target path per mode (null = confirm disabled).
  let confirmPath: string | null = null;
  if (mode === "directory") {
    confirmPath = atRoots ? null : cwd;
  } else if (mode === "file") {
    confirmPath = selectedFile;
  } else {
    confirmPath = !atRoots && filename.trim() !== "" ? joinPath(cwd, filename.trim()) : null;
  }

  const handleConfirm = useCallback(() => {
    if (confirmPath !== null) {
      onConfirm(confirmPath);
      onOpenChange(false);
    }
  }, [confirmPath, onConfirm, onOpenChange]);

  // directory mode shows folders only; file/save also show files.
  const visible = mode === "directory" ? entries.filter((e) => e.is_dir) : entries;

  const confirmLabel =
    mode === "directory"
      ? t("web.browse.select_folder", "Select folder")
      : mode === "save"
      ? t("web.browse.save", "Save")
      : t("web.browse.select_file", "Open");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid={FS_BROWSER} className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        {/* Path bar + Up */}
        <div className="mt-2 flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-testid={FS_BROWSER_UP}
            disabled={atRoots}
            onClick={handleUp}
          >
            {t("web.browse.up", "↑ Up")}
          </Button>
          <span
            data-testid={FS_BROWSER_PATH}
            className="min-w-0 flex-1 truncate font-mono text-xs text-neutral-600"
            title={atRoots ? undefined : cwd}
          >
            {atRoots ? t("web.browse.roots", "This PC") : cwd}
          </span>
        </div>

        {/* Entry list */}
        <div className="mt-2 h-64 overflow-auto rounded border border-neutral-200">
          {loading ? (
            <p className="p-3 text-sm text-neutral-400">
              {t("web.browse.loading", "Loading…")}
            </p>
          ) : error !== null ? (
            <p
              data-testid={FS_BROWSER_ERROR}
              role="alert"
              className="p-3 text-sm text-red-600"
            >
              {error}
            </p>
          ) : visible.length === 0 ? (
            <p className="p-3 text-sm text-neutral-400">
              {t("web.browse.empty", "Empty folder")}
            </p>
          ) : (
            <ul role="list">
              {visible.map((entry) => {
                const isSelected = !entry.is_dir && selectedFile === entry.path;
                const dimmed = !entry.is_dir && mode === "save" && !entry.is_manifest;
                return (
                  <li key={entry.path}>
                    <button
                      type="button"
                      data-testid={FS_BROWSER_ENTRY}
                      data-kind={entry.is_dir ? "dir" : "file"}
                      onClick={() => handleEntryClick(entry)}
                      onDoubleClick={() => handleEntryDouble(entry)}
                      className={cn(
                        "flex w-full items-center gap-2 px-3 py-1 text-left text-sm hover:bg-neutral-100",
                        isSelected && "bg-blue-50",
                        dimmed && "text-neutral-400"
                      )}
                    >
                      <span aria-hidden="true">
                        {entry.is_dir ? "📁" : entry.is_manifest ? "🗃" : "📄"}
                      </span>
                      <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                      {entry.is_manifest && (
                        <span className="text-xs text-blue-600">
                          {t("web.browse.manifest_tag", "manifest")}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* Filename input (save mode only) */}
        {mode === "save" && (
          <div className="mt-2 flex items-center gap-2">
            <label
              htmlFor="fs-browser-filename-input"
              className="text-sm text-neutral-700"
            >
              {t("web.browse.filename", "File name:")}
            </label>
            <input
              id="fs-browser-filename-input"
              data-testid={FS_BROWSER_FILENAME}
              type="text"
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
              className="flex-1 rounded border border-neutral-300 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400"
            />
          </div>
        )}

        <DialogFooter className="mt-4">
          <Button
            type="button"
            variant="outline"
            data-testid={FS_BROWSER_CANCEL}
            onClick={() => onOpenChange(false)}
          >
            {t("web.browse.cancel", "Cancel")}
          </Button>
          <Button
            type="button"
            data-testid={FS_BROWSER_CONFIRM}
            disabled={confirmPath === null}
            onClick={handleConfirm}
          >
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

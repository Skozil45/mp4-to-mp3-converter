"""MP4 to MP3 Converter - a Windows 11 style desktop app (Python + CustomTkinter + FFmpeg)."""
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except Exception:  # drag and drop is optional
    HAS_DND = False

APP_NAME = "MP4 to MP3 Converter"
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv"}
QUALITIES = ["128 kbps", "192 kbps", "256 kbps", "320 kbps"]
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

BG, CARD, ROW, BORDER = "#1c1d20", "#26272b", "#2f3136", "#383a40"
TEXT, MUTED, DARK = "#f3f4f6", "#9ca0a8", "#08131d"
ACCENT, ACCENT_HOVER, ACCENT_DEEP = "#4cc2ff", "#7ad2ff", "#1f6fbf"
OK, BAD = "#6ccb5f", "#ff6b6b"
FONT = "Segoe UI"


# ---------- FFmpeg helpers ----------
def find_ffmpeg():
    """Use the FFmpeg bundled with imageio-ffmpeg, else one on PATH."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def parse_clock(value):
    m = re.match(r"(\d+):(\d+):(\d+(?:\.\d+)?)", value.strip())
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None


def get_duration(ffmpeg, path):
    p = subprocess.run([ffmpeg, "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    m = re.search(r"Duration:\s*([\d:.]+)", p.stderr)
    return (parse_clock(m.group(1)) or 0) if m else 0


def unique_path(path, taken=()):
    """Never overwrite: song.mp3 -> song (1).mp3 -> song (2).mp3"""
    n, candidate = 0, path
    while candidate.exists() or candidate in taken:
        n += 1
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
    return candidate


def shorten(text, limit=46):
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Converter(threading.Thread):
    """Runs FFmpeg for each job in the background and reports through a queue."""

    def __init__(self, ffmpeg, jobs, bitrate, events):
        super().__init__(daemon=True)
        self.ffmpeg, self.jobs, self.bitrate, self.events = ffmpeg, jobs, bitrate, events
        self.stop_flag = threading.Event()
        self.proc = None

    def cancel(self):
        self.stop_flag.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def run(self):
        for idx, (src, dst) in enumerate(self.jobs):
            if self.stop_flag.is_set():
                break
            self.events.put(("start", idx))
            try:
                self._convert(idx, src, dst)
                if self.stop_flag.is_set():
                    break
                self.events.put(("done", idx))
            except Exception as exc:
                if self.stop_flag.is_set():
                    break
                self.events.put(("error", idx, str(exc)))
        self.events.put(("finished", self.stop_flag.is_set()))

    def _convert(self, idx, src, dst):
        total = get_duration(self.ffmpeg, src)
        cmd = [self.ffmpeg, "-y", "-i", str(src), "-vn", "-map", "0:a:0",
               "-codec:a", "libmp3lame", "-b:a", self.bitrate,
               "-progress", "pipe:1", "-nostats", "-loglevel", "error", str(dst)]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace",
                                     creationflags=NO_WINDOW)
        problems = []
        for line in self.proc.stdout:
            line = line.strip()
            if line.startswith("out_time="):
                secs = parse_clock(line[9:])
                if secs is not None and total:
                    self.events.put(("progress", idx, min(secs / total, 1.0)))
            elif line and not re.match(r"^[a-z_0-9]+=", line):
                problems.append(line)
        code = self.proc.wait()
        if code != 0:
            Path(dst).unlink(missing_ok=True)
            raise RuntimeError(" ".join(problems[-3:]) if problems else f"FFmpeg exited with code {code}")


# ---------- UI ----------
_Base = (ctk.CTk, TkinterDnD.DnDWrapper) if HAS_DND else (ctk.CTk,)


class App(*_Base):
    def __init__(self):
        super().__init__()
        if HAS_DND:
            self.TkdndVersion = TkinterDnD._require(self)
        self.ffmpeg = find_ffmpeg()
        self.items, self.run_items, self.failures = [], [], []
        self.events = queue.Queue()
        self.worker, self.busy = None, False
        self.finished_count, self.out_dir, self.last_dir = 0, None, None

        self.title(APP_NAME)
        self.geometry("780x720")
        self.minsize(700, 640)
        self.configure(fg_color=BG)
        self._build()
        if HAS_DND:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", lambda e: self.add_files(self.tk.splitlist(e.data)))
        self.after(100, self._poll)
        if not self.ffmpeg:
            messagebox.showerror(APP_NAME, "FFmpeg was not found.\nInstall it with:  pip install imageio-ffmpeg")

    def _outline_btn(self, parent, text, width, height, command):
        return ctk.CTkButton(parent, text=text, width=width, height=height, command=command,
                             fg_color="transparent", border_width=1, border_color=BORDER,
                             hover_color=BORDER, text_color=TEXT, font=(FONT, 13))

    def _build(self):
        pad = 24
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=pad, pady=(22, 10))
        ctk.CTkLabel(head, text="MP4 to MP3", font=(FONT, 30, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(head, text="Pull the audio out of your videos. Files never leave this PC.",
                     font=(FONT, 13), text_color=MUTED).pack(anchor="w")

        # Drop zone and file list
        zone = ctk.CTkFrame(self, fg_color=CARD, border_color=BORDER, border_width=1, corner_radius=12)
        zone.pack(fill="both", expand=True, padx=pad, pady=6)
        bar = ctk.CTkFrame(zone, fg_color="transparent")
        bar.pack(fill="x", padx=14, pady=(12, 4))
        self.add_btn = ctk.CTkButton(bar, text="Add videos", width=110, height=34, command=self.pick_files,
                                     fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=DARK,
                                     font=(FONT, 13, "bold"))
        self.add_btn.pack(side="left")
        self.clear_btn = self._outline_btn(bar, "Clear list", 92, 34, self.clear)
        self.clear_btn.pack(side="left", padx=8)
        self.count = ctk.CTkLabel(bar, text="", text_color=MUTED, font=(FONT, 12))
        self.count.pack(side="right")
        self.list = ctk.CTkScrollableFrame(zone, fg_color="transparent")
        self.list.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        self.empty = ctk.CTkLabel(self.list, text_color=MUTED, font=(FONT, 14), justify="center",
                                  text="Drop videos here, or choose Add videos.\nMP4, MOV, MKV, AVI and WebM all work.")
        self.empty.pack(pady=70)

        # Settings
        opts = ctk.CTkFrame(self, fg_color=CARD, border_color=BORDER, border_width=1, corner_radius=12)
        opts.pack(fill="x", padx=pad, pady=6)
        opts.grid_columnconfigure(1, weight=1)
        seg = dict(fg_color=BG, selected_color=ACCENT_DEEP, selected_hover_color=ACCENT_DEEP,
                   unselected_color="#32343a", unselected_hover_color="#3d4047",
                   text_color=TEXT, font=(FONT, 13))
        ctk.CTkLabel(opts, text="Save to", font=(FONT, 13), text_color=TEXT).grid(
            row=0, column=0, sticky="w", padx=(16, 16), pady=(14, 6))
        self.dest_mode = ctk.CTkSegmentedButton(opts, values=["Next to the video", "Choose folder"],
                                                command=self._dest_changed, **seg)
        self.dest_mode.set("Next to the video")
        self.dest_mode.grid(row=0, column=1, sticky="w", pady=(14, 6))
        self.folder_row = ctk.CTkFrame(opts, fg_color="transparent")
        self.folder_row.grid(row=1, column=1, sticky="ew", pady=2, padx=(0, 16))
        self.folder_lbl = ctk.CTkLabel(self.folder_row, text="No folder chosen", text_color=MUTED, anchor="w")
        self.folder_lbl.pack(side="left", fill="x", expand=True)
        self._outline_btn(self.folder_row, "Browse", 84, 30, self.pick_folder).pack(side="right")
        self.folder_row.grid_remove()
        ctk.CTkLabel(opts, text="Quality", font=(FONT, 13), text_color=TEXT).grid(
            row=2, column=0, sticky="w", padx=(16, 16), pady=(6, 14))
        self.quality = ctk.CTkSegmentedButton(opts, values=QUALITIES, **seg)
        self.quality.set("192 kbps")
        self.quality.grid(row=2, column=1, sticky="w", pady=(6, 14))

        # Footer
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=pad, pady=(6, 20))
        self.progress = ctk.CTkProgressBar(foot, height=8, progress_color=ACCENT, fg_color=BORDER)
        self.progress.set(0)
        self.progress.pack(fill="x", pady=(4, 10))
        row = ctk.CTkFrame(foot, fg_color="transparent")
        row.pack(fill="x")
        self.status = ctk.CTkLabel(row, text="Add some videos to get started.", text_color=MUTED,
                                   anchor="w", font=(FONT, 13))
        self.status.pack(side="left", fill="x", expand=True)
        self.convert_btn = ctk.CTkButton(row, text="Convert to MP3", width=160, height=40, command=self.toggle,
                                         fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=DARK,
                                         font=(FONT, 14, "bold"))
        self.convert_btn.pack(side="right")
        self.open_btn = self._outline_btn(row, "Open folder", 110, 40, self.open_folder)

    # ----- file list -----
    def pick_files(self):
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        paths = filedialog.askopenfilenames(title="Choose videos",
                                            filetypes=[("Video files", exts), ("All files", "*.*")])
        self.add_files(paths)

    def add_files(self, paths):
        if self.busy:
            return
        known = {i["path"] for i in self.items}
        for p in map(Path, paths):
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS and p not in known:
                self._add_row(p)
                known.add(p)
        self._refresh()

    def _add_row(self, path):
        row = ctk.CTkFrame(self.list, fg_color=ROW, corner_radius=8)
        row.pack(fill="x", pady=3, padx=2)
        item = {"path": path, "row": row, "state": "waiting"}
        ctk.CTkButton(row, text="×", width=28, height=28, fg_color="transparent", hover_color=BORDER,
                      text_color=MUTED, font=(FONT, 16), command=lambda: self.remove(item)
                      ).pack(side="right", padx=(0, 6))
        item["status"] = ctk.CTkLabel(row, text=f"{path.stat().st_size / 1e6:.1f} MB", width=110,
                                      anchor="e", text_color=MUTED, font=(FONT, 12))
        item["status"].pack(side="right", padx=4)
        ctk.CTkLabel(row, text=shorten(path.name), anchor="w", text_color=TEXT, font=(FONT, 13)
                     ).pack(side="left", fill="x", expand=True, padx=(12, 8), pady=9)
        self.items.append(item)

    def remove(self, item):
        if self.busy:
            return
        item["row"].destroy()
        self.items.remove(item)
        self._refresh()

    def clear(self):
        if self.busy:
            return
        for item in self.items:
            item["row"].destroy()
        self.items.clear()
        self._refresh()

    def _refresh(self):
        n = len(self.items)
        self.count.configure(text=f"{n} video{'s' if n != 1 else ''}" if n else "")
        if n:
            self.empty.pack_forget()
            self.status.configure(text="Ready to convert.", text_color=MUTED)
        else:
            self.empty.pack(pady=70)
            self.status.configure(text="Add some videos to get started.", text_color=MUTED)

    def _set_status(self, item, text, color=MUTED):
        item["status"].configure(text=text, text_color=color)

    # ----- settings -----
    def _dest_changed(self, value):
        if value == "Choose folder":
            self.folder_row.grid()
            if not self.out_dir:
                self.pick_folder()
        else:
            self.folder_row.grid_remove()

    def pick_folder(self):
        folder = filedialog.askdirectory(title="Choose where to save the MP3 files")
        if folder:
            self.out_dir = Path(folder)
            self.folder_lbl.configure(text=shorten(str(self.out_dir), 60), text_color=TEXT)

    # ----- conversion -----
    def toggle(self):
        if self.busy:
            self.worker.cancel()
            self.status.configure(text="Cancelling…", text_color=MUTED)
        else:
            self.start()

    def start(self):
        if not self.ffmpeg:
            messagebox.showerror(APP_NAME, "FFmpeg was not found, so conversion can't run.")
            return
        todo = [i for i in self.items if i["state"] != "done"]
        if not todo:
            messagebox.showinfo(APP_NAME, "Add at least one video to convert.")
            return
        custom = self.dest_mode.get() == "Choose folder"
        if custom and not self.out_dir:
            self.pick_folder()
            if not self.out_dir:
                return
        jobs, taken = [], set()
        for item in todo:
            folder = self.out_dir if custom else item["path"].parent
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror(APP_NAME, f"Can't use that folder:\n{exc}")
                return
            dst = unique_path(folder / f"{item['path'].stem}.mp3", taken)
            taken.add(dst)
            jobs.append((item["path"], dst))
        for item in todo:
            item["state"] = "queued"
            self._set_status(item, "Waiting")
        self.run_items, self.failures, self.finished_count = todo, [], 0
        self.last_dir = jobs[0][1].parent
        self.progress.set(0)
        self.open_btn.pack_forget()
        self._lock(True)
        bitrate = self.quality.get().split()[0] + "k"
        self.worker = Converter(self.ffmpeg, jobs, bitrate, self.events)
        self.worker.start()

    def _lock(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for w in (self.add_btn, self.clear_btn, self.dest_mode, self.quality):
            w.configure(state=state)
        if busy:
            self.convert_btn.configure(text="Cancel", fg_color="#3a3d44", hover_color="#4a4d55", text_color=TEXT)
        else:
            self.convert_btn.configure(text="Convert to MP3", fg_color=ACCENT,
                                       hover_color=ACCENT_HOVER, text_color=DARK)

    def _poll(self):
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _handle(self, ev):
        kind = ev[0]
        if kind == "finished":
            return self._finish(ev[1])
        item, total = self.run_items[ev[1]], len(self.run_items)
        if kind == "start":
            item["state"] = "running"
            self._set_status(item, "Converting 0%", ACCENT)
            self.status.configure(text=f"Converting {ev[1] + 1} of {total}: {shorten(item['path'].name, 40)}",
                                  text_color=TEXT)
        elif kind == "progress":
            self._set_status(item, f"Converting {ev[2] * 100:.0f}%", ACCENT)
            self.progress.set((self.finished_count + ev[2]) / total)
        elif kind == "done":
            item["state"] = "done"
            self.finished_count += 1
            self._set_status(item, "Done", OK)
            self.progress.set(self.finished_count / total)
        elif kind == "error":
            msg = ev[2]
            if "matches no streams" in msg:
                msg = "This video has no audio track."
            item["state"] = "failed"
            self.finished_count += 1
            self.failures.append(f"{item['path'].name}: {msg}")
            self._set_status(item, "Failed", BAD)

    def _finish(self, cancelled):
        self._lock(False)
        ok = sum(1 for i in self.run_items if i["state"] == "done")
        for item in self.run_items:
            if item["state"] in ("queued", "running"):
                item["state"] = "waiting"
                self._set_status(item, "Cancelled")
        if cancelled:
            self.status.configure(text=f"Cancelled. {ok} converted.", text_color=MUTED)
        else:
            self.progress.set(1)
            text = f"Finished. {ok} converted" + (f", {len(self.failures)} failed." if self.failures else ".")
            self.status.configure(text=text, text_color=BAD if self.failures else OK)
        if ok:
            self.open_btn.pack(side="right", padx=8)
        if self.failures:
            messagebox.showwarning(APP_NAME, "Some files could not be converted:\n\n" + "\n".join(self.failures))

    def open_folder(self):
        if self.last_dir and sys.platform == "win32":
            os.startfile(self.last_dir)


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    App().mainloop()


if __name__ == "__main__":
    main()

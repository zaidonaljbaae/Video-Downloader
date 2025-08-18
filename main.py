import ttkbootstrap as tb
from ttkbootstrap.constants import *
import tkinter as tk
from tkinter import filedialog, messagebox
import yt_dlp
import threading
import subprocess
import platform
from pathlib import Path
import shutil
import sys, os

# ======================== FFmpeg Detection (Robust) ========================

def resource_path(*parts):
    base = getattr(
        sys, '_MEIPASS',
        os.path.abspath(os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else __file__))
    )
    return os.path.join(base, *parts)

USER_FFMPEG_BIN = resource_path("ffmpeg", "bin")

def windows_exe(name: str) -> str:
    return name + (".exe" if platform.system() == "Windows" else "")


def ensure_ffmpeg_paths(bin_dir: str | None):
    """Returns (ffmpeg_exe_path, ffprobe_exe_path, ffmpeg_location_for_yt_dlp) or (None, None, None)."""
    def have_exes(d):
        if not d:
            return False
        f = os.path.join(d, windows_exe("ffmpeg"))
        p = os.path.join(d, windows_exe("ffprobe"))
        return os.path.isfile(f) and os.path.isfile(p)

    # 1) User provided bin dir
    if have_exes(bin_dir):
        ffmpeg_path = os.path.join(bin_dir, windows_exe("ffmpeg"))
        ffprobe_path = os.path.join(bin_dir, windows_exe("ffprobe"))
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
        if platform.system() == "Windows":
            try:
                os.add_dll_directory(bin_dir)  # Python 3.8+
            except Exception:
                pass
        return ffmpeg_path, ffprobe_path, ffmpeg_path

    # 2) PATH
    which_ffmpeg = shutil.which("ffmpeg")
    which_ffprobe = shutil.which("ffprobe")
    if which_ffmpeg and which_ffprobe and os.path.isfile(which_ffmpeg) and os.path.isfile(which_ffprobe):
        if platform.system() == "Windows":
            try:
                os.add_dll_directory(os.path.dirname(which_ffmpeg))
            except Exception:
                pass
        return which_ffmpeg, which_ffprobe, which_ffmpeg

    # 3) Not found
    return None, None, None

FFMPEG_EXE, FFPROBE_EXE, FFMPEG_LOC_FOR_YTDLP = ensure_ffmpeg_paths(USER_FFMPEG_BIN)

# ======================== Globals & Utils ========================

videos_list = []  # {url, title, res_label, size_bytes, status, filepath, iid}
current_url = [None]
cancel_event = threading.Event()

def get_default_download_folder():
    if platform.system() == "Windows":
        import ctypes.wintypes
        CSIDL_PERSONAL = 0x0005
        SHGFP_TYPE_CURRENT = 0
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf)
        documents = buf.value
        downloads = os.path.join(os.path.dirname(documents), "Downloads")
        if os.path.exists(downloads):
            return downloads
        return documents
    else:
        return str(Path.home() / "Downloads")

default_folder = get_default_download_folder()

def sizeof_fmt(num, suffix='B'):
    if num is None:
        return "?"
    num = float(num)
    for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"

def speed_fmt(bytes_per_sec):
    if not bytes_per_sec:
        return ""
    return f"{sizeof_fmt(float(bytes_per_sec))}/s"

def get_tag_by_status(status):
    s = (status or "").lower()
    if "play" in s:
        return "play"
    if "done" in s:
        return "done"
    if any(k in s for k in ("downloading", "analyzing", "waiting", "starting", "ready", "processing")):
        return "orange"
    if any(k in s for k in ("error", "cancel")):
        return "error"
    return "default"

def update_status_in_table(url, *, status=None, resolution_with_size=None, filepath=None):
    for video in videos_list:
        if video["url"] == url:
            if status is not None:
                video["status"] = status
            if resolution_with_size is not None:
                video["res_label"] = resolution_with_size
            if filepath is not None:
                video["filepath"] = filepath
            tree.item(
                video["iid"],
                values=("🗑", video["title"], video["res_label"], video["status"]),
                tags=(get_tag_by_status(video["status"]),)
            )
            break

# ===== Friendly explanations for common download errors =====

def friendly_error_message(exc: Exception) -> str:
    e = str(exc) if exc else ""
    e_low = e.lower()

    if "requested range not satisfiable" in e_low or "http error 416" in e_low:
        return ("The server refused the requested byte range (HTTP 416).\n"
                "Tips: delete any .part files and try again; disable resume; avoid proxies/VPN; "
                "set 'http_chunk_size': 0 in yt-dlp to let the server control chunking.")
    if "http error 403" in e_low:
        return ("Access forbidden (HTTP 403). The server blocked the request.\n"
                "Tips: try without VPN/proxy, update yt-dlp, or try again later.")
    if "http error 404" in e_low:
        return ("Content not found (HTTP 404). The media may have been removed or moved.")
    if "ssl" in e_low:
        return ("SSL/Certificate problem. Check your date/time, network, and try again.\n"
                "A corporate proxy can also cause this.")
    if "timed out" in e_low or "timeout" in e_low:
        return ("Network timeout. Check your internet and try again.")
    if "unsupported url" in e_low:
        return ("This URL is not supported by yt-dlp. Verify the link.")
    if "captcha" in e_low or "consent" in e_low:
        return ("The site is asking for human verification. Open the URL in a browser first.")
    if "ffmpeg" in e_low or "ffprobe" in e_low:
        return ("FFmpeg/ffprobe problem. Make sure both executables are available.")
    # default
    return ("An unexpected error occurred. Try again, and if it persists, update yt-dlp.\n"
            "Details:\n" + e)

# ======================== Size Estimation Helpers ========================

def estimate_size_bytes_from_bitrate(fmt: dict, duration: float | None) -> int | None:
    """
    Estimate size using bitrate fields if filesize is missing.
    yt-dlp 'tbr', 'vbr', 'abr' are in Kbits/s (usually). Use duration (s) to estimate.
    """
    if not duration:
        return None
    # Prefer 'tbr' (total bitrate); else vbr/abr depending on stream kind
    tbr = fmt.get('tbr')  # Kbits/s
    # If this is clearly audio-only, use abr; if video-only, use vbr when present
    if tbr is None:
        if fmt.get('acodec') not in (None, 'none'):
            tbr = fmt.get('abr')
        if tbr is None and fmt.get('vcodec') not in (None, 'none'):
            tbr = fmt.get('vbr')
    if not tbr:
        return None
    # Convert Kbits/s -> bytes: kbps * 1000 / 8 * seconds
    try:
        return int(float(tbr) * 1000.0 / 8.0 * float(duration))
    except Exception:
        return None

def resolve_stream_size(fmt: dict, duration: float | None) -> int | None:
    """Return best known or estimated size for a single format."""
    size = fmt.get('filesize') or fmt.get('filesize_approx')
    if size:
        return int(size)
    return estimate_size_bytes_from_bitrate(fmt, duration)

def choose_best_audio(formats: list[dict]) -> dict | None:
    """Pick an audio stream likely to be best for muxing (largest size or highest abr)."""
    best = None
    for f in formats:
        if f.get('acodec') not in (None, 'none'):
            # Prefer known size; fallback to abr
            size = f.get('filesize') or f.get('filesize_approx')
            abr = f.get('abr') or 0
            key = (1 if size else 0, int(size or 0), float(abr))
            if best is None or key > best[0]:
                best = (key, f)
    return best[1] if best else None

# ======================== yt-dlp Info Fetching ========================

def fetch_video_info(url):
    """
    Returns:
      title (str),
      options (list of dict): [{'label': '1080p — ~245.3 MiB', 'res': '1080p', 'size_bytes': 257123456}, ...]
      Includes 'Highest (best available)' as first option with a size estimate when possible.
    """
    try:
        ydl_opts = {'quiet': True, 'noplaylist': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get("formats", [])
            title = info.get("title", "Unknown Title")
            duration = info.get("duration")  # seconds (may be None)

            best_audio_fmt = choose_best_audio(formats)

            # collect unique heights
            heights = sorted({f.get("height") for f in formats if f.get("height")}, key=int)
            options = []

            for h in heights:
                # best video for this height (prefer known size; else higher tbr)
                best_video_fmt = None
                best_score = (-1, -1.0)  # (has_size, tbr)
                for f in formats:
                    if f.get("height") == h and f.get('vcodec') not in (None, 'none'):
                        has_size = 1 if (f.get('filesize') or f.get('filesize_approx')) else 0
                        tbr = float(f.get('tbr') or f.get('vbr') or 0.0)
                        score = (has_size, tbr)
                        if best_video_fmt is None or score > best_score:
                            best_video_fmt = f
                            best_score = score
                if not best_video_fmt:
                    continue

                v_size = resolve_stream_size(best_video_fmt, duration) or 0
                a_size = resolve_stream_size(best_audio_fmt, duration) if best_audio_fmt else 0
                total_size = (v_size or 0) + (a_size or 0)
                label = f"{h}p — {sizeof_fmt(total_size) if total_size else '?'}"
                options.append({'label': label, 'res': f"{h}p", 'size_bytes': total_size or None})

            options_sorted = sorted(options, key=lambda x: int(x['res'].replace('p', '')))
            # Build "Highest" with estimated size from the max height option if any
            highest_entry = None
            if options_sorted:
                highest_entry = {
                    'label': f"Highest (best available) — ~{options_sorted[-1]['label'].split('~')[-1]}",
                    'res': 'Highest',
                    'size_bytes': options_sorted[-1]['size_bytes']
                }

            if options_sorted:
                return title, ([highest_entry] if highest_entry else [{'label': 'Highest (best available)', 'res': 'Highest', 'size_bytes': None}]) + options_sorted
            # If nothing matched, still return title and empty options
            return title, []
    except Exception:
        return None, []

# ======================== Busy/Waiting UI (LEFT) ========================

def set_busy(is_busy: bool, message: str = ""):
    """Enable/disable controls and show a LEFT-aligned status bar while working."""
    def _apply():
        # Cursor
        root.configure(cursor="watch" if is_busy else "")
        # Status bar (left-aligned)
        if is_busy:
            busy_msg_var.set(message or "Working...")
            if not busy_bar.winfo_ismapped():
                busy_bar.pack(side=tk.LEFT, padx=(0, 8))
            if not busy_lbl.winfo_ismapped():
                busy_lbl.pack(side=tk.LEFT)
            busy_bar.start(12)
        else:
            busy_bar.stop()
            if busy_bar.winfo_ismapped():
                busy_bar.pack_forget()
            if busy_lbl.winfo_ismapped():
                busy_lbl.pack_forget()
            busy_msg_var.set("")
        # Controls state
        state = tk.DISABLED if is_busy else tk.NORMAL
        url_entry.configure(state=state)
        fetch_btn.configure(state=state)
        resolution_combo.configure(state=state)
        add_btn.configure(state=state)
        browse_btn.configure(state=state)
        download_btn.configure(state=state)
        cancel_btn.configure(state=state)
        open_folder_btn.configure(state=state)
        clear_all_btn.configure(state=state)

    root.after(0, _apply)

# ======================== Actions ========================

def threaded_fetch_resolutions():
    """Fetches title and resolution options ONLY. Does NOT add to the table."""
    url = url_var.get().strip()
    if not url:
        messagebox.showerror("Error", "Please enter a video URL.")
        return

    resolution_combo.set("")
    resolution_combo["values"] = []
    title_var.set("Fetching info...")

    set_busy(True, "Fetching resolutions...")
    def task():
        try:
            title, options = fetch_video_info(url)
            if title and options:
                values = [opt['label'] for opt in options]
                def _done():
                    resolution_combo["values"] = values
                    resolution_combo.current(0)
                    title_var.set(f"Title: {title}")
                root.after(0, _done)
            else:
                def _err():
                    title_var.set("Title: —")
                    messagebox.showerror("Error", "Could not fetch resolutions for this URL.")
                root.after(0, _err)
        finally:
            set_busy(False)
    threading.Thread(target=task, daemon=True).start()

def add_video():
    url = url_var.get().strip()
    if not url:
        messagebox.showerror("Error", "Please enter a video URL.")
        return

    if any(v["url"] == url for v in videos_list):
        messagebox.showwarning("Duplicate", "This video is already in the list.")
        return

    selected_label = resolution_var.get().strip()
    if not selected_label:
        messagebox.showwarning("No resolution", "Please fetch resolutions and select one.")
        return

    set_busy(True, "Adding video...")
    def task():
        try:
            title, options = fetch_video_info(url)
            if not options:
                root.after(0, lambda: messagebox.showerror("Error", "No resolutions found for this video."))
                return

            # pick matching option
            selected = None
            for o in options:
                if o['label'] == selected_label:
                    selected = o
                    break
            if selected is None:
                selected = options[0]

            res_label = selected['label']
            filename_guess = f"{title}.mp4"
            filepath_guess = os.path.join(save_path_var.get(), filename_guess)
            status = "Play" if os.path.exists(filepath_guess) else "Ready"

            def _append():
                iid = tree.insert("", tk.END, values=("🗑", title, res_label, status),
                                  tags=(get_tag_by_status(status),))
                videos_list.append({
                    "url": url,
                    "title": title,
                    "res_label": res_label,
                    "size_bytes": selected['size_bytes'],
                    "status": status,
                    "filepath": filepath_guess if os.path.exists(filepath_guess) else None,
                    "iid": iid
                })
                # Clear inputs but keep the fetched title visible until next fetch
                url_var.set("")
                resolution_var.set("")
                resolution_combo["values"] = []

            root.after(0, _append)
        finally:
            set_busy(False)
    threading.Thread(target=task, daemon=True).start()

def browse_folder():
    folder = filedialog.askdirectory()
    if folder:
        save_path_var.set(folder)

def open_file(filepath):
    if not filepath or not os.path.exists(filepath):
        messagebox.showerror("Error", "File does not exist.")
        return
    if platform.system() == "Windows":
        os.startfile(filepath)
    elif platform.system() == "Darwin":
        subprocess.call(("open", filepath))
    else:
        subprocess.call(("xdg-open", filepath))

def on_tree_double_click(event):
    item_id = tree.identify_row(event.y)
    if not item_id:
        return
    col = tree.identify_column(event.x)
    if col == "#4":  # Status -> Play
        for v in videos_list:
            if v["iid"] == item_id and v["status"].lower().startswith("play"):
                open_file(v["filepath"])
                break

def on_tree_click(event):
    item_id = tree.identify_row(event.y)
    if not item_id:
        return
    if tree.identify("region", event.x, event.y) != "cell":
        return
    col = tree.identify_column(event.x)
    if col == "#1":  # delete icon
        for v in list(videos_list):
            if v["iid"] == item_id:
                delete_video(v["url"])
                break

def delete_video(url):
    global videos_list
    for video in list(videos_list):
        if video["url"] == url:
            try:
                tree.delete(video["iid"])
            except Exception:
                pass
            videos_list.remove(video)
            break

def delete_all_videos():
    global videos_list
    if messagebox.askyesno("Confirm", "Are you sure you want to delete all videos?"):
        for video in videos_list:
            try:
                tree.delete(video["iid"])
            except Exception:
                pass
        videos_list = []

# ======================== Downloading ========================

def download_video():
    if not videos_list:
        messagebox.showerror("Error", "No videos to download.")
        return
    if not save_path_var.get():
        messagebox.showerror("Error", "Please select a save folder.")
        return

    cancel_event.clear()

    # Friendly warning if ffmpeg is missing
    if FFMPEG_LOC_FOR_YTDLP is None:
        messagebox.showwarning(
            "FFmpeg not found",
            "ffmpeg/ffprobe were not found.\n"
            "Place them in:\n"
            f"{USER_FFMPEG_BIN}\n"
            "or install system-wide and restart the app."
        )

    set_busy(True, "Downloading...")
    def task():
        try:
            for video in videos_list:
                if cancel_event.is_set():
                    break

                url = video["url"]
                title = video["title"]
                iid = video["iid"]
                label = video["res_label"]

                if label.startswith("Highest"):
                    fmt_str = "bestvideo+bestaudio/best"
                    height_desc = "best"
                else:
                    try:
                        height = int(label.split('p', 1)[0])
                        height_desc = f"{height}p"
                        fmt_str = f"bestvideo[height={height}]+bestaudio/best[height={height}]/best"
                    except Exception:
                        height_desc = "best"
                        fmt_str = "bestvideo+bestaudio/best"

                filename_guess = f"{title}.mp4"
                filepath_guess = os.path.join(save_path_var.get(), filename_guess)
                if os.path.exists(filepath_guess):
                    update_status_in_table(url, status="Play", filepath=filepath_guess)
                    continue

                update_status_in_table(url, status=f"Starting download ({height_desc})...")
                current_url[0] = url
                tree.see(iid)

                ydl_opts = {
                    'format': fmt_str,
                    'outtmpl': f'{save_path_var.get()}/%(title)s.%(ext)s',
                    'progress_hooks': [progress_hook],
                    'quiet': True,
                    'no_warnings': True,
                    'noprogress': True,            # <--- hide yt-dlp console progress
                    'ffmpeg_location': FFMPEG_LOC_FOR_YTDLP,
                    'noplaylist': True,
                    'ignoreerrors': True,
                    'http_chunk_size': 0,
                    'socket_timeout': 15,
                    'merge_output_format': 'mp4',
                    'continuedl': False,
                }

                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info_dict = ydl.extract_info(url, download=True)
                        ext = info_dict.get('ext', 'mp4')
                        filename = os.path.join(save_path_var.get(), f"{info_dict.get('title')}.{ext}")
                        if cancel_event.is_set():
                            update_status_in_table(url, status="Canceled")
                        else:
                            update_status_in_table(url, status="Play", filepath=filename)
                except Exception as e:
                    update_status_in_table(url, status="Error")
                    msg = friendly_error_message(e)
                    root.after(0, lambda m=msg, t=title, u=url: messagebox.showerror(
                        "Download failed",
                        f"Video: {t}\nURL: {u}\n\n{m}"
                    ))
        finally:
            set_busy(False)
            current_url[0] = None
    threading.Thread(target=task, daemon=True).start()

def cancel_downloading():
    cancel_event.set()

def progress_hook(d):
    url = current_url[0]
    if cancel_event.is_set():
        raise Exception("Canceled by user")
    if not url:
        return

    if d.get('status') == 'downloading':
        # Numeric values (more consistent than _*_str fields)
        downloaded = float(d.get('downloaded_bytes') or 0)
        total = float(d.get('total_bytes') or d.get('total_bytes_estimate') or 0)
        speed = d.get('speed')  # bytes/sec or None

        # Percent
        if total > 0:
            pct = downloaded / total * 100.0
            percent_str = f"{pct:.1f}%"
            total_str = sizeof_fmt(total)
        else:
            percent_str = (d.get('_percent_str') or '0.0%').strip()
            total_str = "?"

        # Sizes
        downloaded_str = sizeof_fmt(downloaded)

        # Speed (no padding)
        speed_str = speed_fmt(speed) if speed else "--/s"

        # Final: 98.9% (127.5MiB/128.9MiB)  --  5.16MiB/s
        status_text = f"{percent_str} ({downloaded_str}/{total_str})  --  {speed_str}"

        # Update table + status line + console
        update_status_in_table(url, status=status_text)
        busy_msg_var.set(status_text)

    elif d.get('status') == 'finished':
        update_status_in_table(url, status="Processing...")
        busy_msg_var.set("Processing...")

# ======================== GUI ========================

root = tb.Window(themename="superhero")
root.title("YouTube Video Downloader")
root.geometry("960x740")
root.minsize(900, 700)
root.resizable(True, True)

try:
    root.iconbitmap(resource_path("logo.ico"))
except Exception:
    # fallback: png/jpg كصورة نافذة لبعض البيئات (ليس أيقونة شريط المهام)
    try:
        import PIL.Image, PIL.ImageTk
        from PIL import ImageTk, Image
        img = Image.open(resource_path("logo.jpg"))
        tkicon = ImageTk.PhotoImage(img)
        root.iconphoto(True, tkicon)
    except Exception:
        pass
    
# Title
title_lbl = tb.Label(root, text="Welcome to YouTube Downloader", font=("Arial", 20, "bold"), foreground="white")
title_lbl.pack(pady=15)

# URL + Fetch
frame_url = tb.Frame(root)
frame_url.pack(fill="x", padx=20, pady=5)

url_var = tk.StringVar()
tb.Label(frame_url, text="Video URL:", font=("Arial", 12), foreground="white").pack(side=tk.LEFT)
url_entry = tb.Entry(frame_url, textvariable=url_var, width=64)
url_entry.pack(side=tk.LEFT, padx=10)
fetch_btn = tb.Button(frame_url, text="Fetch Resolutions", bootstyle=INFO, command=threaded_fetch_resolutions)
fetch_btn.pack(side=tk.LEFT)

# Title (from fetched info)
frame_title = tb.Frame(root)
frame_title.pack(fill="x", padx=20, pady=(0, 5))
title_var = tk.StringVar(value="Title: —")
tb.Label(frame_title, textvariable=title_var, font=("Arial", 11), foreground="white").pack(side=tk.LEFT)

# Resolution + Add
frame_res = tb.Frame(root)
frame_res.pack(fill="x", padx=20, pady=5)

tb.Label(frame_res, text="Select Resolution (with size):", font=("Arial", 12), foreground="white").pack(side=tk.LEFT)
resolution_var = tk.StringVar()
resolution_combo = tb.Combobox(frame_res, textvariable=resolution_var, state="readonly", width=40)
resolution_combo.pack(side=tk.LEFT, padx=10)
resolution_combo.set("")

add_btn = tb.Button(frame_res, text="Add Video", bootstyle=SUCCESS, command=add_video)
add_btn.pack(side=tk.LEFT, padx=5)

# Results Tree
frame_results = tb.Frame(root)
frame_results.pack(fill="both", expand=True, padx=20, pady=10)

columns = ("Delete", "Name", "Resolution", "Status")
tree = tb.Treeview(frame_results, columns=columns, show="headings", selectmode="browse")
tree.pack(side=tk.LEFT, fill="both", expand=True)

scrollbar = tb.Scrollbar(frame_results, orient=tk.VERTICAL, command=tree.yview)
scrollbar.pack(side=tk.LEFT, fill="y")
tree.configure(yscrollcommand=scrollbar.set)

tree.heading("Delete", text="")
tree.heading("Name", text="Name", anchor="w")
tree.heading("Resolution", text="Resolution (with size)")
tree.heading("Status", text="Status")

tree.column("Delete", anchor=tk.CENTER, width=36)
tree.column("Name", anchor="w", width=420) 
tree.column("Resolution", anchor=tk.CENTER, width=220)
tree.column("Status", anchor=tk.CENTER, width=220)

tree.tag_configure("default", foreground="white", font=("Arial", 10, "bold"))
tree.tag_configure("done", foreground="#28a745", font=("Arial", 10, "bold"))
tree.tag_configure("play", foreground="#007bff", font=("Arial", 10, "bold"))
tree.tag_configure("orange", foreground="#fd7e14", font=("Arial", 10, "bold"))
tree.tag_configure("ready", foreground="#007bff", font=("Arial", 10, "bold"))
tree.tag_configure("error", foreground="#dc3545", font=("Arial", 10, "bold"))

tree.bind("<Double-1>", on_tree_double_click)
tree.bind("<Button-1>", on_tree_click)

# Clear All row
frame_controls = tb.Frame(root)
frame_controls.pack(fill="x", padx=20, pady=(0, 10))
clear_all_btn = tb.Button(frame_controls, text="Clear All", bootstyle=DANGER, command=delete_all_videos)
clear_all_btn.pack(side=tk.RIGHT)

# Save folder row
frame_bottom = tb.Frame(root)
frame_bottom.pack(fill="x", padx=20, pady=5)

save_path_var = tk.StringVar(value=default_folder)
tb.Label(frame_bottom, text="Save Folder:", font=("Arial", 12), foreground="white").pack(side=tk.LEFT)
save_entry = tb.Entry(frame_bottom, textvariable=save_path_var, width=50)
save_entry.pack(side=tk.LEFT, padx=10)
browse_btn = tb.Button(frame_bottom, text="Browse", bootstyle=SECONDARY, command=browse_folder)
browse_btn.pack(side=tk.LEFT, padx=5)

# Actions row (aligned RIGHT)
frame_actions = tb.Frame(root)
frame_actions.pack(fill="x", padx=20, pady=10)

# LEFT: status area (spinner + text)
status_left = tb.Frame(frame_actions)
status_left.pack(side=tk.LEFT, fill="x", expand=True)

busy_msg_var = tk.StringVar(value="Waiting...")
busy_bar = tb.Progressbar(status_left, mode="indeterminate", length=160, bootstyle=INFO)
busy_lbl = tb.Label(status_left, textvariable=busy_msg_var, font=("Arial", 10, "italic"), foreground="white")
# Initially hidden; set_busy() handles packing/unpacking

# RIGHT: action buttons
actions_right = tb.Frame(frame_actions)
actions_right.pack(side=tk.RIGHT)

download_btn = tb.Button(actions_right, text="Download All", bootstyle=PRIMARY, command=download_video)
download_btn.pack(side=tk.LEFT, padx=5)

cancel_btn = tb.Button(actions_right, text="Cancel Current", bootstyle=WARNING, command=cancel_downloading)
cancel_btn.pack(side=tk.LEFT, padx=5)

def open_folder():
    path = save_path_var.get()
    if not path or not os.path.exists(path):
        messagebox.showerror("Error", "Save folder does not exist.")
        return
    if platform.system() == "Windows":
        os.startfile(path)
    elif platform.system() == "Darwin":
        subprocess.call(["open", path])
    else:
        subprocess.call(["xdg-open", path])

open_folder_btn = tb.Button(actions_right, text="Open Folder", bootstyle=INFO, command=open_folder)
open_folder_btn.pack(side=tk.LEFT, padx=5)

console_frame = tb.Frame(root)
console_frame.pack(fill="both", padx=20, pady=(0, 10))

# Status bar (LEFT-aligned busy indicator)
status_bar = tb.Frame(root)
status_bar.pack(fill="x", padx=20, pady=(0, 8), side=tk.BOTTOM, anchor="w")
busy_msg_var = tk.StringVar(value="")
busy_bar = tb.Progressbar(status_bar, mode="indeterminate", length=160, bootstyle=INFO)
busy_lbl = tb.Label(status_bar, textvariable=busy_msg_var, font=("Arial", 10, "italic"), foreground="white")
# initially hidden
# (widgets are packed dynamically in set_busy)

# Footer
footer_lbl = tb.Label(root, text="Created by Zaidon", font=("Arial", 10, "italic"), foreground="gray")
footer_lbl.pack(side=tk.BOTTOM, pady=5)

root.mainloop()
